"""Геометрические отношения фигур на слайде."""

from __future__ import annotations

from prism.parsing.model import BBox, Shape, ShapeKind, SlideRaw


def contains(outer: BBox, inner: BBox, tol: int = 0) -> bool:
    return (inner.x >= outer.x - tol and inner.y >= outer.y - tol
            and inner.right <= outer.right + tol and inner.bottom <= outer.bottom + tol)


def contains_point(b: BBox, x: float, y: float) -> bool:
    return b.x <= x <= b.right and b.y <= y <= b.bottom


def intersection(a: BBox, b: BBox) -> int:
    w = min(a.right, b.right) - max(a.x, b.x)
    h = min(a.bottom, b.bottom) - max(a.y, b.y)
    return w * h if w > 0 and h > 0 else 0


def is_bleed(b: BBox, slide_w: int, slide_h: int) -> bool:
    """Фигура выходит за край слайда — это декор «навылет», а не контент."""
    return b.x < 0 or b.y < 0 or b.right > slide_w or b.bottom > slide_h


def surface_color(s: Shape) -> str | None:
    """Цвет, который фигура даёт как подложка для того, что лежит поверх."""
    if s.fill_kind in ("solid", "gradient") and s.fill:
        return s.fill
    if s.kind == ShapeKind.picture and s.image and s.image.avg_color and (s.image.opaque_ratio or 0) > 0.9:
        return s.image.avg_color
    return None


def backdrop(slide: SlideRaw, shape: Shape) -> str | None:
    """Цвет под текстом фигуры: собственная заливка, иначе верхняя непрозрачная фигура ниже по z, иначе фон."""
    own = surface_color(shape) if shape.kind != ShapeKind.picture else None
    if own:
        return own
    cx, cy = shape.bbox.x + shape.bbox.w / 2, shape.bbox.y + shape.bbox.h / 2
    below = [s for s in slide.shapes if s.z < shape.z and s.id != shape.id and contains_point(s.bbox, cx, cy)]
    for s in sorted(below, key=lambda s: -s.z):
        color = surface_color(s)
        if color:
            return color
    return slide.background.color
