"""Структурированный вывод с обратной связью: невалидный ответ возвращается модели вместе с ошибкой."""

from __future__ import annotations

import json

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, ValidationError

from prism.llm.client import strip_think


class StructuredOutputError(RuntimeError):
    def __init__(self, schema: type[BaseModel], attempts: int, last_error: str, last_raw: str):
        super().__init__(f"{schema.__name__}: не удалось получить валидный ответ за {attempts} попыток: {last_error}")
        self.last_raw = last_raw


def _extract_json(text: str) -> str:
    text = strip_think(text)
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    return text[start:].strip() if start > 0 else text.strip()


async def ainvoke_structured[T: BaseModel](
    model: BaseChatModel,
    messages: list[BaseMessage],
    schema: type[T],
    *,
    retries: int = 2,
    use_json_schema: bool = True,
) -> T:
    """Запрашивает ответ по схеме; при ошибке валидации дописывает её в диалог и пробует снова.

    use_json_schema=True — схема уходит в response_format (constrained decoding на стороне сервера).
    False — только JSON-режим и схема в тексте запроса, для провайдеров без поддержки json_schema.
    """
    if use_json_schema:
        bound = model.bind(
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
            }
        )
    else:
        bound = model.bind(response_format={"type": "json_object"})
        messages = [
            *messages,
            HumanMessage(
                "Ответь только JSON-объектом по этой JSON Schema, без пояснений:\n"
                + json.dumps(schema.model_json_schema(), ensure_ascii=False)
            ),
        ]

    history = list(messages)
    last_error, raw = "", ""
    for _ in range(retries + 1):
        reply = await bound.ainvoke(history)
        raw = reply.content if isinstance(reply.content, str) else json.dumps(reply.content, ensure_ascii=False)
        try:
            return schema.model_validate_json(_extract_json(raw))
        except ValidationError as e:
            last_error = e.json(include_url=False)
        history += [
            AIMessage(raw),
            HumanMessage(f"Ответ не прошёл валидацию схемы {schema.__name__}:\n{last_error}\nИсправь и верни весь JSON заново."),
        ]
    raise StructuredOutputError(schema, retries + 1, last_error, raw)
