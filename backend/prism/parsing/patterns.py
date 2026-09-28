"""Паттерны слайдов: слоты (куда встаёт контент) и повторяющиеся группы (N одинаковых карточек/шагов).

Повторяющиеся группы ищутся без опоры на подложки карточек:
  1. фигуры с одинаковой «подписью» (тип, размер, стиль текста) образуют дорожку;
  2. дорожки с равным числом элементов и постоянным сдвигом между ними — одна группа;
     i-й элемент группы = i-е фигуры всех её дорожек (заголовок + текст + иконка + подложка).
Так находятся и карточки на подложках, и «голые» колонки текста, и шаги таймлайна.
"""

from __future__ import annotations

import re
from enum import StrEnum
from itertools import pairwise
from statistics import median

from pydantic import BaseModel, Field

from prism.parsing.geometry import backdrop, intersection, is_bleed
from prism.parsing.model import BBox, RunStyle, Shape, ShapeKind, SlideRaw, TemplateRaw
from prism.parsing.tokens import DesignTokens

SIZE_TOL = 0.04          # относительный допуск размеров внутри дорожки
OFFSET_TOL = 0.02        # допуск сдвига между дорожками, доля ширины слайда
MAX_CANDIDATE_AREA = 0.40
_SERVICE_PH = {"slide_number", "sldnum", "footer", "ftr", "date", "dt"}
_TITLE_PH = {"title", "center_title", "ctrtitle"}
_IMAGE_HINT = re.compile(r"фото|иллюстрац|скрин|изображен|картин|мокап|photo|image|picture|qr", re.IGNORECASE)
_NUMBER = re.compile(r"^\s*[<>~≈+\-]?\s*[\dXxХх]+([.,:]\d+)?\s*(%|₽|\$|k|к|млн|млрд|\*)?\s*$")


class SlotKind(StrEnum):
    title = "title"
    heading = "heading"      # заголовок внутри блока/карточки
    text = "text"            # основной текст
    caption = "caption"      # подпись, мелкий текст
    number = "number"        # крупное число/показатель, номер шага
    image = "image"
    icon = "icon"
    chart = "chart"
    table = "table"
    surface = "surface"      # подложка карточки (не заполняется, но масштабируется вместе с элементом)


class Slot(BaseModel):
    shape_id: int
    kind: SlotKind
    bbox: BBox
    sample: str = ""                           # текст в шаблоне — подсказка модели о характере слота
    styles: list[RunStyle] = Field(default_factory=list)   # стиль каждого абзаца-образца
    backdrop: str | None = None
    autofit: str | None = None
    insets: tuple[int, int, int, int] | None = None      # внутренние отступы текста l, t, r, b (EMU)
    table_rows: int | None = None                           # у слота-таблицы: сколько строк в образце


class RepeatGroup(BaseModel):
    arrangement: str                           # row | column | grid
    rows: int
    cols: int
    items: list[list[Slot]]                    # элемент = слоты одной карточки, в порядке чтения
    item_bboxes: list[BBox]
    fixed_count: bool = False                  # карточки нарисованы в макете: число элементов менять нельзя

    @property
    def count(self) -> int:
        return len(self.items)

    def item_kinds(self) -> list[str]:
        return [s.kind.value for s in self.items[0]] if self.items else []


class Picture(BaseModel):
    """Крупная картинка-декорация: 3D-объект, пример графика, иллюстрация. Не слот, но занимает место —
    заголовок не должен на неё наезжать, а пример графика заменяется настоящим."""

    shape_id: int
    bbox: BBox
    cutout: bool                               # с прозрачностью (вырезанный объект), а не прямоугольное фото
    from_layout: bool = False                  # нарисована в макете: мешает, но удалить со слайда нельзя
    bleed: bool = False                        # выходит за край слайда (фоновая декорация)


class Pattern(BaseModel):
    id: str                                    # slide:7 | layout:<name>
    source: str                                # slide | layout
    slide_index: int | None = None
    layout: str
    background: str | None
    dark: bool
    title: Slot | None
    slots: list[Slot]                          # одиночные слоты вне групп
    groups: list[RepeatGroup]
    decor: list[int]                           # id фигур, которые копируются как есть
    pictures: list[Picture] = Field(default_factory=list)
    title_plate: Picture | None = None         # плашка под заголовком (подгоняется под длину текста)
    text_chars: int                            # объём текста в образце


# ── классификация отдельных фигур ──────────────────────────────────────────


