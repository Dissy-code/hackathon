"""Фабрика чат-моделей по ролям. Код пайплайна не знает, какая модель стоит за ролью."""

from __future__ import annotations

import re

from langchain_openai import ChatOpenAI

from prism.config import AppConfig, ResolvedRole

_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def strip_think(text: str) -> str:
    """Убирает блок размышлений, если сервер вернул его внутри content, а не в reasoning_content."""
    text = _THINK_BLOCK.sub("", text)
    # незакрытый <think> — ответ оборван на размышлениях, полезного текста нет
    if "<think>" in text:
        text = text.split("<think>", 1)[0]
    return text.strip()


def _extra_body(style: str, top_k: int, think: bool) -> dict:
    """Нестандартные параметры — у каждого семейства серверов свои."""
    if style == "vllm":
        return {"top_k": top_k, "chat_template_kwargs": {"enable_thinking": think}}
    if style == "openrouter":
        # exclude: не присылать сами рассуждения — нам нужен только ответ
        return {"reasoning": {"enabled": think, "exclude": True}}
    return {}


def build_chat_model(r: ResolvedRole, **overrides) -> ChatOpenAI:
    params = r.model_dump(include={"temperature", "top_p", "top_k", "think", "max_tokens"}) | overrides
    return ChatOpenAI(
        model=r.model,
        base_url=r.llm.base_url,
        api_key=r.llm.api_key,
        timeout=r.llm.timeout_s,
        max_retries=r.llm.max_retries,
        temperature=params["temperature"],
        top_p=params["top_p"],
        max_tokens=params["max_tokens"],
        extra_body=_extra_body(r.llm.api_style, params["top_k"], params["think"]),
        # потоком: соединение не простаивает, и шлюз провайдера не рвёт долгую генерацию по таймауту
        streaming=True,
        stream_usage=True,
    )


class LLMFactory:
    def __init__(self, config: AppConfig):
        self.config = config
        self._cache: dict[str, ChatOpenAI] = {}

    def resolve(self, role: str) -> ResolvedRole:
        return self.config.resolve_role(role)

    def for_role(self, role: str, **overrides) -> ChatOpenAI:
        if overrides:
            return build_chat_model(self.resolve(role), **overrides)
        if role not in self._cache:
            self._cache[role] = build_chat_model(self.resolve(role))
        return self._cache[role]
