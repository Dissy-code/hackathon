"""Подставная модель для разработки и тестов: LLM_FAKE=1 или напрямую в тестах.

Отвечает заготовленной колодой по первой фразе брифа — без сети. Нужна, чтобы проверять граф,
вёрстку, рендер и интерфейс, когда провайдер недоступен. В /api/health такой режим виден явно.
"""

from __future__ import annotations

import json
import re

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage


def _topic(brief: str) -> str:
    first = re.split(r"[.!?\n:]", brief.strip(), maxsplit=1)[0].strip()
    return first[:80] or "Новая инициатива"


def outline(brief: str) -> dict:
    t = _topic(brief)
    return {
        "title": t, "language": "ru", "audience": "руководители", "storyline": "Проблема, решение, план, запрос.",
        "slides": [
            {"kind": "title", "title": t, "key_message": "О чём колода", "content_brief": "титул"},
            {"kind": "cards", "title": "Три причины действовать сейчас", "key_message": "Окно возможностей",
             "content_brief": "три карточки"},
            {"kind": "kpi", "title": "Пилот подтвердил спрос", "key_message": "Цифры пилота",
             "content_brief": "три показателя"},
            {"kind": "process", "title": "Запуск проходит за четыре шага", "key_message": "Понятный план",
             "content_brief": "четыре шага"},
            {"kind": "chart", "title": "Рынок растёт быстрее конкурентов", "key_message": "Рост",
             "content_brief": "столбчатый график"},
            {"kind": "closing", "title": "Готовы начать в этом квартале", "key_message": "Следующий шаг",
             "content_brief": "призыв"},
        ],
    }


def slides(brief: str) -> dict:
    t = _topic(brief)
    return {"slides": [
        {"kind": "title", "title": t, "subtitle": "Демо-режим: текст подставной, вёрстка настоящая"},
        {"kind": "cards", "title": "Три причины действовать сейчас", "items": [
            {"heading": "Спрос", "text": "Клиенты просят решение уже сегодня"},
            {"heading": "Команда", "text": "Есть люди и экспертиза для запуска"},
            {"heading": "Окно", "text": "Конкуренты ещё не заняли нишу"}]},
        {"kind": "kpi", "title": "Пилот подтвердил спрос", "items": [
            {"value": "3 200", "text": "заказов за шесть недель"},
            {"value": "38%", "text": "повторных покупок"},
            {"value": "1 150 ₽", "text": "средний чек"}]},
        {"kind": "process", "title": "Запуск проходит за четыре шага", "items": [
            {"heading": "Партнёры", "text": "Подключаем первых клиентов"},
            {"heading": "Команда", "text": "Нанимаем и обучаем"},
            {"heading": "Маркетинг", "text": "Первые кампании"},
            {"heading": "Масштаб", "text": "Выходим в новые регионы"}]},
        {"kind": "chart", "title": "Рынок растёт быстрее конкурентов", "subtitle": "Объём рынка, млрд ₽",
         "chart": {"type": "column", "categories": ["2023", "2024", "2025", "2026"],
                   "series": [{"name": "Рынок", "values": [9.6, 11.6, 14.0, 16.9]}], "unit": "млрд ₽"}},
        {"kind": "closing", "title": "Готовы начать в этом квартале", "subtitle": "Следующий шаг — встреча"},
    ]}


class _Model(GenericFakeChatModel):
    def bind(self, **kwargs):            # response_format и прочее игнорируем
        return self


class FakeLLMFactory:
    """Совместима с LLMFactory в том, что использует пайплайн: for_role() и resolve()."""

    fake = True

    def __init__(self, brief: str = ""):
        self.brief = brief

    def for_role(self, role: str, **_):
        # бриф берём из последнего сообщения при вызове — модели GenericFake это не нужно, поэтому
        # отвечаем по теме, заданной при создании (пайплайн пересоздаёт фабрику на задачу)
        data = outline(self.brief) if role == "outline_planner" else slides(self.brief)
        return _Model(messages=iter([AIMessage(json.dumps(data, ensure_ascii=False))] * 3))

    def resolve(self, role: str):
        return type("Resolved", (), {"model": "fake"})()
