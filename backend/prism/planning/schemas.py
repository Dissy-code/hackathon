"""Структура колоды до этапа вёрстки: состав слайдов, порядок, смысл каждого."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class DeckPurpose(StrEnum):
    feature = "feature"
    product = "product"
    project = "project"
    initiative = "initiative"


class SlideKind(StrEnum):
    """Смысловой тип слайда. На этапе вёрстки сопоставляется с макетами шаблона."""

    title = "title"
    agenda = "agenda"
    section = "section"
    bullets = "bullets"
    cards = "cards"              # N параллельных тезисов в карточках/колонках
    two_column = "two_column"
    kpi = "kpi"
    chart = "chart"
    table = "table"
    process = "process"
    timeline = "timeline"
    comparison = "comparison"
    quote = "quote"
    image = "image"
    team = "team"
    closing = "closing"


class SlidePlan(BaseModel):
    kind: SlideKind
    title: str = Field(description="Заголовок-вывод, а не название темы")
    key_message: str = Field(description="Главная мысль слайда одним предложением")
    content_brief: str = Field(description="Что должно быть на слайде: тезисы, данные, визуал")
    source_refs: list[str] = Field(
        default_factory=list, description="Идентификаторы фрагментов исходных материалов, на которые опирается слайд"
    )


class DeckOutline(BaseModel):
    title: str
    language: str = Field(description="Язык колоды, код ISO 639-1")
    audience: str
    storyline: str = Field(description="Логика повествования колоды в 1-2 предложениях")
    slides: list[SlidePlan] = Field(min_length=3, max_length=40)
