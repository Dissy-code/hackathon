"""Токены дизайн-системы из сырого шаблона: палитра, шрифты, типографическая шкала, поля, направляющие.

Статистика взвешивается так, чтобы каждый слайд давал одинаковый вклад: иначе служебный слайд
с библиотекой из сотен иконок перевешивает весь шаблон.
"""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, Field

from prism.color import chroma, delta_e, relative_luminance
from prism.parsing.geometry import backdrop, is_bleed
from prism.parsing.model import Shape, ShapeKind, SlideRaw, TemplateRaw

SAME_COLOR_DE = 5.0         # ΔE, ниже которого цвета считаются одним токеном
SIZE_CLUSTER_RATIO = 1.08   # соседние кегли в пределах 8% — одна ступень шкалы
ACCENT_CHROMA = 30.0        # насыщенность, с которой цвет считается акцентным
DARK_LUMINANCE = 0.3        # фон темнее — дизайнеры кладут на него светлый текст (0077FF ≈ 0.2)


class PaletteColor(BaseModel):
    hex: str
    usage: float                          # доля в суммарном весе наблюдений
    roles: dict[str, float]               # background/surface/text/accent/line -> доля внутри роли
    theme_slot: str | None = None         # accent1, dk1… если совпадает с цветом темы
    members: list[str] = Field(default_factory=list)   # слитые близкие оттенки


class TextColorPair(BaseModel):
    text: str
    backdrop: str
    share: float


class TypeStep(BaseModel):
    size_pt: float
    usage: float
    role: str                             # display | title | heading | body | caption
    sizes: list[float]                    # все исходные кегли ступени
    fonts: dict[str, float]


class FontTokens(BaseModel):
    heading: str | None
    body: str | None
    usage: dict[str, float]


class Box(BaseModel):
    left: int
    top: int
    right: int
    bottom: int


class BackgroundVariant(BaseModel):
    kind: str
    color: str | None
    dark: bool
    slides: list[int]


class DesignTokens(BaseModel):
    slide_w: int
    slide_h: int
    palette: list[PaletteColor]
    text_pairs: list[TextColorPair]
    fonts: FontTokens
    type_scale: list[TypeStep]
    margins: Box
    guides_x: list[int]
    guides_y: list[int]
    backgrounds: list[BackgroundVariant]

    def color(self, role: str) -> str | None:
        ranked = sorted((c for c in self.palette if role in c.roles), key=lambda c: -c.roles[role])
        return ranked[0].hex if ranked else None

    def accent(self) -> str | None:
        """Акцент, отличимый от основного текста (иначе выделение сольётся с текстом)."""
        text = self.color("text")
        ranked = sorted((c for c in self.palette if "accent" in c.roles), key=lambda c: -c.roles["accent"])
        for c in ranked:
            if text is None or delta_e(c.hex, text) > 15:
                return c.hex
        return ranked[0].hex if ranked else None


# ── палитра ────────────────────────────────────────────────────────────────


def _observations(slide: SlideRaw, slide_area: int) -> dict[str, dict[str, float]]:
    """Наблюдения цветов на слайде по ролям, каждая роль нормирована до 1 внутри слайда."""
    obs: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    if slide.background.color:
        obs["background"][slide.background.color] += 1
    for s in slide.shapes:
        if s.kind == ShapeKind.group:
            continue
        for p in s.paragraphs if not s.prompt_text else []:
            for rs in p.run_styles or [p.style]:
                if rs.color:
                    obs["text"][rs.color] += max(len(p.text.strip()), 1)
        if s.fill and s.fill_kind in ("solid", "gradient"):
            area = min(s.bbox.area / slide_area, 1.0)
            role = "accent" if chroma(s.fill) >= ACCENT_CHROMA else "surface"
            obs[role][s.fill] += area
        if s.line:
            obs["line"][s.line] += 1
    for role, colors in obs.items():
        total = sum(colors.values()) or 1
        for c in colors:
            colors[c] /= total
    return obs


def _cluster_colors(weights: dict[str, float]) -> list[list[str]]:
    clusters: list[list[str]] = []
    for c in sorted(weights, key=lambda c: -weights[c]):
        for cl in clusters:
            if delta_e(cl[0], c) < SAME_COLOR_DE:
                cl.append(c)
                break
        else:
            clusters.append([c])
    return clusters


