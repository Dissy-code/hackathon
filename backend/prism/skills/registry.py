"""Реестр скиллов: промпты и параметры агентов лежат в skills/<name>/v<N>.yaml, а не в коде.

Файл скилла:
    name: outline_planner
    version: 1
    role: outline_planner          # роль из configs/*.yaml -> модель и параметры генерации
    description: ...
    system: |                      # Jinja2
      ...
    user: |                        # Jinja2
      ...
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, StrictUndefined
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, PrivateAttr

from prism.config import BACKEND_DIR

DEFAULT_SKILLS_DIR = BACKEND_DIR / "skills"
_VERSION_FILE = re.compile(r"^v(\d+)\.ya?ml$")
_jinja = Environment(undefined=StrictUndefined, keep_trailing_newline=False, autoescape=False)


class SkillError(LookupError):
    pass


class Skill(BaseModel):
    name: str
    version: int
    role: str
    description: str = ""
    system: str
    user: str
    sha256: str
    _source: Path = PrivateAttr()

    @property
    def ref(self) -> str:
        return f"{self.name}@v{self.version}"

    def render(self, **variables: Any) -> list[BaseMessage]:
        return [
            SystemMessage(_jinja.from_string(self.system).render(**variables).strip()),
            HumanMessage(_jinja.from_string(self.user).render(**variables).strip()),
        ]

    def manifest_entry(self) -> dict[str, Any]:
        return {"skill": self.ref, "role": self.role, "sha256": self.sha256}


def _load_file(path: Path, expected_name: str, expected_version: int) -> Skill:
    raw_bytes = path.read_bytes()
    data = yaml.safe_load(raw_bytes)
    if data.get("name") != expected_name or data.get("version") != expected_version:
        raise SkillError(
            f"{path}: name/version в файле ({data.get('name')}@v{data.get('version')}) "
            f"не совпадают с путём ({expected_name}@v{expected_version})"
        )
    skill = Skill(**data, sha256=hashlib.sha256(raw_bytes).hexdigest())
    skill._source = path
    return skill


class SkillRegistry:
    def __init__(self, root: Path = DEFAULT_SKILLS_DIR, pins: dict[str, int] | None = None):
        self.root = root
        self.pins = pins or {}

    def versions(self, name: str) -> list[int]:
        d = self.root / name
        if not d.is_dir():
            raise SkillError(f"скилл {name!r} не найден в {self.root}")
        return sorted(int(m.group(1)) for p in d.iterdir() if (m := _VERSION_FILE.match(p.name)))

    def names(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and not p.name.startswith("_"))

    def get(self, name: str, version: int | None = None) -> Skill:
        available = self.versions(name)
        version = version or self.pins.get(name) or (available[-1] if available else None)
        if version not in available:
            raise SkillError(f"{name}@v{version} не найден; доступны версии {available}")
        path = next(self.root.joinpath(name).glob(f"v{version}.y*ml"))
        return _load_file(path, name, version)
