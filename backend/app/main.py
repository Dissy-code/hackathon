"""HTTP API сервиса + раздача фронтенда (статические страницы из ../frontend)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.templates import router as templates_router
from prism.config import BACKEND_DIR, load_config
from prism.llm.client import LLMFactory
from prism.skills.registry import SkillRegistry

FRONTEND_DIR = BACKEND_DIR.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    app.state.config = config
    app.state.llm = LLMFactory(config)
    app.state.skills = SkillRegistry(pins=config.skills)
    yield


app = FastAPI(title="ЦДС", version="0.1.0", lifespan=lifespan)
app.include_router(templates_router)


@app.get("/api/health")
async def health() -> dict:
    cfg = app.state.config
    return {
        "status": "ok",
        "profile": cfg.profile,
        "models": {slot: ref.name for slot, ref in cfg.active.models.items()},
        "skills": [app.state.skills.get(n).ref for n in app.state.skills.names()],
    }


# Фронтенд — последним: всё, что не /api, отдаётся как статика (index.html, configurator.html…)
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
