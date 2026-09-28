"""Цветовая математика: WCAG-контраст, расстояние в CIELAB, светлота. Общая для парсинга и аудита."""

from __future__ import annotations

import math
from functools import lru_cache


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _linear(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


@lru_cache(maxsize=4096)
def relative_luminance(h: str) -> float:
    r, g, b = (_linear(c) for c in hex_to_rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """WCAG 2.x: 1..21, норма для обычного текста — 4.5."""
    la, lb = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def is_dark(h: str) -> bool:
    # порог по контрасту: на тёмном фоне белый текст контрастнее чёрного
    return contrast_ratio(h, "FFFFFF") > contrast_ratio(h, "000000")


@lru_cache(maxsize=4096)
def to_lab(h: str) -> tuple[float, float, float]:
    r, g, b = (_linear(c) for c in hex_to_rgb(h))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a: str, b: str) -> float:
    """CIE76: < 2.3 — неразличимо глазом, < 5 — «тот же цвет» для целей палитры."""
    return math.dist(to_lab(a), to_lab(b))


def chroma(h: str) -> float:
    _, a, b = to_lab(h)
    return math.hypot(a, b)
