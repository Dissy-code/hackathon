"""Генерация колод через API.

    POST /api/images                    картинка пользователя -> {id, name, url}
    POST /api/decks                     {prompt, template_ids, image_ids?, slides?} -> {id}
    GET  /api/decks/{id}/events         SSE: прогресс {stage, message, progress}, в конце {stage: "done"|"error"}
    GET  /api/decks/{id}                статус и результат: по колоде на шаблон — превью, pptx, pdf, предупреждения
    GET  /api/decks/{id}/files/{path}   файлы колоды (pptx, pdf, png)

Задача живёт в процессе (asyncio): для хакатона этого достаточно, состояние дублируется на диск.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.auth import Db, OptionalUser
from app.templates import UPLOADS, load_spec
from prism.config import BACKEND_DIR
from prism.generation.pipeline import detect_language, run
from prism.parsing.spec import SPECS_DIR

router = APIRouter(prefix="/api", tags=["decks"])

DECKS = BACKEND_DIR / "data" / "decks"
IMAGES = BACKEND_DIR / "data" / "images"
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_IMAGE = 25 * 1024 * 1024


# ── картинки ───────────────────────────────────────────────────────────────


class ImageOut(BaseModel):
    id: str
    name: str
    url: str


@router.post("/images", response_model=ImageOut)
async def upload_image(file: UploadFile) -> ImageOut:
    name = Path(file.filename or "image").name
    ext = Path(name).suffix.lower()
    if ext not in IMAGE_TYPES:
        raise HTTPException(415, "Нужна картинка: png, jpg, webp или gif")
    data = await file.read(MAX_IMAGE + 1)
    if len(data) > MAX_IMAGE:
        raise HTTPException(413, "Картинка больше 25 МБ")
    image_id = hashlib.sha256(data).hexdigest()[:16]
    IMAGES.mkdir(parents=True, exist_ok=True)
    (IMAGES / f"{image_id}{ext}").write_bytes(data)
    (IMAGES / f"{image_id}.json").write_text(json.dumps({"name": name, "ext": ext}), encoding="utf-8")
    return ImageOut(id=image_id, name=name, url=f"/api/images/{image_id}")


def _image(image_id: str) -> tuple[Path, str]:
    meta = IMAGES / f"{image_id}.json"
    if not image_id.isalnum() or not meta.exists():
        raise HTTPException(404, f"Картинка {image_id} не найдена — загрузите её заново")
    info = json.loads(meta.read_text(encoding="utf-8"))
    return IMAGES / f"{image_id}{info['ext']}", info["name"]


@router.get("/images/{image_id}")
async def get_image(image_id: str) -> FileResponse:
    return FileResponse(_image(image_id)[0])


# ── задачи ─────────────────────────────────────────────────────────────────


@dataclass
class Job:
    id: str
    status: str = "running"                 # running | done | error
    events: list[dict] = field(default_factory=list)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    result: dict | None = None
    error: str | None = None
    task: asyncio.Task | None = None        # ссылка держит задачу от сборщика мусора


JOBS: dict[str, Job] = {}


class DeckRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=20000)
    template_ids: list[str] = Field(min_length=1, max_length=3)
    image_ids: list[str] = Field(default_factory=list, max_length=6)
    slides: int | None = Field(default=None, ge=3, le=30)


class DeckCreated(BaseModel):
    id: str


async def _publish(job: Job, event: dict) -> None:
    async with job.changed:
        job.events.append(event)
        job.changed.notify_all()


def _public_result(deck_id: str, final: dict) -> dict:
    """Пути на диске -> URL; только то, что нужно интерфейсу."""
    base = DECKS / deck_id

    def url(path: str | None) -> str | None:
        return f"/api/decks/{deck_id}/files/{Path(path).relative_to(base).as_posix()}" if path else None

    return {
        "outline": [s["title"] for s in final.get("outline", {}).get("slides", [])],
        "decks": [{
            "template_id": d["template_id"], "name": d["name"],
            "pptx": url(d["pptx"]), "pdf": url(d.get("pdf")),
            "slides": [url(p) for p in d.get("previews", [])],
            "warnings": [{"slide": r["index"], "text": w} for r in d["reports"] for w in r["warnings"]],
            "render_error": d.get("render_error"),
        } for d in final.get("decks", [])],
        "warnings": final.get("warnings", []),
        "manifest": final.get("manifest", {}),
    }


async def _run_job(job: Job, state: dict, llm, skills) -> None:
    try:
        final = await run(state, llm, skills, on_event=lambda e: _publish(job, e))
        job.result = _public_result(job.id, final)
        (DECKS / job.id / "result.json").write_text(json.dumps(job.result, ensure_ascii=False, indent=1),
                                                    encoding="utf-8")
        job.status = "done"
        await _publish(job, {"stage": "done", "message": "готово", "progress": 1.0})
    except Exception as e:  # noqa: BLE001 — любая ошибка генерации должна дойти до интерфейса, а не потеряться
        job.status, job.error = "error", f"{type(e).__name__}: {e}"
        await _publish(job, {"stage": "error", "message": job.error, "progress": 1.0})


@router.post("/decks", response_model=DeckCreated)
async def create_deck(body: DeckRequest, request: Request, db: Db, user: OptionalUser) -> DeckCreated:
    templates = []
    for tid in body.template_ids:
        spec = load_spec(tid)                                  # 404, если шаблон не разбирался
        meta = SPECS_DIR / tid / "upload.json"
        name = json.loads(meta.read_text(encoding="utf-8"))["name"] if meta.exists() else spec.source
        pptx = UPLOADS / f"{tid}.pptx"
        if not pptx.exists():
            raise HTTPException(404, f"Файл шаблона {name} не найден — загрузите его заново")
        templates.append({"id": tid, "name": name, "pptx": str(pptx), "spec": str(SPECS_DIR / tid / "spec.json")})
    images = []
    for iid in body.image_ids:
        path, name = _image(iid)
        images.append({"id": iid, "name": name, "path": str(path)})

    deck_id = uuid.uuid4().hex[:12]
    out_dir = DECKS / deck_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "request.json").write_text(body.model_dump_json(indent=1), encoding="utf-8")
    state = {"deck_id": deck_id, "out_dir": str(out_dir), "brief": body.prompt, "n_slides": body.slides,
             "language": detect_language(body.prompt), "templates": templates, "images": images}
    job = Job(id=deck_id)
    JOBS[deck_id] = job
    if user is not None:
        db.execute("INSERT INTO user_decks (user_id, deck_id, title) VALUES (?, ?, ?)",
                   (user.id, deck_id, body.prompt[:120]))
    app = request.app
    job.task = asyncio.create_task(_run_job(job, state, app.state.llm, app.state.skills))
    return DeckCreated(id=deck_id)


def _job_or_disk(deck_id: str) -> Job:
    if deck_id in JOBS:
        return JOBS[deck_id]
    result = DECKS / deck_id / "result.json"
    if deck_id.isalnum() and result.exists():               # колода из прошлого запуска сервера
        return Job(id=deck_id, status="done", result=json.loads(result.read_text(encoding="utf-8")))
    raise HTTPException(404, "Колода не найдена")


@router.get("/decks/{deck_id}/events")
async def deck_events(deck_id: str) -> StreamingResponse:
    job = _job_or_disk(deck_id)

    async def stream():
        sent = 0
        while True:
            async with job.changed:
                if sent >= len(job.events) and job.status == "running":
                    await job.changed.wait()
                pending = job.events[sent:]
            for e in pending:
                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
            sent += len(pending)
            if job.status != "running" and sent >= len(job.events):
                if not job.events:                           # колода с диска — сразу финал
                    yield f"data: {json.dumps({'stage': job.status, 'progress': 1.0}, ensure_ascii=False)}\n\n"
                return

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/decks/{deck_id}")
async def get_deck(deck_id: str) -> dict:
    job = _job_or_disk(deck_id)
    last = job.events[-1] if job.events else None
    return {"id": deck_id, "status": job.status, "error": job.error, "progress": last, "result": job.result}


@router.get("/decks/{deck_id}/files/{path:path}")
async def deck_file(deck_id: str, path: str) -> FileResponse:
    base = (DECKS / deck_id).resolve()
    target = (base / path).resolve()
    if not deck_id.isalnum() or base not in target.parents or not target.is_file():
        raise HTTPException(404, "Файл не найден")
    names = {".pptx": "presentation.pptx", ".pdf": "presentation.pdf"}
    return FileResponse(target, filename=names.get(target.suffix))
