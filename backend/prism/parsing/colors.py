"""Разрешение цветов DrawingML в hex: srgbClr / schemeClr / sysClr / prstClr + модификаторы."""

from __future__ import annotations

import colorsys

from lxml import etree

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_COLOR_TAGS = {f"{{{A}}}{t}" for t in ("srgbClr", "schemeClr", "sysClr", "prstClr", "scrgbClr", "hslClr")}
_PRESET = {"black": "000000", "white": "FFFFFF", "red": "FF0000", "green": "008000", "blue": "0000FF",
           "yellow": "FFFF00", "gray": "808080", "grey": "808080"}
# Эти имена всегда идут через clrMap мастера
_MAPPED = {"tx1", "tx2", "bg1", "bg2"}


class ColorContext:
    def __init__(self, theme_colors: dict[str, str], color_map: dict[str, str]):
        self.theme = theme_colors
        self.map = color_map

    def scheme(self, name: str) -> str | None:
        if name in _MAPPED:
            name = self.map.get(name, name)
        return self.theme.get(name)


def _hex_to_rgb(h: str) -> tuple[float, float, float]:
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "".join(f"{round(min(max(c, 0.0), 1.0) * 255):02X}" for c in rgb)


def _apply_modifiers(hex_color: str, el: etree._Element) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    for m in el:
        tag = etree.QName(m).localname
        val = int(m.get("val", "100000")) / 100000
        if tag in ("lumMod", "lumOff", "satMod"):
            h, lum, s = colorsys.rgb_to_hls(r, g, b)
            if tag == "lumMod":
                lum *= val
            elif tag == "lumOff":
                lum += val
            else:
                s *= val
            r, g, b = colorsys.hls_to_rgb(h, min(max(lum, 0), 1), min(max(s, 0), 1))
        elif tag == "tint":      # к белому
            r, g, b = (c * val + (1 - val) for c in (r, g, b))
        elif tag == "shade":     # к чёрному
            r, g, b = (c * val for c in (r, g, b))
    return _rgb_to_hex((r, g, b))


def color_element(parent: etree._Element | None) -> etree._Element | None:
    """Первый дочерний элемент-цвет (для solidFill, buClr, gs, fontRef и т.п.)."""
    if parent is None:
        return None
    return next((c for c in parent if c.tag in _COLOR_TAGS), None)


def resolve(el: etree._Element | None, ctx: ColorContext, ph_color: str | None = None) -> tuple[str | None, str | None]:
    """Возвращает (hex, ссылка на тему или None)."""
    if el is None:
        return None, None
    tag = etree.QName(el).localname
    ref = None
    if tag == "srgbClr":
        base = el.get("val")
    elif tag == "schemeClr":
        ref = el.get("val")
        base = ph_color if ref == "phClr" else ctx.scheme(ref)
    elif tag == "sysClr":
        base = el.get("lastClr") or {"windowText": "000000", "window": "FFFFFF"}.get(el.get("val", ""))
    elif tag == "prstClr":
        base = _PRESET.get(el.get("val", ""))
    elif tag == "scrgbClr":
        base = _rgb_to_hex(tuple(int(el.get(k, "0")) / 100000 for k in ("r", "g", "b")))  # type: ignore[arg-type]
    elif tag == "hslClr":
        h, s, lum = (int(el.get(k, "0")) for k in ("hue", "sat", "lum"))
        base = _rgb_to_hex(colorsys.hls_to_rgb(h / 21600000, lum / 100000, s / 100000))
    else:
        return None, None
    if not base:
        return None, ref
    return _apply_modifiers(base.upper(), el), ref


def resolve_fill(parent: etree._Element | None, ctx: ColorContext) -> tuple[str | None, str | None]:
    """Заливка из spPr/bgPr/tcPr: возвращает (kind, hex)."""
    if parent is None:
        return None, None
    for child in parent:
        tag = etree.QName(child).localname
        if tag == "solidFill":
            return "solid", resolve(color_element(child), ctx)[0]
        if tag == "gradFill":
            gs = child.find(f"{{{A}}}gsLst/{{{A}}}gs")
            return "gradient", resolve(color_element(gs), ctx)[0]
        if tag == "blipFill":
            return "picture", None
        if tag == "noFill":
            return "none", None
        if tag == "pattFill":
            return "pattern", resolve(color_element(child.find(f"{{{A}}}fgClr")), ctx)[0]
    return None, None
