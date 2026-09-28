from pathlib import Path

import pytest
from jinja2 import UndefinedError

from prism.config import load_config
from prism.skills.registry import SkillError, SkillRegistry


def write_skill(root: Path, name: str, version: int, user: str = "Hello {{ who }}") -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"v{version}.yaml").write_text(
        f"name: {name}\nversion: {version}\nrole: writer\nsystem: sys v{version}\nuser: '{user}'\n",
        encoding="utf-8",
    )


def test_latest_version_by_default(tmp_path):
    write_skill(tmp_path, "writer", 1)
    write_skill(tmp_path, "writer", 2)
    reg = SkillRegistry(tmp_path)
    assert reg.versions("writer") == [1, 2]
    assert reg.get("writer").version == 2


def test_pin_and_explicit_version(tmp_path):
    write_skill(tmp_path, "writer", 1)
    write_skill(tmp_path, "writer", 2)
    assert SkillRegistry(tmp_path, pins={"writer": 1}).get("writer").version == 1
    assert SkillRegistry(tmp_path, pins={"writer": 1}).get("writer", 2).version == 2


def test_missing_version(tmp_path):
    write_skill(tmp_path, "writer", 1)
    with pytest.raises(SkillError, match="v3"):
        SkillRegistry(tmp_path).get("writer", 3)


def test_name_mismatch_is_rejected(tmp_path):
    write_skill(tmp_path, "writer", 1)
    (tmp_path / "writer" / "v1.yaml").rename(tmp_path / "writer" / "v5.yaml")
    with pytest.raises(SkillError, match="не совпадают"):
        SkillRegistry(tmp_path).get("writer", 5)


def test_render_is_strict(tmp_path):
    write_skill(tmp_path, "writer", 1)
    skill = SkillRegistry(tmp_path).get("writer")
    sys_msg, user_msg = skill.render(who="мир")
    assert (sys_msg.content, user_msg.content) == ("sys v1", "Hello мир")
    with pytest.raises(UndefinedError):
        skill.render()


def test_fingerprint_changes_with_content(tmp_path):
    write_skill(tmp_path, "writer", 1)
    before = SkillRegistry(tmp_path).get("writer").manifest_entry()
    write_skill(tmp_path, "writer", 1, user="Hi {{ who }}")
    after = SkillRegistry(tmp_path).get("writer").manifest_entry()
    assert before["skill"] == after["skill"] == "writer@v1"
    assert before["sha256"] != after["sha256"]


def test_repo_skills_load_and_reference_known_roles():
    cfg = load_config(env={"LLM_BASE_URL": "x", "LLM_API_KEY": "y", "LLM_MODEL": "m"})
    reg = SkillRegistry()
    for name in reg.names():
        skill = reg.get(name)
        assert skill.role in cfg.roles, f"{skill.ref}: роль {skill.role!r} не описана в конфиге"