def _palette(raw: TemplateRaw, slides: list[SlideRaw]) -> list[PaletteColor]:
    area = raw.slide_w * raw.slide_h
    by_role: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for slide in slides:
        for role, colors in _observations(slide, area).items():
            for c, w in colors.items():
                by_role[role][c] += w
    # текстовые акценты: насыщенный цвет текста — тоже акцент
    for c, w in by_role["text"].items():
        if chroma(c) >= ACCENT_CHROMA:
            by_role["accent"][c] += w * 0.5

    total: dict[str, float] = defaultdict(float)
    for colors in by_role.values():
        for c, w in colors.items():
            total[c] += w
    # при совпадении цветов слотов (accent1 == hlink) предпочитаем «смысловой» слот
    slot_rank = ["accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "dk1", "lt1", "dk2", "lt2",
                 "hlink", "folHlink"]
    theme: dict[str, str] = {}
    for m in raw.masters:
        for slot in sorted(m.theme.colors, key=lambda k: slot_rank.index(k) if k in slot_rank else 99):
            theme.setdefault(m.theme.colors[slot], slot)
    grand = sum(total.values()) or 1

    palette = []
    for cluster in _cluster_colors(total):
        head = cluster[0]
        roles = {}
        for role, colors in by_role.items():
            role_total = sum(colors.values()) or 1
            share = sum(colors.get(c, 0) for c in cluster) / role_total
            if share >= 0.01:
                roles[role] = round(share, 3)
        slot = next((theme[c] for c in cluster if c in theme), None)
        palette.append(PaletteColor(hex=head, usage=round(sum(total[c] for c in cluster) / grand, 4),
                                    roles=roles, theme_slot=slot, members=cluster[1:]))
    # цвета темы, которых нет на слайдах, всё равно легальны для генерации
    for hex_color, slot in theme.items():
        if not any(delta_e(hex_color, p.hex) < SAME_COLOR_DE for p in palette):
            palette.append(PaletteColor(hex=hex_color, usage=0.0, roles={}, theme_slot=slot))
    return sorted(palette, key=lambda p: -p.usage)


def _text_pairs(slides: list[SlideRaw]) -> list[TextColorPair]:
    pairs: dict[tuple[str, str], float] = defaultdict(float)
    for slide in slides:
        chars = defaultdict(float)
        for s in slide.shapes:
            if s.prompt_text or not s.paragraphs:
                continue
            under = backdrop(slide, s)
            if not under:
                continue
            for p in s.paragraphs:
                if p.style.color and p.text.strip():
                    chars[(p.style.color, under)] += len(p.text.strip())
        total = sum(chars.values()) or 1
        for k, v in chars.items():
            pairs[k] += v / total
    grand = sum(pairs.values()) or 1
    ranked = sorted(pairs.items(), key=lambda kv: -kv[1])
    return [TextColorPair(text=t, backdrop=b, share=round(w / grand, 4)) for (t, b), w in ranked if w / grand >= 0.005]


# ── типографика ────────────────────────────────────────────────────────────


def _is_title(s: Shape) -> bool:
    return bool(s.placeholder and s.placeholder.type in ("title", "center_title", "ctrtitle"))


def _slide_title(slide: SlideRaw, slide_h: int) -> Shape | None:
    """Плейсхолдер заголовка, а без него — самый крупный текст в верхней четверти слайда."""
    ph = next((s for s in slide.shapes if _is_title(s)), None)
    if ph is not None:
        return ph
    top = [s for s in slide.shapes if s.kind == ShapeKind.text and s.paragraphs and s.text.strip()
           and s.bbox.y < slide_h * 0.25]
    return max(top, key=lambda s: s.paragraphs[0].style.size_pt or 0, default=None)


def _type_observations(slides: list[SlideRaw], slide_h: int):
    """(кегль с учётом autofit, вес, это заголовок, шрифт) — нормировано по слайду."""
    out = []
    for slide in slides:
        rows = []
        title = _slide_title(slide, slide_h)
        for s in slide.shapes:
            for p in s.paragraphs:
                if p.style.size_pt and p.text.strip():
                    size = round(p.style.size_pt * s.autofit_scale, 2)
                    rows.append((size, len(p.text.strip()), s is title, p.style.font))
        total = sum(r[1] for r in rows) or 1
        out += [(size, w / total, title, font) for size, w, title, font in rows]
    return out


def _type_scale(slides: list[SlideRaw], slide_h: int) -> list[TypeStep]:
    obs = _type_observations(slides, slide_h)
    if not obs:
        return []
    by_size: dict[float, list] = defaultdict(list)
    for o in obs:
        by_size[o[0]].append(o)

    clusters: list[list[float]] = []
    for size in sorted(by_size):
        if clusters and size / clusters[-1][0] < SIZE_CLUSTER_RATIO:
            clusters[-1].append(size)
        else:
            clusters.append([size])

    grand = sum(o[1] for o in obs)
    steps = []
    for cl in clusters:
        rows = [o for s in cl for o in by_size[s]]
        weight = sum(r[1] for r in rows)
        rep = max(cl, key=lambda s: sum(o[1] for o in by_size[s]))
        fonts: dict[str, float] = defaultdict(float)
        for r in rows:
            if r[3]:
                fonts[r[3]] += r[1] / weight
        title_share = sum(r[1] for r in rows if r[2]) / weight
        steps.append((rep, weight / grand, title_share, sorted(cl), dict(fonts)))

    # роли: body — самая «тяжёлая» ступень без заголовков; title — где больше всего заголовков
    body = max((s for s in steps if s[2] < 0.5), key=lambda s: s[1], default=steps[0])
    title = max(steps, key=lambda s: s[1] * s[2]) if any(s[2] > 0 for s in steps) else None
    result = []
    for rep, usage, _, sizes, fonts in steps:
        if title and rep == title[0]:
            role = "title"
        elif rep == body[0]:
            role = "body"
        elif rep < body[0]:
            role = "caption"
        elif title and rep > title[0]:
            role = "display"
        else:
            role = "heading"
        result.append(TypeStep(size_pt=rep, usage=round(usage, 4), role=role, sizes=sizes,
                               fonts={k: round(v, 3) for k, v in sorted(fonts.items(), key=lambda kv: -kv[1])}))
    return result


