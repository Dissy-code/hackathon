"""Загрузка конфига: YAML + подстановка ${VAR} / ${VAR:-default} из окружения."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = BACKEND_DIR / "configs" / "default.yaml"

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(ValueError):
    pass


def _interpolate(value: Any, env: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {k: _interpolate(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v, env) for v in value]
    if not isinstance(value, str):
        return value

    def repl(m: re.Match[str]) -> str:
        name, default = m.group(1), m.group(2)
        if name in env:
            return env[name]
        if default is not None:
            return default
        raise ConfigError(f"переменная окружения {name} не задана (см. backend/.env.example)")

    return _VAR.sub(repl, value)


class LLMConfig(BaseModel):
    """Единственный провайдер модели: OpenAI-совместимый API."""

    base_url: str
    api_key: str
    api_style: Literal["vllm", "openrouter", "plain"] = "vllm"
    timeout_s: float = 180
    max_retries: int = 2
    models: dict[str, str]                 # слот (text, vision) -> имя модели у провайдера

    def model(self, slot: str) -> str | None:
        name = self.models.get(slot) or None
        if name is None and slot != "text":
            return self.model("text")      # пустой vision — та же модель, что text
        return name


class RoleParams(BaseModel):
    """Параметры генерации роли. None — наследуется из role_defaults."""

    model: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    think: bool | None = None
    max_tokens: int | None = None


class ResolvedRole(BaseModel):
    """Роль, развёрнутая до конкретной модели."""

    role: str
    model: str
    llm: LLMConfig
    temperature: float
    top_p: float
    top_k: int
    think: bool
    max_tokens: int


class ResearchConfig(BaseModel):
    """Поиск в интернете для коротких брифов (узел research графа, инструменты MCP-сервера web)."""

    min_brief_words: int = 60      # бриф короче и почти без чисел — модель ищет материалы сама
    max_queries: int = 3
    max_pages: int = 4
    page_chars: int = 6000         # сколько текста страницы отдавать модели
    budget_s: float = 60           # весь поиск; не успели — генерация идёт по брифу


class AppConfig(BaseModel):
    llm: LLMConfig
    role_defaults: RoleParams = Field(default_factory=RoleParams)
    roles: dict[str, RoleParams]
    skills: dict[str, int] = Field(default_factory=dict)
    mcp: dict[str, str] = Field(default_factory=dict)       # имя сервера -> URL (streamable HTTP); пусто — выкл.
    research: ResearchConfig = Field(default_factory=ResearchConfig)

    def resolve_role(self, role: str) -> ResolvedRole:
        if role not in self.roles:
            raise ConfigError(f"роль {role!r} не описана в конфиге; есть: {sorted(self.roles)}")
        merged = self.role_defaults.model_dump() | {
            k: v for k, v in self.roles[role].model_dump().items() if v is not None
        }
        missing = [k for k, v in merged.items() if v is None]
        if missing:
            raise ConfigError(f"роль {role!r}: не заданы {missing} (ни в роли, ни в role_defaults)")

        slot = merged.pop("model")
        name = self.llm.model(slot)
        if not name:
            raise ConfigError(f"роль {role!r}: не задана модель для слота {slot!r} (LLM_MODEL в .env)")
        if not self.llm.base_url:
            raise ConfigError("не задан LLM_BASE_URL")
        return ResolvedRole(role=role, model=name, llm=self.llm, **merged)


def load_config(
    path: Path | str | None = None,
    *,
    env: dict[str, str] | None = None,
    dotenv_path: Path | str | None = BACKEND_DIR / ".env",
) -> AppConfig:
    if env is None:
        if dotenv_path:
            load_dotenv(dotenv_path, override=False)
        env = dict(os.environ)
    raw = yaml.safe_load(Path(path or DEFAULT_CONFIG).read_text(encoding="utf-8"))
    return AppConfig.model_validate(_interpolate(raw, env))
