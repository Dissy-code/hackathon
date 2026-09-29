"""Проверка содержимого слайда до вёрстки: полнота по типу, длина текстов, согласованность данных графика.

Детерминированная: один и тот же слайд — один и тот же результат. Замечания уходят модели на исправление,
а если и после этого слайд неполный — тип понижается до того, что содержимое реально выдерживает.
"""

from __future__ import annotations

import re

from prism.planning.schemas import Item, SlideContent, SlideKind

# сколько пунктов нужно типу слайда (мин, макс)
ITEMS = {
    SlideKind.agenda: (3, 6), SlideKind.cards: (2, 6), SlideKind.two_column: (2, 2),
    SlideKind.comparison: (2, 4), SlideKind.bullets: (2, 6), SlideKind.process: (3, 6),
    SlideKind.timeline: (3, 6), SlideKind.kpi: (2, 4), SlideKind.team: (2, 8),
}
MAX_HEADING_WORDS = 5
MAX_TEXT_WORDS = 18
MAX_TITLE_WORDS = 14


# «3 200 заказов за 6 недель» -> число «3 200» + подпись «заказов за 6 недель»
_VALUE = re.compile(
    r"^(?P<num>[<>~≈+\-−]?\s*\d[\d\s\u00a0.,]*\s*(?:%|₽|\$|€|x|×|раз[а]?|млн|млрд|тыс\.?|трлн|мин(?:ут[аы]?)?|ч(?:ас(?:а|ов)?)?|сек|дн(?:ей|я)?|лет|год(?:а)?)?"
    r"(?:\s*(?:₽|руб\.?))?)\s+(?P<rest>\S.*)$",
    re.IGNORECASE,
)
MAX_VALUE_CHARS = 14


def normalize(c: SlideContent) -> SlideContent:
    """Детерминированные поправки до проверки: длинное «число» делится на число и подпись."""
    items = []
    for it in c.items:
        if it.value and len(it.value) > MAX_VALUE_CHARS:
            m = _VALUE.match(it.value.strip())
            if m:
                rest = m.group("rest").strip()
                if not it.text:
                    text = rest
                elif rest.lower() in it.text.lower():
                    text = it.text
                else:                       # «выручка» + «за год» -> «выручка за год»
                    text = f"{rest} {it.text}" if len(rest.split()) <= 3 else f"{rest}. {it.text}"
                it = Item(heading=it.heading, value=m.group("num").strip(), text=text)
        elif c.kind == SlideKind.kpi and not it.value and it.text:
            # «14 млрд ₽ в 2025 году» в подписи -> число в value, подпись — заголовок пункта + остаток
            m = _VALUE.match(it.text.strip())
            if m:
                rest = m.group("rest").strip()
                it = Item(heading=None, value=m.group("num").strip(),
                          text=f"{it.heading} {rest}".strip() if it.heading else rest)
        items.append(it)
    return c.model_copy(update={"items": items})


def _words(text: str | None) -> int:
    return len(text.split()) if text else 0


def content_problems(c: SlideContent) -> list[str]:
    """Замечания по-русски, пригодные, чтобы вернуть их модели как есть."""
    p: list[str] = []
    if _words(c.title) > MAX_TITLE_WORDS:
        p.append(f"заголовок длиннее {MAX_TITLE_WORDS} слов — сократи")
    need = ITEMS.get(c.kind)
    if need:
        lo, hi = need
        if not lo <= len(c.items) <= hi:
            p.append(f"для типа {c.kind.value} нужно {lo}–{hi} пунктов, сейчас {len(c.items)}")
    for i, it in enumerate(c.items, 1):
        if c.kind == SlideKind.kpi and not it.value:
            p.append(f"пункт {i}: у показателя нет числа (value)")
        if c.kind == SlideKind.kpi and not it.text:
            p.append(f"пункт {i}: у показателя нет подписи (text) — что именно измеряет число")
        if it.value and len(it.value) > MAX_VALUE_CHARS:
            p.append(f"пункт {i}: value должно быть только числом с единицей («38%», «3 200»), пояснение — в text")
        if c.kind == SlideKind.timeline and not (it.value and it.text):
            p.append(f"пункт {i}: у точки таймлайна нужны и дата/период (value), и текст (text)")
        if c.kind in (SlideKind.cards, SlideKind.process, SlideKind.team) and not (it.heading or it.text):
            p.append(f"пункт {i}: пустой — нужен заголовок или текст")
        if _words(it.heading) > MAX_HEADING_WORDS:
            p.append(f"пункт {i}: заголовок длиннее {MAX_HEADING_WORDS} слов")
        if _words(it.text) > MAX_TEXT_WORDS:
            p.append(f"пункт {i}: текст длиннее {MAX_TEXT_WORDS} слов — сократи")
    if c.kind == SlideKind.chart:
        ch = c.chart
        if ch is None or not ch.categories or not ch.series:
            p.append("для графика нужен объект chart с категориями и хотя бы одной серией из цифр брифа")
        elif any(len(s.values) != len(ch.categories) for s in ch.series):
            p.append("в каждой серии графика должно быть столько значений, сколько категорий")
    if c.kind == SlideKind.table and (c.table is None or not c.table.rows):
        p.append("для таблицы нужен объект table с колонками и строками из данных брифа")
    if c.kind == SlideKind.quote and not c.quote:
        p.append("для цитаты нужен текст цитаты (quote)")
    return p


def degrade(c: SlideContent) -> SlideContent:
    """Последний рубеж: тип под то, что в содержимом реально есть (иначе вёрстка покажет пустые образцы)."""
    kind = c.kind
    if kind == SlideKind.chart and (c.chart is None or not c.chart.series):
        kind = SlideKind.table if c.table else (SlideKind.cards if len(c.items) >= 2 else SlideKind.section)
    elif kind == SlideKind.table and (c.table is None or not c.table.rows) or kind == SlideKind.kpi and sum(1 for i in c.items if i.value) < 2:
        kind = SlideKind.cards if len(c.items) >= 2 else SlideKind.section
    elif kind == SlideKind.timeline and sum(1 for i in c.items if i.value) < 2:
        kind = SlideKind.process if len(c.items) >= 3 else (SlideKind.bullets if c.items else SlideKind.section)
    elif kind in (SlideKind.team, SlideKind.cards, SlideKind.process, SlideKind.agenda) and len(c.items) < 2 or kind == SlideKind.quote and not c.quote:
        kind = SlideKind.section
    # пустые пункты не нужны ни одному типу
    items = [i for i in c.items if i.heading or i.text or i.value]
    return c.model_copy(update={"kind": kind, "items": items})