def _style_role(size: float | None, tokens: DesignTokens) -> str | None:
    if not size or not tokens.type_scale:
        return None
    step = min(tokens.type_scale, key=lambda s: min(abs(size - x) for x in s.sizes))
    return step.role


def _text_kind(shape: Shape, tokens: DesignTokens) -> SlotKind:
    text = shape.text.strip()
    lead = next((p.text.strip() for p in shape.paragraphs if p.text.strip()), "")
    first = shape.paragraphs[0].style if shape.paragraphs else None
    role = _style_role(first.size_pt if first else None, tokens)
    if _NUMBER.match(lead) and len(lead) <= 8:
        return SlotKind.number
    if _IMAGE_HINT.search(text) and len(text) <= 30:
        return SlotKind.image
    if role in ("display",):
        return SlotKind.number if len(text) <= 12 else SlotKind.heading
    if role in ("title", "heading"):
        return SlotKind.heading
    if role == "caption":
        return SlotKind.caption
    return SlotKind.text


def _shape_kind(shape: Shape, raw: TemplateRaw, tokens: DesignTokens) -> SlotKind | None:
    """Слот для фигуры или None, если это декор."""
    slide_area = raw.slide_w * raw.slide_h
    if shape.kind == ShapeKind.chart:
        return SlotKind.chart
    if shape.kind == ShapeKind.table:
        return SlotKind.table
    if shape.placeholder and shape.placeholder.type in ("picture", "pic"):
        return SlotKind.image
    if shape.kind == ShapeKind.text:
        return _text_kind(shape, tokens)
    if shape.kind == ShapeKind.picture or shape.fill_kind == "picture":   # в т.ч. фото в круглой рамке
        max_side = max(shape.bbox.w, shape.bbox.h)
        img = shape.image
        photo_like = img is not None and (img.opaque_ratio or 0) >= 0.9
        if photo_like and max_side > raw.slide_w * 0.04 and shape.bbox.area <= 0.6 * slide_area:
            return SlotKind.image
        if max_side <= raw.slide_w * 0.08:
            return SlotKind.icon
        return None
    if shape.kind == ShapeKind.shape and shape.fill_kind in ("solid", "gradient"):
        return SlotKind.surface
    return None


def _clip(b: BBox, raw: TemplateRaw) -> BBox:
    x, y = max(b.x, 0), max(b.y, 0)
    return BBox(x=x, y=y, w=min(b.right, raw.slide_w) - x, h=min(b.bottom, raw.slide_h) - y)


def _slot(shape: Shape, kind: SlotKind, slide: SlideRaw, raw: TemplateRaw) -> Slot:
    # текстовая рамка шире слайда — частый случай (текст выровнен влево и помещается); слот — видимая часть
    return Slot(
        shape_id=shape.id, kind=kind, bbox=_clip(shape.bbox, raw), sample=shape.text.strip()[:200],
        styles=[p.style for p in shape.paragraphs if p.text.strip()] or [p.style for p in shape.paragraphs[:1]],
        backdrop=backdrop(slide, shape) if shape.paragraphs else None,
        autofit=shape.autofit,
        insets=shape.insets,
        table_rows=shape.table_size[0] if shape.table_size else None,
    )


# ── повторяющиеся группы ───────────────────────────────────────────────────


def _signature(shape: Shape) -> tuple:
    if shape.kind == ShapeKind.text and shape.paragraphs:
        st = shape.paragraphs[0].style
        return ("text", st.size_pt, st.color, st.bold, shape.fill)
    if shape.kind == ShapeKind.picture:
        return ("picture",)
    return (shape.kind.value, shape.geometry, shape.fill, shape.line)


def _similar_size(a: Shape, b: Shape) -> bool:
    text = a.kind == ShapeKind.text
    # у текста с автоподбором фигуры (spAutoFit) ширина/высота зависят от длины образца
    w_tol = 0.6 if text and "shape" in (a.autofit, b.autofit) else SIZE_TOL
    h_tol = 0.35 if text else SIZE_TOL
    return (abs(a.bbox.w - b.bbox.w) <= w_tol * max(a.bbox.w, b.bbox.w, 1)
            and abs(a.bbox.h - b.bbox.h) <= h_tol * max(a.bbox.h, b.bbox.h, 1))


