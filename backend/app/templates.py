"""Загрузка и разбор шаблонов: POST /api/templates, GET /api/templates/{id}."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.auth import Db, OptionalUser
from prism.config import BACKEND_DIR
from prism.parsing.spec import SPECS_DIR, TemplateSpec, parse_template_sync

router = APIRouter(prefix="/api/templates", tags=["templates"])

UPLOADS = BACKEND_DIR / "data" / "uploads"
MAX_BYTES = 150 * 1024 * 1024
SUPPORTED = {".pptx", ".potx"}          # шаблон — только PowerPoint: из PDF/HTML не восстановить макеты и стили


class TemplateSummary(BaseModel):
    id: str
    name: str
    format: str
    patterns: int
    kinds: dict[str, int]
    palette: list[str]
    colors: dict[str, str]              # background / text / accent — для миниатюры в интерфейсе
    fonts: list[str]
    service_slides: int
    assets: int


def _potx_to_pptx(data: bytes) -> bytes:
    """.potx отличается от .pptx только типом главной части в [Content_Types].xml."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            body = src.read(item.filename)
            if item.filename == "[Content_Types].xml":
                body = body.replace(b"presentationml.template.main+xml", b"presentationml.presentation.main+xml")
            dst.writestr(item, body)
    return out.getvalue()


def _summary(spec: TemplateSpec, name: str, fmt: str) -> TemplateSummary:
    t = spec.tokens
    palette = [c.hex for c in t.palette if c.usage > 0][:6]
    fonts = list(dict.fromkeys(f for f in (t.fonts.heading, t.fonts.body) if f))
    return TemplateSummary(
        id=spec.sha256[:16], name=name, format=fmt, patterns=len(spec.patterns),
        kinds=dict(Counter(p.kind.value for p in spec.patterns).most_common()),
        palette=palette, fonts=fonts,
        colors={k: c for k, c in (("background", t.color("background")), ("text", t.color("text")),
                                  ("accent", t.accent())) if c}, service_slides=len(spec.service_slides), assets=len(spec.assets),
    )


def _meta_path(template_id: str) -> Path:
    return SPECS_DIR / template_id / "upload.json"


@router.post("", response_model=TemplateSummary)
async def upload_template(file: UploadFile, db: Db, user: OptionalUser) -> TemplateSummary:
    name = Path(file.filename or "template").name
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED:
        raise HTTPException(415, f"Формат {ext or 'без расширения'} не поддерживается: нужен .pptx или .potx")

    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Файл больше 150 МБ")
    if ext == ".potx":
        data = _potx_to_pptx(data)

    UPLOADS.mkdir(parents=True, exist_ok=True)
    path = UPLOADS / f"{hashlib.sha256(data).hexdigest()[:16]}.pptx"
    path.write_bytes(data)
    try:
        spec = await run_in_threadpool(parse_template_sync, path)
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        raise HTTPException(422, f"Не удалось разобрать шаблон: {e}") from e

    template_id = spec.sha256[:16]
    _meta_path(template_id).write_text(json.dumps({"name": name, "format": ext[1:]}), encoding="utf-8")
    if user is not None:  # гость тоже может работать, но в историю шаблон попадает только у аккаунта
        db.execute("INSERT OR REPLACE INTO user_templates (user_id, template_id, name, format) VALUES (?, ?, ?, ?)",
                   (user.id, template_id, name, ext[1:]))
    return _summary(spec, name, ext[1:])


@router.get("", response_model=list[TemplateSummary])
async def my_templates(db: Db, user: OptionalUser) -> list[TemplateSummary]:
    """Шаблоны, которые пользователь загружал раньше (гостю — пусто)."""
    if user is None:
        return []
    rows = db.execute("SELECT * FROM user_templates WHERE user_id = ? ORDER BY created_at DESC", (user.id,))
    out = []
    for row in rows:
        try:
            out.append(_summary(load_spec(row["template_id"]), row["name"], row["format"]))
        except HTTPException:
            continue  # кеш разбора удалён — шаблон нужно загрузить заново
    return out


def load_spec(template_id: str) -> TemplateSpec:
    if not template_id.isalnum():
        raise HTTPException(404, "Шаблон не найден")
    path = SPECS_DIR / template_id / "spec.json"
    if not path.exists():
        raise HTTPException(404, "Шаблон не найден")
    return TemplateSpec.model_validate_json(path.read_text(encoding="utf-8"))


@router.get("/{template_id}", response_model=TemplateSummary)
async def get_template(template_id: str) -> TemplateSummary:
    spec = load_spec(template_id)
    meta_path = _meta_path(template_id)
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return _summary(spec, meta.get("name", spec.source), meta.get("format", "pptx"))


@router.get("/{template_id}/spec", response_model=TemplateSpec)
async def get_template_spec(template_id: str) -> TemplateSpec:
    return load_spec(template_id)
