"""Загрузка конфига: YAML + подстановка ${VAR} / ${VAR:-default} из окружения."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

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


class ProviderConfig(BaseModel):
    base_url: str
    api_key: str
    timeout_s: float = 180
    max_retries: int = 2


class ModelRef(BaseModel):
    provider: str
    name: str


class Profile(BaseModel):
    models: dict[str, ModelRef]


class RoleParams(BaseModel):
    """Параметры генерации роли. None — наследуется из role_defaults."""

    model: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    think: bool | None = None
    max_tokens: int | None = None


class ResolvedRole(BaseModel):
    """Роль, развёрнутая до конкретной модели и провайдера активного профиля."""

    role: str
    model: str
    provider_name: str
    provider: ProviderConfig
    temperature: float
    top_p: float
    top_k: int
    think: bool
    max_tokens: int


class AppConfig(BaseModel):
    profile: str
    providers: dict[str, ProviderConfig]
    profiles: dict[str, Profile]
    role_defaults: RoleParams = Field(default_factory=RoleParams)
    roles: dict[str, RoleParams]
    skills: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_refs(self) -> AppConfig:
        if self.profile not in self.profiles:
            raise ConfigError(f"профиль {self.profile!r} не описан; есть: {sorted(self.profiles)}")
        for pname, prof in self.profiles.items():
            for slot, ref in prof.models.items():
                if ref.provider not in self.providers:
                    raise ConfigError(f"profiles.{pname}.models.{slot}: неизвестный провайдер {ref.provider!r}")
        return self

    @property
    def active(self) -> Profile:
        return self.profiles[self.profile]

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
        if slot not in self.active.models:
            raise ConfigError(f"роль {role!r} ссылается на модель {slot!r}, которой нет в профиле {self.profile!r}")
        ref = self.active.models[slot]
        provider = self.providers[ref.provider]
        if not provider.base_url:
            raise ConfigError(f"провайдер {ref.provider!r} (профиль {self.profile!r}) без base_url")
        return ResolvedRole(role=role, model=ref.name, provider_name=ref.provider, provider=provider, **merged)


def load_config(
    path: Path | str | None = None,
    *,
    profile: str | None = None,
    env: dict[str, str] | None = None,
    dotenv_path: Path | str | None = BACKEND_DIR / ".env",
) -> AppConfig:
    if env is None:
        if dotenv_path:
            load_dotenv(dotenv_path, override=False)
        env = dict(os.environ)
    raw = yaml.safe_load(Path(path or DEFAULT_CONFIG).read_text(encoding="utf-8"))
    data = _interpolate(raw, env)
    if profile:
        data["profile"] = profile
    return AppConfig.model_validate(data)
