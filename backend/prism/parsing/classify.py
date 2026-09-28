"""Классификация слайдов шаблона: образец / служебный / библиотека ассетов и смысловой тип образца.

Два слоя:
  * heuristic_* — детерминированные правила по структуре паттерна (работают без модели, покрыты тестами);
  * classify_with_llm — скилл template_classifier уточняет тип по текстовому описанию всех слайдов разом.
Если модель недоступна или ответ невалиден, остаётся эвристика — пайплайн не останавливается.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field

from prism.llm.client import LLMFactory
from prism.llm.structured import StructuredOutputError, ainvoke_structured
from prism.parsing.model import ShapeKind, SlideRaw
from prism.parsing.patterns import Pattern, SlotKind
from prism.planning.schemas import SlideKind
from prism.skills.registry import SkillRegistry

ASSET_LIBRARY_PICTURES = 25
ASSET_LIBRARY_GROUPS = 20      # векторные иконки: десятки групп фигур на одном слайде
_HEX = re.compile(r"#?\b[0-9A-F]{6}\b")
_URL = re.compile(r"https?://|www\.|\.ru/|\.com/")
_CODE = re.compile(r"[{};]\s*$|^\s*[a-z-]+\s*:\s*[^:]+;\s*$", re.MULTILINE)
_GUIDE_WORDS = re.compile(r"шрифт|font|палитр|цвет[аы]?:|используй|можно брать|скачать|рекомендуем", re.IGNORECASE)
# фразы, которые однозначно выдают инструкцию к шаблону — достаточно одной
_GUIDE_STRONG = re.compile(
    r"привет, участник|горячие клавиши|обязательный блок|логотип[ыа]? постановщ|этот шаблон|эта презентация —",
    re.IGNORECASE,
)
_YEAR = re.compile(r"^(19|20)\d\d$")
_DATE_WORD = re.compile(r"\b(дата|январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]|июн\w*|июл\w*|август\w*|"
                        r"сентябр\w*|октябр\w*|ноябр\w*|декабр\w*|янв|фев|мар|апр|авг|сен|окт|ноя|дек|"
                        r"q[1-4]|квартал\w*)\b", re.IGNORECASE)
_CLOSING = re.compile(r"спасибо|вопрос|q&a|контакт|thank", re.IGNORECASE)
_AGENDA = re.compile(r"оглавлен|содержан|agenda|план", re.IGNORECASE)
_PERSON = re.compile(r"имя|фамили|должност|роль в команде|спикер", re.IGNORECASE)


class Usage(StrEnum):
    pattern = "pattern"              # образец для генерации
    service = "service"              # гайдлайн, инструкция, ссылки — в генерацию не идёт
    asset_library = "asset_library"  # библиотека иконок/логотипов — источник ассетов


class SlideClass(BaseModel):
    index: int
    usage: Usage
    kind: SlideKind | None = None
    purpose: str = Field(default="", description="Что это за слайд, 3-8 слов по-русски")
    source: str = "heuristic"        # heuristic | llm


class TemplateClassification(BaseModel):
    slides: list[SlideClass]
    skill: dict | None = None        # manifest_entry скилла, если размечала модель


# ── эвристики ──────────────────────────────────────────────────────────────


def _all_text(slide: SlideRaw) -> str:
    return "\n".join(s.text for s in slide.shapes if not s.prompt_text)


def heuristic_usage(slide: SlideRaw) -> Usage:
    pictures = sum(1 for s in slide.shapes if s.kind == ShapeKind.picture)
    groups = sum(1 for s in slide.shapes if s.kind == ShapeKind.group)
    if pictures >= ASSET_LIBRARY_PICTURES or groups >= ASSET_LIBRARY_GROUPS:
        return Usage.asset_library
    text = _all_text(slide)
    if _GUIDE_STRONG.search(text):
        return Usage.service
    signals = (len(_HEX.findall(text)) >= 2) + bool(_URL.search(text)) + (len(_CODE.findall(text)) >= 3) \
        + bool(_GUIDE_WORDS.search(text))
    return Usage.service if signals >= 2 or len(_CODE.findall(text)) >= 3 else Usage.pattern


def _slot_texts(p: Pattern) -> list[str]:
    out = [s.sample for s in p.slots]
    for g in p.groups:
        out += [s.sample for it in g.items for s in it]
    return [t for t in out if t]


def heuristic_kind(p: Pattern, position: int, total: int) -> SlideKind:
    title = p.title.sample if p.title else ""
    texts = _slot_texts(p)
    kinds = {s.kind for s in p.slots}
    group_kinds = {k for g in p.groups for k in g.item_kinds()}
    all_text = " ".join([title, *texts])

    if SlotKind.chart in kinds:
        return SlideKind.chart
    if SlotKind.table in kinds:
        return SlideKind.table
    if _CLOSING.search(title) or (position == total and _CLOSING.search(all_text)):
        return SlideKind.closing
    if not p.groups and len(texts) <= 1:
        return SlideKind.title if position <= 2 else SlideKind.section
    if _AGENDA.search(title):
        return SlideKind.agenda
    if p.groups:
        g = p.groups[0]
        samples = [s.sample for it in g.items for s in it if s.sample]
        if SlotKind.image in group_kinds and any(_PERSON.search(t) for t in samples):
            return SlideKind.team
        if any(_YEAR.match(t.strip()) or _DATE_WORD.search(t) for t in samples):
            return SlideKind.timeline
        if SlotKind.number in group_kinds:
            numbers = [s.sample for it in g.items for s in it if s.kind == SlotKind.number]
            if all(re.fullmatch(r"0?\d", n.strip()) for n in numbers if n):
                return SlideKind.process
            return SlideKind.kpi
        if g.count == 2 and g.arrangement == "row":
            return SlideKind.two_column
        return SlideKind.cards
    if SlotKind.number in kinds:
        return SlideKind.kpi
    if SlotKind.image in kinds:
        return SlideKind.image
    if any(t.startswith(("«", "“", '"')) for t in texts):
        return SlideKind.quote
    return SlideKind.bullets


def heuristic_classification(slides: list[SlideRaw], patterns: dict[int, Pattern]) -> TemplateClassification:
    out = []
    for s in slides:
        usage = heuristic_usage(s)
        kind = heuristic_kind(patterns[s.index], s.index, len(slides)) if usage == Usage.pattern else None
        out.append(SlideClass(index=s.index, usage=usage, kind=kind))
    return TemplateClassification(slides=out)


# ── описание для модели ────────────────────────────────────────────────────


def _clip(text: str, n: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def describe(slide: SlideRaw, p: Pattern, h: SlideClass, slide_area: int) -> str:
    pictures = sum(1 for s in slide.shapes if s.kind == ShapeKind.picture)
    guess = h.usage.value + ("/" + h.kind.value if h.kind else "")
    bg = "dark" if p.dark else "light"
    lines = [f"#{slide.index} layout={slide.layout!r} bg={bg} pictures={pictures} guess={guess}"]
    if p.title and p.title.sample:
        lines.append(f"  title: {_clip(p.title.sample)!r}")
    for g in p.groups[:3]:
        first = ", ".join(f"{s.kind.value} {_clip(s.sample, 40)!r}" if s.sample else s.kind.value for s in g.items[0])
        lines.append(f"  group: {g.count} items {g.arrangement}{' (fixed)' if g.fixed_count else ''}; item: [{first}]")
    for s in p.slots[:6]:
        lines.append(f"  {s.kind.value}: {_clip(s.sample)!r}" if s.sample else f"  {s.kind.value}")
    # крупные картинки: скриншоты, мокапы, графики-картинки, декоративные 3D-объекты
    for s in sorted((s for s in slide.shapes if s.kind == ShapeKind.picture), key=lambda s: -s.bbox.area)[:3]:
        share = s.bbox.area / slide_area
        if share >= 0.08:
            look = "opaque" if s.image and (s.image.opaque_ratio or 0) >= 0.9 else "cutout"
            lines.append(f"  large picture: {share:.0%} of slide, {look}")
    if h.usage != Usage.pattern:
        lines.append(f"  text: {_clip(_all_text(slide), 160)!r}")
    return "\n".join(lines)


async def classify_with_llm(
    slides: list[SlideRaw], patterns: dict[int, Pattern], llm: LLMFactory, skills: SkillRegistry,
    *, slide_area: int, batch: int = 30,
) -> TemplateClassification:
    base = heuristic_classification(slides, patterns)
    by_index = {c.index: c for c in base.slides}
    skill = skills.get("template_classifier")
    model = llm.for_role(skill.role)
    for start in range(0, len(slides), batch):
        chunk = slides[start:start + batch]
        msgs = skill.render(
            kinds=[k.value for k in SlideKind],
            slides="\n".join(describe(s, patterns[s.index], by_index[s.index], slide_area) for s in chunk),
        )
        try:
            result = await ainvoke_structured(model, msgs, TemplateClassification, retries=1)
        except (StructuredOutputError, OSError, ValueError):
            continue  # остаётся эвристика
        for c in result.slides:
            if c.index in by_index and start < c.index <= start + len(chunk):
                if c.usage == Usage.pattern and c.kind is None:
                    c.kind = by_index[c.index].kind
                by_index[c.index] = c.model_copy(update={"source": "llm"})
    return TemplateClassification(slides=[by_index[s.index] for s in slides], skill=skill.manifest_entry())