def _reading_order(shapes: list[Shape], row_tol: int) -> list[Shape]:
    rows: list[list[Shape]] = []
    for s in sorted(shapes, key=lambda s: s.bbox.y):
        if rows and abs(s.bbox.y - rows[-1][0].bbox.y) <= row_tol:
            rows[-1].append(s)
        else:
            rows.append([s])
    return [s for row in rows for s in sorted(row, key=lambda s: s.bbox.x)]


def _tracks(candidates: list[Shape], row_tol: int) -> list[list[Shape]]:
    buckets: dict[tuple, list[Shape]] = {}
    for s in candidates:
        buckets.setdefault(_signature(s), []).append(s)
    tracks = []
    for members in buckets.values():
        # внутри сигнатуры — дополнительно по размеру
        clusters: list[list[Shape]] = []
        for s in members:
            for cl in clusters:
                if _similar_size(cl[0], s):
                    cl.append(s)
                    break
            else:
                clusters.append([s])
        tracks += [_reading_order(cl, row_tol) for cl in clusters if len(cl) >= 2]
    return tracks


def _center(b: BBox) -> tuple[float, float]:
    return b.x + b.w / 2, b.y + b.h / 2


def _parallel(a: list[Shape], b: list[Shape], tol: float) -> bool:
    if len(a) != len(b):
        return False
    offs = [(_center(y.bbox)[0] - _center(x.bbox)[0], _center(y.bbox)[1] - _center(x.bbox)[1]) for x, y in zip(a, b)]
    mx, my = median(o[0] for o in offs), median(o[1] for o in offs)
    # элементы одной карточки лежат рядом: сдвиг между дорожками меньше шага между элементами
    return all(abs(o[0] - mx) <= tol and abs(o[1] - my) <= tol for o in offs)


def _gap(a: BBox, b: BBox) -> float:
    """Расстояние между прямоугольниками (0, если пересекаются)."""
    dx = max(a.x - b.right, b.x - a.right, 0)
    dy = max(a.y - b.bottom, b.y - a.bottom, 0)
    return (dx * dx + dy * dy) ** 0.5


def _same_items(a: list[Shape], b: list[Shape]) -> bool:
    """Каждый b[i] ближе всего именно к a[i] — значит, они части одной карточки."""
    for i, bi in enumerate(b):
        own = (_gap(a[i].bbox, bi.bbox), _dist(a[i].bbox, bi.bbox))
        for j, aj in enumerate(a):
            if j != i and (_gap(aj.bbox, bi.bbox), _dist(aj.bbox, bi.bbox)) <= own:
                return False
    return True


def _dist(a: BBox, b: BBox) -> float:
    (x0, y0), (x1, y1) = _center(a), _center(b)
    return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5


def _union(boxes: list[BBox]) -> BBox:
    x, y = min(b.x for b in boxes), min(b.y for b in boxes)
    return BBox(x=x, y=y, w=max(b.right for b in boxes) - x, h=max(b.bottom for b in boxes) - y)


def _count_lines(values: list[int], tol: int) -> int:
    lines: list[int] = []
    for v in sorted(values):
        if not lines or v - lines[-1] > tol:
            lines.append(v)
    return len(lines)


