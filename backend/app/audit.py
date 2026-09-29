"""Панель аудита: находки по варианту колоды, смысловая проверка моделью и исправления на выбор.

    GET  /api/decks/{id}/audit/{template}/{variant}              находки (детерминированные — уже есть после генерации)
    POST /api/decks/{id}/audit/{template}/{variant}/contextual   смысловая проверка моделью (картинка + текст слайда)
    POST /api/decks/{id}/audit/{template}/{variant}/fix          {"fixes": [{"issue", "fix"}]} -> правка, новая ревизия

Правки и смысловая проверка одного варианта не идут одновременно (замок на вариант).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.decks import DECKS, JOBS
from prism.audit import service
from prism.llm.structured import StructuredOutputError

router = APIRouter(prefix="/api/decks", tags=["audit"])

_LOCKS: dict[tuple[str, str, int], asyncio.Lock] = {}


class FixChoice(BaseModel):
    issue: str
    fix: str


class FixRequest(BaseModel):
    fixes: list[FixChoice] = Field(min_length=1, max_length=200)


def _vdir(deck_id: str, template_id: str, variant: int) -> Path:
    if not (deck_id.isalnum() and template_id.isalnum() and 1 <= variant <= 9):
        raise HTTPException(404, "Колода не найдена")
    vdir = DECKS / deck_id / template_id / f"v{variant}"
    if not (vdir / "deck.pptx").exists():
        raise HTTPException(404, "Вариант колоды не найден")
    return vdir


def _lock(deck_id: str, template_id: str, variant: int) -> asyncio.Lock:
    return _LOCKS.setdefault((deck_id, template_id, variant), asyncio.Lock())


def _out(deck_id: str, vdir: Path, state: service.AuditState, extra: dict | None = None) -> dict:
    base = f"/api/decks/{deck_id}/files/{vdir.relative_to(DECKS / deck_id).as_posix()}"
    r = f"?r={state.rev}" if state.rev else ""
    errors = sum(1 for i in state.issues if i.severity == "error")
    return {
        "rev": state.rev,
        "issues": [i.model_dump() for i in state.issues],
        "contextual": state.contextual,
        "contextual_errors": state.contextual_errors,
        "log": state.log[-50:],
        "summary": {"errors": errors, "warnings": len(state.issues) - errors},
        "slides": [f"{base}/preview/{p.name}{r}" for p in sorted((vdir / "preview").glob("*.png"))],
        "pptx": f"{base}/deck.pptx{r}",
        "pdf": f"{base}/deck.pdf{r}" if (vdir / "deck.pdf").exists() else None,
        "html": f"{base}/deck.html{r}" if (vdir / "deck.html").exists() else None,
        **(extra or {}),
    }


def _refresh_result(deck_id: str, template_id: str, variant: int, out: dict) -> None:
    """После правки у варианта новые превью и сводка — обновляем сохранённый результат колоды."""
    path = DECKS / deck_id / "result.json"
    result = JOBS[deck_id].result if deck_id in JOBS and JOBS[deck_id].result else (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else None)
    if not result or "decks" not in result:
        return
    for d in result["decks"]:
        if d.get("template_id") != template_id:
            continue
        for v in d["variants"]:
            if v.get("variant") == variant:
                v.update({"slides": out["slides"], "pptx": out["pptx"], "pdf": out["pdf"], "html": out["html"],
                          "audit": out["summary"]})
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


def _llm(request: Request):
    llm = request.app.state.llm
    if getattr(llm, "fake", False):
        raise HTTPException(409, "В демо-режиме без модели смысловая проверка и правки моделью недоступны")
    return llm, request.app.state.skills


@router.get("/{deck_id}/audit/{template_id}/{variant}")
async def get_audit(deck_id: str, template_id: str, variant: int) -> dict:
    vdir = _vdir(deck_id, template_id, variant)
    state = service.load_state(vdir)
    if not (vdir / "audit.json").exists():             # колода до появления аудита — проверяем сейчас
        context = service.load_context(DECKS / deck_id)
        state = await asyncio.to_thread(service.run_deterministic, vdir, context, template_id)
    return _out(deck_id, vdir, state)


@router.post("/{deck_id}/audit/{template_id}/{variant}/contextual")
async def run_contextual(deck_id: str, template_id: str, variant: int, request: Request) -> dict:
    vdir = _vdir(deck_id, template_id, variant)
    llm, skills = _llm(request)
    lock = _lock(deck_id, template_id, variant)
    if lock.locked():
        raise HTTPException(409, "По этому варианту уже идёт проверка или правка")
    async with lock:
        try:
            state = await service.run_contextual(vdir, service.load_context(DECKS / deck_id), llm, skills)
        except (RuntimeError, StructuredOutputError) as e:
            raise HTTPException(502, f"Смысловая проверка не удалась: {e}") from e
    return _out(deck_id, vdir, state)


@router.post("/{deck_id}/audit/{template_id}/{variant}/fix")
async def fix(deck_id: str, template_id: str, variant: int, body: FixRequest, request: Request) -> dict:
    vdir = _vdir(deck_id, template_id, variant)
    needs_model = any(c.fix in ("shorten", "rewrite") for c in body.fixes)
    llm, skills = _llm(request) if needs_model else (request.app.state.llm, request.app.state.skills)
    lock = _lock(deck_id, template_id, variant)
    if lock.locked():
        raise HTTPException(409, "По этому варианту уже идёт проверка или правка")
    context = service.load_context(DECKS / deck_id)
    async with lock:
        try:
            state, report = await service.fix(vdir, context, template_id,
                                              [(c.issue, c.fix) for c in body.fixes], llm, skills)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    out = _out(deck_id, vdir, state, {"result": report})
    _refresh_result(deck_id, template_id, variant, out)
    return out
