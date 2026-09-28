"""Структура колоды до этапа вёрстки: состав слайдов, порядок, смысл каждого."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

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


# ── Содержимое слайда: не зависит от шаблона ───────────────────────────────
# Карточки, шаги процесса, KPI, точки таймлайна и пункты списка — всё это items:
# вёрстка раскладывает их по повторяющейся группе выбранного образца.


class Item(BaseModel):
    heading: str | None = Field(default=None, description="Заголовок карточки/шага, 1-4 слова")
    text: str | None = Field(default=None, description="Пояснение, до 15 слов")
    value: str | None = Field(default=None, description="Число или показатель: «38%», «3 200», «2026»")


class ChartSeries(BaseModel):
    name: str
    values: list[float]


class ChartData(BaseModel):
    type: Literal["column", "bar", "line", "pie", "doughnut"] = "column"
    categories: list[str]
    series: list[ChartSeries] = Field(max_length=5)
    unit: str | None = None


class TableData(BaseModel):
    columns: list[str] = Field(max_length=5)
    rows: list[list[str]] = Field(max_length=7)


class SlideContent(BaseModel):
    kind: SlideKind
    title: str
    subtitle: str | None = Field(default=None, description="Подзаголовок или ключевая мысль")
    items: list[Item] = Field(default_factory=list, max_length=8)
    chart: ChartData | None = None
    table: TableData | None = None
    quote: str | None = None
    author: str | None = None
    image: str | None = Field(default=None, description="id картинки пользователя, если она к месту")
    notes: str = Field(default="", description="Заметки спикера")