def find_groups(shapes: list[Shape], raw: TemplateRaw, slide: SlideRaw, tokens: DesignTokens,
                kinds: dict[int, SlotKind]) -> tuple[list[RepeatGroup], set[int]]:
    row_tol = raw.slide_h // 50
    tol = raw.slide_w * OFFSET_TOL
    tracks = _tracks([s for s in shapes if s.id in kinds], row_tol)

    # объединяем параллельные дорожки (union-find по индексам)
    parent = list(range(len(tracks)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(tracks)):
        for j in range(i + 1, len(tracks)):
            if _parallel(tracks[i], tracks[j], tol) and _same_items(tracks[i], tracks[j]):
                parent[find(j)] = find(i)

    components: dict[int, list[list[Shape]]] = {}
    for i, tr in enumerate(tracks):
        components.setdefault(find(i), []).append(tr)

    groups_members: list[list[list[Shape]]] = []   # группа -> элементы -> фигуры
    used: set[int] = set()
    for comp in components.values():
        items = [list(m) for m in zip(*comp)]
        groups_members.append(items)
        used.update(s.id for it in items for s in it)

    # выделенная («акцентная») карточка выпадает из дорожки по стилю, и её элементы образуют
    # отдельную группу короче основной. Присоединяем меньшие группы к бóльшим, если каждый их
    # элемент однозначно ложится в свою карточку.
    for small in sorted(groups_members, key=len):
        for big in sorted(groups_members, key=len, reverse=True):
            if len(big) <= len(small):
                break
            mapping = _map_to_items([_union([s.bbox for s in it]) for it in small], big, tol)
            if mapping:
                for k, i in mapping:
                    big[i].extend(small[k])
                groups_members.remove(small)
                break
    # подложки, которые так и не присоединились к карточкам с контентом, — декоративный ритм
    for items in [g for g in groups_members if all(kinds[s.id] == SlotKind.surface for it in g for s in it)]:
        groups_members.remove(items)
        used.difference_update(s.id for it in items for s in it)
    free = [s for s in shapes if s.id in kinds and s.id not in used]
    for items in groups_members:
        _extend_by_pitch(items, free, used, tol)
        _fill_gaps(items, free, used, tol)

    groups = []
    for items in groups_members:
        boxes = [_union([s.bbox for s in it]) for it in items]
        rows = _count_lines([b.y for b in boxes], row_tol)
        cols = _count_lines([b.x for b in boxes], raw.slide_w // 50)
        groups.append(RepeatGroup(
            arrangement="row" if rows == 1 else "column" if cols == 1 else "grid", rows=rows, cols=cols,
            items=[[_slot(s, kinds[s.id], slide, raw) for s in sorted(it, key=lambda s: (s.bbox.y, s.bbox.x))]
                   for it in items],
            item_bboxes=boxes,
            fixed_count=_drawn_in_layout(boxes, raw, slide),
        ))
    return sorted(groups, key=lambda g: -g.count), used


def _map_to_items(boxes: list[BBox], items: list[list[Shape]], tol: float) -> list[tuple[int, int]] | None:
    """Сопоставляет рамки элементам группы: [(индекс рамки, индекс элемента)] или None."""
    item_boxes = [_union([s.bbox for s in it]) for it in items]
    mapping, offsets = [], []
    for k, box in enumerate(boxes):
        gaps = sorted((_gap(b, box), _dist(b, box), i) for i, b in enumerate(item_boxes))
        (g0, d0, i0), (g1, d1, _) = gaps[0], gaps[1]
        if g0 > 2 * tol or (g1, d1) <= (g0, d0):      # не рядом с карточкой или неоднозначно
            return None
        anchor = items[i0][0].bbox
        mapping.append((k, i0))
        offsets.append((box.x - anchor.x, box.y - anchor.y))
    if len({i for _, i in mapping}) != len(mapping):
        return None
    mx, my = median(o[0] for o in offsets), median(o[1] for o in offsets)
    if any(abs(o[0] - mx) > tol or abs(o[1] - my) > tol for o in offsets):
        return None
    return mapping


def _extend_by_pitch(items: list[list[Shape]], free: list[Shape], used: set[int], tol: float) -> None:
    """Ряд/колонка с шагом p: если на позиции «до первого» или «после последнего» стоит фигура того же
    размера, что опорная, — это ещё один элемент (обычно выделенная карточка другого цвета)."""
    if len(items) < 2:
        return
    anchors = [it[0].bbox for it in items]
    xs, ys = [a.x for a in anchors], [a.y for a in anchors]
    horizontal = max(ys) - min(ys) <= tol
    vertical = max(xs) - min(xs) <= tol
    if not (horizontal or vertical):
        return
    order = sorted(range(len(items)), key=lambda i: anchors[i].x if horizontal else anchors[i].y)
    steps = [(anchors[b].x - anchors[a].x, anchors[b].y - anchors[a].y) for a, b in pairwise(order)]
    dx, dy = median(s[0] for s in steps), median(s[1] for s in steps)
    ref = anchors[order[0]]
    for direction, start in ((1, anchors[order[-1]]), (-1, anchors[order[0]])):
        ex, ey = start.x + direction * dx, start.y + direction * dy
        while True:
            cand = next((c for c in free if c.id not in used and abs(c.bbox.x - ex) <= tol
                         and abs(c.bbox.y - ey) <= tol and abs(c.bbox.w - ref.w) <= 0.15 * ref.w
                         and abs(c.bbox.h - ref.h) <= 0.15 * ref.h), None)
            if cand is None:
                break
            new_item = [cand]
            used.add(cand.id)
            if direction > 0:
                items.append(new_item)
            else:
                items.insert(0, new_item)
            ex, ey = ex + direction * dx, ey + direction * dy


def _fill_gaps(items: list[list[Shape]], free: list[Shape], used: set[int], tol: float) -> None:
    """Если у части карточек есть элемент, а у других на том же месте нет — ищем там любую подходящую фигуру."""
    for a in items:
        anchor_a = a[0].bbox
        for member in a[1:]:
            ox, oy = member.bbox.x - anchor_a.x, member.bbox.y - anchor_a.y
            for b in items:
                if b is a:
                    continue
                anchor_b = b[0].bbox
                ex, ey = anchor_b.x + ox, anchor_b.y + oy
                if any(abs(m.bbox.x - ex) <= tol and abs(m.bbox.y - ey) <= tol for m in b):
                    continue
                for cand in free:
                    if (cand.id not in used and abs(cand.bbox.x - ex) <= tol and abs(cand.bbox.y - ey) <= tol
                            and abs(cand.bbox.w - member.bbox.w) <= 0.3 * member.bbox.w):
                        b.append(cand)
                        used.add(cand.id)
                        break


def _layout_shapes(raw: TemplateRaw, slide: SlideRaw) -> list[Shape]:
    layout = next((lay for lay in raw.layouts if lay.name == slide.layout and lay.master == slide.master), None)
    return layout.shapes if layout else []


def _drawn_in_layout(boxes: list[BBox], raw: TemplateRaw, slide: SlideRaw) -> bool:
    """Карточки нарисованы в макете (картинки/подложки), а на слайде только текст поверх — менять число нельзя."""
    layout = next((lay for lay in raw.layouts if lay.name == slide.layout and lay.master == slide.master), None)
    if layout is None:
        return False
    visuals = [s.bbox for s in layout.shapes
               if not s.placeholder and s.kind in (ShapeKind.picture, ShapeKind.shape, ShapeKind.text)]
    hits = sum(1 for b in boxes if any(intersection(b, v) >= 0.5 * b.area and v.area <= 4 * b.area for v in visuals))
    return len(boxes) > 1 and hits >= len(boxes) / 2


# ── паттерн слайда ─────────────────────────────────────────────────────────


def _title_plate(slide: SlideRaw, title: Shape) -> Shape | None:
    """Залитая фигура под заголовком, подогнанная под образец («таблетка» с текстом внутри)."""
    tb = title.bbox
    for s in sorted(slide.shapes, key=lambda s: -s.z):
        if s.z >= title.z or s.kind != ShapeKind.shape or s.fill_kind not in ("solid", "gradient"):
            continue
        b = s.bbox
        overlap = intersection(b, tb)
        # плашка обнимает начало заголовка и сопоставима с ним по высоте, а не подложка всего слайда
        if overlap >= 0.5 * min(b.area, tb.area) and b.h <= 2.5 * tb.h and b.x <= tb.x + tb.h and b.w < tb.w * 1.5:
            return s
    return None


def _safe_title_box(raw: TemplateRaw, slide: SlideRaw, title: Shape, plate: Shape | None) -> BBox:
    """Рамка заголовка минус всё видимое, что в неё залезает (логотип макета сверху, декор справа).
    В шаблоне рамку рисовали под короткий образец, и пересечения не было видно."""
    b = _clip(title.bbox, raw)
    top, bottom, left, right = b.y, b.bottom, b.x, b.right
    gap = raw.slide_h // 80
    slide_area = raw.slide_w * raw.slide_h
    candidates = [s for s in slide.shapes if s is not title and s.kind != ShapeKind.group]
    candidates += [s for s in _layout_shapes(raw, slide) if not s.placeholder]
    candidates += [s for s in raw.masters[slide.master].shapes if not s.placeholder]   # логотипы часто в мастере
    for s in candidates:
        visible = s.kind in (ShapeKind.picture, ShapeKind.text) or s.fill_kind in ("solid", "gradient", "picture")
        if not visible or (plate is not None and s.id == plate.id) or s.bbox.area > 0.5 * slide_area:
            continue
        o = _clip(s.bbox, raw)
        if intersection(o, BBox(x=left, y=top, w=right - left, h=bottom - top)) <= 0:
            continue
        if o.x > left + (right - left) * 0.4:             # справа — сужаем
            right = min(right, o.x - gap)
        elif o.bottom < top + (bottom - top) * 0.5:       # в верхней половине — опускаем верх
            top = max(top, o.bottom + gap)
        elif o.y > top + (bottom - top) * 0.5:            # в нижней половине — поднимаем низ
            bottom = min(bottom, o.y - gap)
    safe = BBox(x=left, y=top, w=right - left, h=bottom - top)
    return safe if safe.w > 0.4 * b.w and safe.h > 0.3 * b.h else b


def _pick_title(shapes: list[Shape], raw: TemplateRaw) -> Shape | None:
    ph = [s for s in shapes if s.placeholder and s.placeholder.type in _TITLE_PH]
    if ph:
        return ph[0]
    # без плейсхолдера: самый крупный текст в верхней четверти слайда, а если там пусто (титулы,
    # разделители часто держат заголовок по центру) — самый крупный текст слайда
    texts = [s for s in shapes if s.kind == ShapeKind.text and s.paragraphs]

    def size(s: Shape) -> float:
        return s.paragraphs[0].style.size_pt or 0

    top = [s for s in texts if s.bbox.y < raw.slide_h * 0.25]
    if top:
        return max(top, key=size)
    biggest = max(texts, key=size, default=None)
    return biggest if biggest is not None and (size(biggest) >= 20 or len(texts) == 1) else None


def build_pattern(raw: TemplateRaw, slide: SlideRaw, tokens: DesignTokens) -> Pattern:
    slide_area = raw.slide_w * raw.slide_h
    shapes = [s for s in slide.shapes if s.kind != ShapeKind.group
              and not (s.placeholder and s.placeholder.type in _SERVICE_PH)]
    title = _pick_title(shapes, raw)

    kinds: dict[int, SlotKind] = {}
    for s in shapes:
        off_slide = s.bbox.x >= raw.slide_w or s.bbox.y >= raw.slide_h or s.bbox.right <= 0 or s.bbox.bottom <= 0
        # таблицы и графики законно занимают полслайда и могут чуть выходить за край — их не отсеиваем
        data_frame = s.kind in (ShapeKind.table, ShapeKind.chart)
        bleed = is_bleed(s.bbox, raw.slide_w, raw.slide_h) and s.kind != ShapeKind.text and not data_frame
        too_big = s.bbox.area > MAX_CANDIDATE_AREA * slide_area and not data_frame
        if s is title or off_slide or bleed or too_big:
            continue
        kind = _shape_kind(s, raw, tokens)
        if kind is not None:
            kinds[s.id] = kind

    groups, used = find_groups(shapes, raw, slide, tokens, kinds)
    singles = [_slot(s, kinds[s.id], slide, raw) for s in shapes
               if s.id in kinds and s.id not in used and kinds[s.id] != SlotKind.surface]
    slot_ids = used | {s.shape_id for s in singles} | ({title.id} if title else set())
    bg = slide.background.color
    plate = _title_plate(slide, title) if title else None
    title_slot = None
    if title:
        title_slot = _slot(title, SlotKind.title, slide, raw)
        title_slot.bbox = _safe_title_box(raw, slide, title, plate)
    return Pattern(
        id=f"slide:{slide.index}", source="slide", slide_index=slide.index, layout=slide.layout,
        background=bg, dark=bool(bg and any(b.dark for b in tokens.backgrounds if b.color == bg)),
        title=title_slot,
        slots=sorted(singles, key=lambda s: (s.bbox.y, s.bbox.x)),
        groups=groups,
        decor=[s.id for s in slide.shapes if s.id not in slot_ids and s.kind != ShapeKind.group],
        pictures=[
            Picture(shape_id=s.id, bbox=_clip(s.bbox, raw), cutout=bool(s.image and (s.image.opaque_ratio or 1) < 0.9),
                    bleed=is_bleed(s.bbox, raw.slide_w, raw.slide_h))
            for s in slide.shapes
            if s.kind == ShapeKind.picture and s.id not in slot_ids and s.parent_group is None
            and _clip(s.bbox, raw).area >= 0.06 * slide_area and s.bbox.area < 0.9 * slide_area
        ] + [
            Picture(shape_id=s.id, bbox=_clip(s.bbox, raw), cutout=True, from_layout=True,
                    bleed=is_bleed(s.bbox, raw.slide_w, raw.slide_h))
            for s in _layout_shapes(raw, slide)
            if s.kind == ShapeKind.picture and not s.placeholder and _clip(s.bbox, raw).area >= 0.06 * slide_area
            and s.bbox.area < 0.9 * slide_area
        ],
        text_chars=sum(len(s.text.strip()) for s in slide.shapes if not s.prompt_text),
        title_plate=Picture(shape_id=plate.id, bbox=plate.bbox, cutout=False) if plate else None,
    )
