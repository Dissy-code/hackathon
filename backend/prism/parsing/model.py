"""Сырое представление шаблона после извлечения: всё, что есть в файле, с разрешённым наследованием.

Координаты — абсолютные, в EMU (с учётом вложенных групп). Цвета — hex RRGGBB после разрешения
ссылок на тему (schemeClr -> clrMap -> тема) и модификаторов (lumMod/lumOff/tint/shade).
Никаких суждений о смысле здесь нет — только факты из файла; интерпретация в следующих слоях.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

EMU_PER_PT = 12700


class BBox(BaseModel):
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def area(self) -> int:
        return max(self.w, 0) * max(self.h, 0)


class ShapeKind(StrEnum):
    text = "text"            # sp с текстом (в т.ч. фигура-карточка с текстом внутри)
    shape = "shape"          # sp без текста: плашки, декор, линии-фигуры
    picture = "picture"
    group = "group"
    line = "line"            # cxnSp
    table = "table"
    chart = "chart"
    smartart = "smartart"
    other = "other"


class RunStyle(BaseModel):
    font: str | None = None
    size_pt: float | None = None
    bold: bool = False
    italic: bool = False
    color: str | None = None           # hex RRGGBB
    color_ref: str | None = None       # исходная ссылка на тему (accent1, tx1…), если была


class Paragraph(BaseModel):
    text: str
    level: int = 0
    bullet: bool = False
    align: str | None = None
    style: RunStyle                    # стиль первого непустого рана — для слотов этого достаточно
    run_styles: list[RunStyle] = Field(default_factory=list)


class PlaceholderRef(BaseModel):
    type: str                          # title, body, pic, ctrTitle, sldNum, …
    idx: int


class ImageRef(BaseModel):
    sha1: str
    content_type: str
    px_w: int | None = None
    px_h: int | None = None
    avg_color: str | None = None       # средний цвет непрозрачных пикселей — для контраста текста поверх
    opaque_ratio: float | None = None  # доля непрозрачных пикселей: иконки/вырезанные объекты << 1
    # «детальность» картинки сеткой DETAIL×DETAIL: разброс яркости в клетке (0…1). Ровный фон ≈ 0, нарисованные
    # в картинке карточки и цифры — заметно больше. Только для крупных картинок (фон слайда/макета).
    detail: list[list[float]] | None = None


class Shape(BaseModel):
    id: int
    name: str
    kind: ShapeKind
    bbox: BBox
    rotation: float = 0.0
    parent_group: int | None = None    # id группы-родителя
    z: int                             # порядок отрисовки внутри слайда
    geometry: str | None = None        # prstGeom: rect, roundRect, ellipse, …
    fill: str | None = None            # hex сплошной заливки / первой точки градиента
    fill_kind: str | None = None       # solid | gradient | picture | none
    line: str | None = None
    placeholder: PlaceholderRef | None = None
    paragraphs: list[Paragraph] = Field(default_factory=list)
    prompt_text: bool = False          # пустой плейсхолдер: paragraphs взяты из подсказки макета
    autofit: str | None = None         # norm (ужимать текст) | shape (растить фигуру) | none
    autofit_scale: float = 1.0         # normAutofit fontScale
    insets: tuple[int, int, int, int] | None = None   # внутренние отступы текста l, t, r, b (EMU)
    image: ImageRef | None = None
    table_size: tuple[int, int] | None = None   # rows, cols
    chart_type: str | None = None
    chart_series: int | None = None

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.paragraphs)


class Background(BaseModel):
    kind: str                          # solid | gradient | picture | none
    color: str | None = None           # для picture — средний цвет картинки
    image: ImageRef | None = None
    source: str                        # slide | layout | master


class Guide(BaseModel):
    orient: str                        # v (вертикальная, задаёт x) | h (горизонтальная, задаёт y)
    pos: int                           # EMU
    source: str                        # presentation | master:N | layout:<name>


class SlideRaw(BaseModel):
    index: int                         # 1-based, как в PowerPoint
    layout: str
    master: int
    background: Background
    shapes: list[Shape]
    notes: str = ""


class LayoutRaw(BaseModel):
    name: str
    master: int
    background: Background
    shapes: list[Shape]


class ThemeRaw(BaseModel):
    name: str | None
    colors: dict[str, str]             # dk1, lt1, accent1… -> hex
    major_font: str | None
    minor_font: str | None


class MasterRaw(BaseModel):
    index: int
    theme: ThemeRaw
    color_map: dict[str, str]          # tx1 -> dk1, bg1 -> lt1, …
    background: Background
    shapes: list[Shape]


class TemplateRaw(BaseModel):
    source: str
    sha256: str
    slide_w: int
    slide_h: int
    masters: list[MasterRaw]
    layouts: list[LayoutRaw]
    slides: list[SlideRaw]
    guides: list[Guide] = Field(default_factory=list)
