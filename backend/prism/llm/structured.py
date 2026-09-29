"""Структурированный вывод с обратной связью: невалидный ответ возвращается модели вместе с ошибкой."""

from __future__ import annotations

import json

import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, ValidationError

from prism.llm.client import strip_think

# (base_url, model), для которых сервер отверг response_format; дальше сразу идём без него
_NO_RESPONSE_FORMAT: set[tuple[str, str]] = set()


def _model_key(model: BaseChatModel) -> tuple[str, str]:
    return (str(getattr(model, "openai_api_base", "")), str(getattr(model, "model_name", "")))


class StructuredOutputError(RuntimeError):
    def __init__(self, schema: type[BaseModel], attempts: int, last_error: str, last_raw: str):
        super().__init__(f"{schema.__name__}: не удалось получить валидный ответ за {attempts} попыток: {last_error}")
        self.last_raw = last_raw


class ProviderError(RuntimeError):
    """Провайдер отказал по ключу: неверный ключ, нет доступа, кончился баланс или лимит трат.
    Повторять бессмысленно — генерация останавливается с понятным сообщением."""


_PROVIDER_REASONS = {
    401: "провайдер модели не принял ключ (LLM_API_KEY) — проверьте ключ в backend/.env",
    402: "у ключа провайдера модели закончился баланс или лимит трат — пополните баланс или поднимите лимит",
    403: "у ключа нет доступа к модели — проверьте LLM_MODEL и права ключа",
}


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

    use_json_schema=True — схема уходит в response_format (constrained decoding на стороне сервера);
    если сервер его отвергает, модель запоминается и дальше используется схема в тексте запроса.
    False — сразу схема в тексте запроса, без response_format.
    """
    if use_json_schema and _model_key(model) in _NO_RESPONSE_FORMAT:
        use_json_schema = False
    if use_json_schema:
        bound = model.bind(
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
            }
        )
    else:
        bound = model   # без response_format: не все провайдеры его принимают, схема — в тексте запроса
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
        try:
            reply = await bound.ainvoke(history)
        except openai.LengthFinishReasonError:
            # модель упёрлась в лимит токенов (обычно — зациклилась на повторе): просим компактно ещё раз
            last_error = "ответ оборван: превышен лимит длины"
            history = [*messages, HumanMessage(
                "Предыдущий ответ был слишком длинным и оборвался. Верни компактный JSON строго по схеме, "
                "без повторов и лишних полей.")]
            continue
        except openai.BadRequestError as e:
            if not use_json_schema or "response_format" not in str(e):
                raise StructuredOutputError(schema, 1, f"провайдер отклонил запрос: {e}", "") from e
            # провайдер не поддерживает response_format — запоминаем и повторяем со схемой в промпте
            _NO_RESPONSE_FORMAT.add(_model_key(model))
            return await ainvoke_structured(model, messages, schema, retries=retries, use_json_schema=False)
        except openai.APIStatusError as e:
            if e.status_code in _PROVIDER_REASONS:
                raise ProviderError(f"{_PROVIDER_REASONS[e.status_code]} (HTTP {e.status_code})") from e
            last_error = f"{type(e).__name__}: {e}"
            continue
        except (openai.APIError, TimeoutError) as e:
            # сеть, 5xx, таймаут: для вызывающего это такая же неудача, как невалидный ответ
            last_error = f"{type(e).__name__}: {e}"
            continue
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
