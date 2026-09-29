"""HTTP API сервиса + раздача фронтенда (статические страницы из ../frontend)."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.audit import router as audit_router
from app.auth import router as auth_router
from app.db import init_db
from app.decks import router as decks_router
from app.templates import router as templates_router
from prism.config import BACKEND_DIR, load_config
from prism.llm.client import LLMFactory
from prism.llm.fake import FakeLLMFactory
from prism.mcp.client import McpHub
from prism.skills.registry import SkillRegistry

FRONTEND_DIR = BACKEND_DIR.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    config = load_config()
    app.state.config = config
    # LLM_FAKE=1 — демо-режим без провайдера: текст подставной, вёрстка и рендер настоящие
    app.state.llm = FakeLLMFactory() if os.environ.get("LLM_FAKE") == "1" else LLMFactory(config)
    app.state.skills = SkillRegistry(pins=config.skills)
    app.state.mcp = McpHub(config.mcp)
    yield


app = FastAPI(title="ЦДС", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(templates_router)
app.include_router(decks_router)
app.include_router(audit_router)


@app.get("/api/health")
async def health() -> dict:
    cfg = app.state.config
    return {
        "status": "ok",
        "fake_llm": bool(getattr(app.state.llm, "fake", False)),
        "base_url": cfg.llm.base_url,
        "models": {slot: cfg.llm.model(slot) for slot in cfg.llm.models},
        "skills": [app.state.skills.get(n).ref for n in app.state.skills.names()],
        "mcp": sorted(app.state.mcp.servers),
    }


@app.get("/api/health/mcp")
async def health_mcp(q: str = "VK Tech") -> dict:
    """Живы ли MCP-серверы: список инструментов, а для web — пробный поиск (значит, жив и SearXNG)."""
    import asyncio

    from prism.mcp.client import McpError

    hub = app.state.mcp
    out: dict = {}
    for name in sorted(hub.servers):
        try:
            async with asyncio.timeout(20):
                tools = sorted(await hub.tools(name))
                info: dict = {"ok": True, "tools": tools}
                if name == "web":
                    found = await hub.call("web", "web_search", query=q, limit=3)
                    results = found.get("results", []) if isinstance(found, dict) else []
                    info["search"] = {"query": q, "results": len(results),
                                      "first": results[0]["url"] if results else None}
                    info["ok"] = bool(results)
        except (McpError, TimeoutError) as e:
            info = {"ok": False, "error": str(e) or "таймаут"}
        out[name] = info
    return {"servers": out, "configured": sorted(hub.servers)}


# Конфигуратор — React-сборка (npm run build -> frontend/app); без сборки — статическая версия
REACT_BUILD = FRONTEND_DIR / "app" / "index.html"


@app.get("/configurator.html", include_in_schema=False)
async def configurator() -> FileResponse:
    return FileResponse(REACT_BUILD if REACT_BUILD.exists() else FRONTEND_DIR / "configurator.html")


# Фронтенд — последним: всё, что не /api, отдаётся как статика (index.html, authorize.html, app/…)
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
