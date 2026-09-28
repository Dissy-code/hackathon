"""Подгонка текста под рамку: перенос строк по реальным метрикам шрифта, уменьшение кегля по шкале шаблона.

Метрики берутся из тех же шрифтов, что стоят в системе для рендера (fc-match): так расчёт
совпадает с тем, что нарисует LibreOffice. Тот же расчёт использует аудит («текст не влез»).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from functools import lru_cache

from lxml import etree
from PIL import ImageFont
from pptx.oxml.ns import qn

EMU_PER_PT = 12700
DEFAULT_INSETS = (91440, 45720, 91440, 45720)
LINE_SPACING = 1.2             # «одинарный» интервал PowerPoint ≈ 1.2 кегля
MIN_SCALE = 0.6                # мельче — уже нечитаемо, лучше сократить текст


@lru_cache(maxsize=256)
def font_path(family: str | None, bold: bool) -> str | None:
    pattern = f"{family or 'sans-serif'}:{'bold' if bold else 'regular'}"
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


@lru_cache(maxsize=512)
def _font(family: str | None, bold: bool, size_pt: float) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = font_path(family, bold)
    # меряем в «пунктах»: 1 pt = 1 единица, дробные кегли округляем до десятых
    try:
        return ImageFont.truetype(path, size=max(1, round(size_pt * 10))) if path else ImageFont.load_default()
    except OSError:
        return ImageFont.load_default()


def text_width_pt(text: str, family: str | None, size_pt: float, bold: bool) -> float:
    return _font(family, bold, size_pt).getlength(text) / 10


def wrap(text: str, width_pt: float, family: str | None, size_pt: float, bold: bool) -> list[str]:
    """Жадный перенос по словам; слово шире строки остаётся одно (его обрежет — это сигнал аудиту)."""
    lines: list[str] = []
    for raw_line in text.split("\n"):
        line = ""
        # только обычный пробел: неразрывный (в «3 200», «1 150 ₽») строку не рвёт
        for word in (w for w in raw_line.split(" ") if w):
            candidate = f"{line} {word}".strip()
            if line and text_width_pt(candidate, family, size_pt, bold) > width_pt:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    return lines


@dataclass
class ParaSpec:
    text: str
    font: str | None
    size_pt: float
    bold: bool
    spacing: float = LINE_SPACING
    space_before_pt: float = 0.0


@dataclass
class FitResult:
    scale: float                # применённый множитель кегля
    lines: int
    height_pt: float
    fits: bool


def measure(paras: list[ParaSpec], width_pt: float, scale: float = 1.0) -> tuple[int, float, bool]:
    """(строк, высота в пт, есть ли слово шире строки)."""
    lines, height, too_wide = 0, 0.0, False
    for i, p in enumerate(paras):
        size = p.size_pt * scale
        wrapped = wrap(p.text, width_pt, p.font, size, p.bold)
        too_wide |= any(text_width_pt(w, p.font, size, p.bold) > width_pt * 1.02 for w in wrapped)
        lines += len(wrapped)
        height += len(wrapped) * size * p.spacing + (p.space_before_pt * scale if i else 0)
    return lines, height, too_wide


def widest_line_pt(paras: list[ParaSpec], width_pt: float, scale: float) -> float:
    """Ширина самой длинной строки после переноса — чтобы подогнать плашку под текст."""
    widest = 0.0
    for p in paras:
        size = p.size_pt * scale
        for line in wrap(p.text, width_pt, p.font, size, p.bold):
            widest = max(widest, text_width_pt(line, p.font, size, p.bold))
    return widest


def fit(paras: list[ParaSpec], box_w: int, box_h: int, insets=None, steps: list[float] | None = None) -> FitResult:
    """Подбирает множитель кегля. steps — допустимые множители (из шкалы шаблона), по убыванию."""
    l, t, r, b = insets or DEFAULT_INSETS
    width_pt = max((box_w - l - r) / EMU_PER_PT, 1)
    height_pt = max((box_h - t - b) / EMU_PER_PT, 1)
    candidates = steps or [1.0, 0.92, 0.85, 0.78, 0.7, 0.64, MIN_SCALE]
    top_size = max(p.size_pt for p in paras)
    last = None
    for scale in candidates:
        lines, h, too_wide = measure(paras, width_pt, scale)
        # одна строка кегля образца влезает по определению: рамку рисовали ровно под неё
        height_pt = max(height_pt, top_size * scale * LINE_SPACING * 1.02)
        last = FitResult(scale=scale, lines=lines, height_pt=h, fits=h <= height_pt and not too_wide)
        if last.fits:
            return last
    return last


# ── чтение/запись XML фигуры ───────────────────────────────────────────────


def paragraph_specs(el: etree._Element, styles: list) -> list[ParaSpec]:
    """Параметры абзацев фигуры после заполнения; кегль/шрифт — из рана, иначе из стиля образца."""
    tx = el.find(qn("p:txBody"))
    out = []
    for i, p in enumerate(tx.findall(qn("a:p")) if tx is not None else []):
        text = "".join(t.text or "" for t in p.iter(qn("a:t")))
        if not text.strip():
            continue
        style = styles[min(i, len(styles) - 1)] if styles else None
        rpr = p.find(f"{qn('a:r')}/{qn('a:rPr')}")
        size = int(rpr.get("sz")) / 100 if rpr is not None and rpr.get("sz") else (style.size_pt if style else 14)
        latin = rpr.find(qn("a:latin")) if rpr is not None else None
        font = latin.get("typeface") if latin is not None and not latin.get("typeface", "").startswith("+") \
            else (style.font if style else None)
        bold = (rpr.get("b") in ("1", "true")) if rpr is not None and rpr.get("b") else bool(style and style.bold)
        spacing, before = LINE_SPACING, 0.0
        ppr = p.find(qn("a:pPr"))
        if ppr is not None:
            pct = ppr.find(f"{qn('a:lnSpc')}/{qn('a:spcPct')}")
            if pct is not None:
                spacing = LINE_SPACING * int(pct.get("val")) / 100000
            pts = ppr.find(f"{qn('a:spcBef')}/{qn('a:spcPts')}")
            if pts is not None:
                before = int(pts.get("val")) / 100
        out.append(ParaSpec(text=text, font=font, size_pt=size or 14, bold=bold, spacing=spacing,
                            space_before_pt=before))
    return out


def apply_scale(el: etree._Element, paras: list[ParaSpec], scale: float) -> None:
    """Проставляет итоговый кегль каждому рану; автосжатие PowerPoint выключаем, чтобы не сжать дважды."""
    tx = el.find(qn("p:txBody"))
    if tx is None:
        return
    body_pr = tx.find(qn("a:bodyPr"))
    norm = body_pr.find(qn("a:normAutofit")) if body_pr is not None else None
    if norm is not None:
        norm.attrib.pop("fontScale", None)
        norm.attrib.pop("lnSpcReduction", None)
    specs = iter(paras)
    for p in tx.findall(qn("a:p")):
        text = "".join(t.text or "" for t in p.iter(qn("a:t")))
        if not text.strip():
            continue
        spec = next(specs, None)
        if spec is None:
            break
        size = str(round(spec.size_pt * scale * 100))
        for tag in ("a:r/a:rPr", "a:endParaRPr"):
            for rpr in p.findall("/".join(qn(x) for x in tag.split("/"))):
                rpr.set("sz", size)
