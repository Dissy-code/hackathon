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


def build_chat_model(r: ResolvedRole, **overrides) -> ChatOpenAI:
    params = r.model_dump(include={"temperature", "top_p", "top_k", "think", "max_tokens"}) | overrides
    return ChatOpenAI(
        model=r.model,
        base_url=r.provider.base_url,
        api_key=r.provider.api_key,
        timeout=r.provider.timeout_s,
        max_retries=r.provider.max_retries,
        temperature=params["temperature"],
        top_p=params["top_p"],
        max_tokens=params["max_tokens"],
        # Не-OpenAI параметры: top_k и переключатель размышлений в chat template (vLLM / llama.cpp / SGLang)
        extra_body={
            "top_k": params["top_k"],
            "chat_template_kwargs": {"enable_thinking": params["think"]},
        },
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
