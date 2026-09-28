"""HTTP API сервиса. Пока только служебные эндпоинты; бизнес-роутеры появятся вместе со слоями пайплайна."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from prism.config import load_config
from prism.llm.client import LLMFactory
from prism.skills.registry import SkillRegistry


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    app.state.config = config
    app.state.llm = LLMFactory(config)
    app.state.skills = SkillRegistry(pins=config.skills)
    yield


app = FastAPI(title="Prism", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    cfg = app.state.config
    return {
        "status": "ok",
        "profile": cfg.profile,
        "models": {slot: ref.name for slot, ref in cfg.active.models.items()},
        "skills": [app.state.skills.get(n).ref for n in app.state.skills.names()],
    }
