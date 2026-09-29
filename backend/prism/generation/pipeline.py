"""Пайплайн генерации: бриф -> план колоды -> тексты слайдов -> вёрстка по каждому шаблону -> превью.

Граф LangGraph (узлы — шаги с понятными границами, состояние — между ними):

    plan ──> write ──> compose ──> render

Модель работает только в plan и write; compose и render детерминированы. Прогресс идёт через
поток custom-событий: {"stage", "message", "progress"} — их ретранслирует SSE в интерфейс.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, TypedDict

import yaml
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from prism.config import BACKEND_DIR
from prism.generation.checks import content_problems, degrade, normalize
from prism.layout.compose import SYNTH_KINDS, compose_deck
from prism.llm.client import LLMFactory
from prism.llm.structured import StructuredOutputError, ainvoke_structured
from prism.parsing.spec import TemplateSpec
from prism.planning.schemas import DeckOutline, SlideContent, SlideKind
from prism.render import RenderError, render_deck
from prism.skills.registry import SkillRegistry

WRITER_BATCH = 4          # слайдов на запрос: меньше пачка — быстрее и надёжнее валидный JSON
WRITER_PARALLEL = 8       # одновременных запросов к модели (на все варианты сразу)
RENDER_PARALLEL = max(2, (os.cpu_count() or 4) // 2)   # одновременных LibreOffice
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
    outlines: dict[str, dict]        # вариант -> план колоды
    contents: dict[str, list[dict]]  # вариант -> содержимое слайдов
    decks: list[dict]
    warnings: list[str]
    manifest: dict


class SlideBatch(BaseModel):
    slides: list[SlideContent] = Field(min_length=1)


def load_variants() -> dict[str, dict]:
    """Варианты колоды из configs/variants.yaml: подпись, режим вёрстки, директивы плану и тексту."""
    data = yaml.safe_load((BACKEND_DIR / "configs" / "variants.yaml").read_text(encoding="utf-8"))
    return {str(k): v for k, v in data["variants"].items()}


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
    """Три плана — по одному на вариант, параллельно: варианты различаются составом и группировкой слайдов."""
    llm, skills = _deps(config)
    _emit("plan", "читаем задачу", 0.03)
    kinds = set(ALWAYS_KINDS)
    for spec in _specs(state):
        kinds |= {p.kind for p in spec.patterns}
    kinds |= SYNTH_KINDS                     # то, что вёрстка умеет собрать сама, доступно при любом шаблоне
    n = state.get("n_slides")
    skill = skills.get("outline_planner")
    model = llm.for_role(skill.role)
    variants = load_variants()

    async def one(key: str, v: dict) -> tuple[str, DeckOutline]:
        msgs = skill.render(
            purpose="determine from the brief (feature, product, project or initiative)",
            audience="determine from the brief",
            n_slides=f"exactly {n}" if n else f"{v['slides']}, or as many as the brief explicitly asks for",
            language=state["language"], kinds=sorted(k.value for k in kinds), brief=state["brief"],
            materials=[], variant=v["planner"],
            images=[{"id": i["id"], "name": i["name"]} for i in state.get("images", [])],
        )
        return key, await ainvoke_structured(model, msgs, DeckOutline, retries=1)

    _emit("plan", "подбираем структуру колоды", 0.08)
    outlines = dict(await asyncio.gather(*(one(k, v) for k, v in variants.items())))
    first = next(iter(outlines.values()))
    _emit("plan", f"план: {', '.join(str(len(o.slides)) for o in outlines.values())} слайдов по вариантам", 0.3,
          titles=[s.title for s in first.slides])
    return {"outlines": {k: o.model_dump(mode="json") for k, o in outlines.items()},
            "manifest": {"skills": [skill.manifest_entry()]}}


async def _write_variant(outline: DeckOutline, style: str, state: GenState, skill, model, gate: asyncio.Semaphore,
                         warnings: list[str], tick) -> list[SlideContent]:
    """Тексты одного варианта: пачки параллельно, затем проверка полноты и один круг исправления."""
    image_ids = {i["id"] for i in state.get("images", [])}
    images = [{"id": i["id"], "name": i["name"]} for i in state.get("images", [])]
    total = len(outline.slides)

    def render(idx: list[int], extra: list[HumanMessage] | None = None):
        msgs = skill.render(
            language=state["language"], brief=state["brief"], materials=[], storyline=outline.storyline,
            first=idx[0] + 1, last=idx[-1] + 1, total=total, style=style, images=images,
            slides=[{"n": i + 1, **outline.slides[i].model_dump(mode="json")} for i in idx],
        )
        return msgs + (extra or [])

    def adopt(i: int, c: SlideContent) -> SlideContent:
        c.kind = outline.slides[i].kind          # тип держим по плану: под него подбирались шаблоны
        if c.image and c.image not in image_ids:
            c.image = None                       # модель сослалась на несуществующую картинку
        return normalize(c)

    async def batch(idx: list[int]) -> None:
        async with gate:
            try:
                written = (await ainvoke_structured(model, render(idx), SlideBatch, retries=1)).slides
            except StructuredOutputError as e:
                warnings.append(f"слайды {idx[0] + 1}–{idx[-1] + 1}: модель не дала валидный текст ({e})")
                written = []
        for k, i in enumerate(idx):
            contents[i] = adopt(i, written[k]) if k < len(written) else SlideContent(
                kind=outline.slides[i].kind, title=outline.slides[i].title, subtitle=outline.slides[i].key_message)
        tick(len(idx))

    contents: list[SlideContent | None] = [None] * total
    chunks = [list(range(st, min(st + WRITER_BATCH, total))) for st in range(0, total, WRITER_BATCH)]
    await asyncio.gather(*(batch(ch) for ch in chunks))

    problems = {i: content_problems(c) for i, c in enumerate(contents)}
    todo = [i for i, pr in problems.items() if pr]

    async def fix(idx: list[int]) -> None:
        notes = "\n".join(f"- слайд {i + 1} ({contents[i].kind.value}): " + "; ".join(problems[i]) for i in idx)
        previous = json.dumps({"slides": [contents[i].model_dump(mode="json") for i in idx]}, ensure_ascii=False)
        extra = [HumanMessage(f"Прошлая версия этих слайдов:\n{previous}\n\nЗамечания:\n{notes}\n"
                              "Исправь все замечания и верни эти слайды целиком тем же JSON.")]
        async with gate:
            try:
                fixed = (await ainvoke_structured(model, render(idx, extra), SlideBatch, retries=1)).slides
            except StructuredOutputError:
                return
        for i, c in zip(idx, fixed, strict=False):
            c = adopt(i, c)
            if len(content_problems(c)) < len(problems[i]):
                contents[i] = c

    await asyncio.gather(*(fix(todo[k:k + WRITER_BATCH]) for k in range(0, len(todo), WRITER_BATCH)))
    out = []
    for i, c in enumerate(contents):
        left = content_problems(c)
        if left:
            degraded = degrade(c)
            if degraded.kind != c.kind:
                warnings.append(f"слайд {i + 1}: {c.kind.value} -> {degraded.kind.value} ({'; '.join(left)})")
            c = degraded
        out.append(c)
    return out


async def write(state: GenState, config: RunnableConfig) -> dict:
    """Тексты всех вариантов параллельно (общий лимит одновременных запросов к модели)."""
    llm, skills = _deps(config)
    skill = skills.get("slide_writer")
    model = llm.for_role(skill.role)
    variants = load_variants()
    outlines = {k: DeckOutline.model_validate(o) for k, o in state["outlines"].items()}
    gate = asyncio.Semaphore(WRITER_PARALLEL)
    warnings: list[str] = list(state.get("warnings", []))
    total = sum(len(o.slides) for o in outlines.values())
    done = 0
    _emit("write", "пишем текст", 0.3)

    def tick(n: int) -> None:
        nonlocal done
        done += n
        _emit("write", "пишем текст", 0.3 + 0.35 * done / total)

    async def one(key: str) -> tuple[str, list[SlideContent]]:
        vw: list[str] = []
        result = await _write_variant(outlines[key], variants[key]["writer"], state, skill, model, gate, vw, tick)
        warnings.extend(f"вариант {key}: {w}" for w in vw)
        return key, result

    contents = dict(await asyncio.gather(*(one(k) for k in outlines)))
    _emit("write", "текст готов", 0.7)
    manifest = dict(state.get("manifest", {}))
    manifest["skills"] = [*manifest.get("skills", []), skill.manifest_entry()]
    return {"contents": {k: [c.model_dump(mode="json") for c in v] for k, v in contents.items()},
            "warnings": warnings, "manifest": manifest}


async def compose(state: GenState, config: RunnableConfig) -> dict:
    """Вёрстка: шаблон × вариант, у каждого варианта своё содержимое; всё параллельно, без модели."""
    variants = load_variants()
    contents = {k: [SlideContent.model_validate(c) for c in v] for k, v in state["contents"].items()}
    images = {i["id"]: Path(i["path"]).read_bytes() for i in state.get("images", [])}
    out = Path(state["out_dir"])
    _emit("compose", "раскладываем слайды", 0.72)

    async def variant(tpl: TemplateRef, spec: TemplateSpec, key: str) -> dict:
        data, reports = await asyncio.to_thread(compose_deck, tpl["pptx"], spec, contents[key], images, int(key))
        vdir = out / tpl["id"] / f"v{key}"
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / "deck.pptx").write_bytes(data)
        (vdir / "report.json").write_text(json.dumps([r.__dict__ for r in reports], ensure_ascii=False, indent=1),
                                          encoding="utf-8")
        return {"variant": int(key), "label": variants[key]["label"], "pptx": str(vdir / "deck.pptx"),
                "reports": [r.__dict__ for r in reports]}

    async def template(tpl: TemplateRef, spec: TemplateSpec) -> dict:
        vs = await asyncio.gather(*(variant(tpl, spec, k) for k in contents))
        return {"template_id": tpl["id"], "name": tpl["name"], "variants": list(vs)}

    decks = await asyncio.gather(*(template(t, s) for t, s in zip(state["templates"], _specs(state), strict=True)))
    _emit("compose", "слайды разложены", 0.8)
    return {"decks": list(decks)}


async def render(state: GenState, config: RunnableConfig) -> dict:
    """Превью и PDF всех вариантов параллельно; LibreOffice тяжёлый — не больше RENDER_PARALLEL сразу."""
    total = sum(len(d["variants"]) for d in state["decks"])
    gate = asyncio.Semaphore(RENDER_PARALLEL)
    done = 0
    _emit("render", "наводим красоту", 0.82)

    async def one(v: dict) -> dict:
        nonlocal done
        pptx = Path(v["pptx"])
        async with gate:
            try:
                pdf, pngs = await asyncio.to_thread(render_deck, pptx, pptx.parent)
                result = {**v, "pdf": str(pdf), "previews": [str(p) for p in pngs]}
            except RenderError as e:
                result = {**v, "pdf": None, "previews": [], "render_error": str(e)}
        done += 1
        _emit("render", "наводим красоту", 0.82 + 0.18 * done / total)
        return result

    # один общий пул на все колоды всех шаблонов, потом раскладываем результаты обратно по шаблонам
    flat = [(di, v) for di, d in enumerate(state["decks"]) for v in d["variants"]]
    rendered = await asyncio.gather(*(one(v) for _, v in flat))
    decks = [{**d, "variants": []} for d in state["decks"]]
    for (di, _), result in zip(flat, rendered, strict=True):
        decks[di]["variants"].append(result)
    _emit("render", "готово", 1.0)
    return {"decks": decks}


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
    manifest["variants"] = {k: v["label"] for k, v in load_variants().items()}
    final["manifest"] = manifest
    return final