def _fonts(slides: list[SlideRaw], scale: list[TypeStep], slide_h: int) -> FontTokens:
    usage: dict[str, float] = defaultdict(float)
    heading: dict[str, float] = defaultdict(float)
    body: dict[str, float] = defaultdict(float)
    body_size = next((s.size_pt for s in scale if s.role == "body"), None)
    for size, w, title, font in _type_observations(slides, slide_h):
        if not font:
            continue
        usage[font] += w
        if title or (body_size and size > body_size * 1.3):
            heading[font] += w
        else:
            body[font] += w
    total = sum(usage.values()) or 1

    def top(d):
        return max(d, key=d.get) if d else None

    return FontTokens(heading=top(heading) or top(usage), body=top(body) or top(usage),
                      usage={k: round(v / total, 3) for k, v in sorted(usage.items(), key=lambda kv: -kv[1])})


# ── поля и направляющие ────────────────────────────────────────────────────


def _content_shapes(raw: TemplateRaw, slide: SlideRaw) -> list[Shape]:
    # пустые плейсхолдеры с подсказкой включаем: они показывают, куда дизайнер ставит контент
    return [
        s for s in slide.shapes
        if s.kind == ShapeKind.text and s.text.strip()
        and not is_bleed(s.bbox, raw.slide_w, raw.slide_h)
        and not (s.placeholder and s.placeholder.type in ("slide_number", "sldnum", "footer", "ftr", "date", "dt"))
    ]


def _snap(value: int, guides: list[int], tol: int) -> int:
    near = [g for g in guides if abs(g - value) <= tol]
    return min(near, key=lambda g: abs(g - value)) if near else value


def _margins(raw: TemplateRaw, slides: list[SlideRaw], gx: list[int], gy: list[int]) -> Box:
    lefts, tops, rights, bottoms = [], [], [], []
    for slide in slides:
        shapes = _content_shapes(raw, slide)
        if not shapes:
            continue
        lefts.append(min(s.bbox.x for s in shapes))
        tops.append(min(s.bbox.y for s in shapes))
        rights.append(raw.slide_w - max(s.bbox.right for s in shapes))
        bottoms.append(raw.slide_h - max(s.bbox.bottom for s in shapes))
    if not lefts:
        d = raw.slide_w // 20
        return Box(left=d, top=d, right=d, bottom=d)
    # края, до которых контент доходит на заметной доле слайдов (а не на каждом)
    tol = raw.slide_w // 100
    return Box(
        left=_snap(_pct(lefts, 0.2), gx, tol),
        top=_snap(_pct(tops, 0.2), gy, tol),
        right=raw.slide_w - _snap(raw.slide_w - _pct(rights, 0.2), gx, tol),
        bottom=raw.slide_h - _snap(raw.slide_h - _pct(bottoms, 0.2), gy, tol),
    )


def _pct(values: list[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * q), len(ordered) - 1)]


def _dedupe(values: list[int], tol: int) -> list[int]:
    out: list[int] = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def build_tokens(raw: TemplateRaw, slide_indices: list[int] | None = None) -> DesignTokens:
    """slide_indices — слайды-образцы (служебные исключены классификатором); None — все слайды."""
    slides = [s for s in raw.slides if slide_indices is None or s.index in slide_indices]
    tol = raw.slide_w // 200
    gx = _dedupe([g.pos for g in raw.guides if g.orient == "v"], tol)
    gy = _dedupe([g.pos for g in raw.guides if g.orient == "h"], tol)

    scale = _type_scale(slides, raw.slide_h)
    bgs: dict[tuple[str, str | None], list[int]] = defaultdict(list)
    for s in slides:
        bgs[(s.background.kind, s.background.color)].append(s.index)

    return DesignTokens(
        slide_w=raw.slide_w, slide_h=raw.slide_h,
        palette=_palette(raw, slides),
        text_pairs=_text_pairs(slides),
        fonts=_fonts(slides, scale, raw.slide_h),
        type_scale=scale,
        margins=_margins(raw, slides, gx, gy),
        guides_x=gx, guides_y=gy,
        backgrounds=[
            BackgroundVariant(kind=k, color=c, dark=bool(c and relative_luminance(c) < DARK_LUMINANCE), slides=idx)
            for (k, c), idx in sorted(bgs.items(), key=lambda kv: -len(kv[1]))
        ],
    )
