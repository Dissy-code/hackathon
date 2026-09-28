"""Пайплайн генерации: бриф -> план колоды -> тексты слайдов -> вёрстка по каждому шаблону -> превью.

Граф LangGraph (узлы — шаги с понятными границами, состояние — между ними):

    plan ──> write ──> compose ──> render

Модель работает только в plan и write; compose и render детерминированы. Прогресс идёт через
поток custom-событий: {"stage", "message", "progress"} — их ретранслирует SSE в интерфейс.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from prism.layout.compose import compose_deck
from prism.llm.client import LLMFactory
from prism.llm.structured import StructuredOutputError, ainvoke_structured
from prism.parsing.spec import TemplateSpec
from prism.planning.schemas import DeckOutline, SlideContent, SlideKind
from prism.render import RenderError, render_deck
from prism.skills.registry import SkillRegistry

WRITER_BATCH = 6
ALWAYS_KINDS = {SlideKind.title, SlideKind.section, SlideKind.closing, SlideKind.bullets}


class TemplateRef(TypedDict):
    id: str
    name: str
    pptx: str          # путь к файлу шаблона
    spec: str          # путь к spec.json


class ImageRef(TypedDict):
    id: str
    name: str
    path: str


class GenState(TypedDict, total=False):
    deck_id: str
    out_dir: str
    brief: str
    n_slides: int | None
    language: str
    templates: list[TemplateRef]
    images: list[ImageRef]
    outline: dict
    contents: list[dict]
    decks: list[dict]
    warnings: list[str]
    manifest: dict


class SlideBatch(BaseModel):
    slides: list[SlideContent] = Field(min_length=1)


def detect_language(text: str) -> str:
    cyr = len(re.findall(r"[а-яё]", text, re.IGNORECASE))
    lat = len(re.findall(r"[a-z]", text, re.IGNORECASE))
    return "ru" if cyr >= lat else "en"


def _deps(config: RunnableConfig) -> tuple[LLMFactory, SkillRegistry]:
    c = config["configurable"]
    return c["llm"], c["skills"]


def _emit(stage: str, message: str, progress: float, **extra: Any) -> None:
    get_stream_writer()({"stage": stage, "message": message, "progress": round(progress, 3), **extra})


def _specs(state: GenState) -> list[TemplateSpec]:
    return [TemplateSpec.model_validate_json(Path(t["spec"]).read_text(encoding="utf-8")) for t in state["templates"]]


# ── узлы ───────────────────────────────────────────────────────────────────


async def plan(state: GenState, config: RunnableConfig) -> dict:
    llm, skills = _deps(config)
    _emit("plan", "читаем задачу", 0.03)
    # план строим только из типов, для которых в выбранных шаблонах есть образцы
    kinds = set(ALWAYS_KINDS)
    for spec in _specs(state):
        kinds |= {p.kind for p in spec.patterns}
    n = state.get("n_slides")
    skill = skills.get("outline_planner")
    msgs = skill.render(
        purpose="determine from the brief (feature, product, project or initiative)",
        audience="determine from the brief",
        n_slides=f"exactly {n}" if n else "10-12, or as many as the brief explicitly asks for",
        language=state["language"],
        kinds=sorted(k.value for k in kinds),
        brief=state["brief"],
        materials=[],
        images=[{"id": i["id"], "name": i["name"]} for i in state.get("images", [])],
    )
    _emit("plan", "подбираем структуру колоды", 0.08)
    outline = await ainvoke_structured(llm.for_role(skill.role), msgs, DeckOutline, retries=1)
    # тип, которого нет ни в одном шаблоне, заменит вёрстка по FALLBACK — но предупредим
    _emit("plan", f"план: {len(outline.slides)} слайдов", 0.3, titles=[s.title for s in outline.slides])
    return {"outline": outline.model_dump(mode="json"), "manifest": {"skills": [skill.manifest_entry()]}}


async def write(state: GenState, config: RunnableConfig) -> dict:
    llm, skills = _deps(config)
    outline = DeckOutline.model_validate(state["outline"])
    skill = skills.get("slide_writer")
    model = llm.for_role(skill.role)
    image_ids = {i["id"] for i in state.get("images", [])}
    total = len(outline.slides)
    contents: list[SlideContent] = []
    warnings = list(state.get("warnings", []))

    for start in range(0, total, WRITER_BATCH):
        chunk = outline.slides[start:start + WRITER_BATCH]
        _emit("write", "пишем текст", 0.3 + 0.4 * start / total)
        msgs = skill.render(
            language=state["language"], brief=state["brief"], materials=[], storyline=outline.storyline,
            first=start + 1, last=start + len(chunk), total=total,
            slides=[{"n": start + i + 1, **s.model_dump(mode="json")} for i, s in enumerate(chunk)],
            images=[{"id": i["id"], "name": i["name"]} for i in state.get("images", [])],
        )
        try:
            batch = await ainvoke_structured(model, msgs, SlideBatch, retries=1)
            written = batch.slides
        except StructuredOutputError as e:
            warnings.append(f"слайды {start + 1}–{start + len(chunk)}: модель не дала валидный текст ({e})")
            written = []
        for i, planned in enumerate(chunk):
            if i < len(written):
                c = written[i]
                c.kind = planned.kind                    # тип держим по плану: под него подбирались шаблоны
                if c.image and c.image not in image_ids:
                    c.image = None                       # модель сослалась на несуществующую картинку
            else:                                        # запасной вариант — слайд из плана
                c = SlideContent(kind=planned.kind, title=planned.title, subtitle=planned.key_message)
            contents.append(c)
    _emit("write", "текст готов", 0.7)
    manifest = dict(state.get("manifest", {}))
    manifest["skills"] = [*manifest.get("skills", []), skill.manifest_entry()]
    return {"contents": [c.model_dump(mode="json") for c in contents], "warnings": warnings, "manifest": manifest}


async def compose(state: GenState, config: RunnableConfig) -> dict:
    contents = [SlideContent.model_validate(c) for c in state["contents"]]
    images = {i["id"]: Path(i["path"]).read_bytes() for i in state.get("images", [])}
    out = Path(state["out_dir"])
    decks = []
    specs = _specs(state)
    for k, (tpl, spec) in enumerate(zip(state["templates"], specs, strict=True)):
        _emit("compose", "раскладываем слайды", 0.7 + 0.15 * k / len(specs), template=tpl["id"])
        data, reports = await asyncio.to_thread(compose_deck, tpl["pptx"], spec, contents, images)
        tdir = out / tpl["id"]
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "deck.pptx").write_bytes(data)
        (tdir / "report.json").write_text(json.dumps([r.__dict__ for r in reports], ensure_ascii=False, indent=1),
                                          encoding="utf-8")
        decks.append({"template_id": tpl["id"], "name": tpl["name"], "pptx": str(tdir / "deck.pptx"),
                      "reports": [r.__dict__ for r in reports]})
    return {"decks": decks}


async def render(state: GenState, config: RunnableConfig) -> dict:
    _emit("render", "наводим красоту", 0.86)

    async def one(deck: dict) -> dict:
        pptx = Path(deck["pptx"])
        try:
            pdf, pngs = await asyncio.to_thread(render_deck, pptx, pptx.parent)
            return {**deck, "pdf": str(pdf), "previews": [str(p) for p in pngs]}
        except RenderError as e:
            return {**deck, "pdf": None, "previews": [], "render_error": str(e)}

    decks = await asyncio.gather(*(one(d) for d in state["decks"]))
    _emit("render", "готово", 1.0)
    return {"decks": list(decks)}


def build_graph():
    g = StateGraph(GenState)
    g.add_node("plan", plan)
    g.add_node("write", write)
    g.add_node("compose", compose)
    g.add_node("render", render)
    g.add_edge(START, "plan")
    g.add_edge("plan", "write")
    g.add_edge("write", "compose")
    g.add_edge("compose", "render")
    g.add_edge("render", END)
    return g.compile()


GRAPH = build_graph()


async def run(state: GenState, llm: LLMFactory, skills: SkillRegistry, on_event=None) -> GenState:
    """Прогон графа; on_event(dict) получает события прогресса. Возвращает итоговое состояние."""
    t0 = time.perf_counter()
    final: GenState = dict(state)  # type: ignore[assignment]
    config = {"configurable": {"llm": llm, "skills": skills}}
    async for mode, data in GRAPH.astream(state, config=config, stream_mode=["custom", "values"]):
        if mode == "custom" and on_event is not None:
            await on_event(data)
        elif mode == "values":
            final = data
    manifest = dict(final.get("manifest", {}))
    manifest["seconds"] = round(time.perf_counter() - t0, 1)
    manifest["model"] = llm.resolve("slide_writer").model
    final["manifest"] = manifest
    return final
