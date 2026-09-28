from pathlib import Path

import pytest

from prism.config import ConfigError, load_config

CONFIG = """
profile: ${PRISM_PROFILE:-dev}
providers:
  owui: { base_url: "${OWUI_BASE_URL}", api_key: "${OWUI_API_KEY}" }
  vk:   { base_url: "${VK_LLM_BASE_URL:-}", api_key: "${VK_LLM_API_KEY:-}" }
profiles:
  dev:
    models:
      text:   { provider: owui, name: small-a3b }
      vision: { provider: owui, name: small-a3b }
  final:
    models:
      text: { provider: vk, name: big-27b }
role_defaults: { temperature: 0.6, top_p: 0.95, top_k: 20, think: false, max_tokens: 1000 }
roles:
  planner: { model: text, think: true, max_tokens: 9000 }
  audit:   { model: vision, temperature: 0.1 }
"""

ENV = {"OWUI_BASE_URL": "http://owui/api", "OWUI_API_KEY": "sk-test"}


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(CONFIG, encoding="utf-8")
    return p


def test_env_interpolation_and_defaults(cfg_path):
    cfg = load_config(cfg_path, env=ENV)
    assert cfg.profile == "dev"
    assert cfg.providers["owui"].base_url == "http://owui/api"
    assert cfg.providers["vk"].base_url == ""


def test_missing_required_env_var_is_reported(cfg_path):
    with pytest.raises(ConfigError, match="OWUI_API_KEY"):
        load_config(cfg_path, env={"OWUI_BASE_URL": "x"})


def test_role_overrides_defaults(cfg_path):
    r = load_config(cfg_path, env=ENV).resolve_role("planner")
    assert (r.model, r.think, r.max_tokens, r.temperature, r.top_k) == ("small-a3b", True, 9000, 0.6, 20)
    assert r.provider.api_key == "sk-test"


def test_profile_switch_changes_model_not_role(cfg_path):
    env = ENV | {"VK_LLM_BASE_URL": "http://vk/v1", "VK_LLM_API_KEY": "k"}
    r = load_config(cfg_path, env=env, profile="final").resolve_role("planner")
    assert (r.model, r.provider.base_url, r.think) == ("big-27b", "http://vk/v1", True)


def test_role_pointing_to_missing_model_slot(cfg_path):
    env = ENV | {"VK_LLM_BASE_URL": "http://vk/v1"}
    cfg = load_config(cfg_path, env=env, profile="final")
    with pytest.raises(ConfigError, match="vision"):
        cfg.resolve_role("audit")


def test_provider_without_base_url_is_rejected_on_use(cfg_path):
    cfg = load_config(cfg_path, env=ENV, profile="final")
    with pytest.raises(ConfigError, match="base_url"):
        cfg.resolve_role("planner")


def test_unknown_profile(cfg_path):
    with pytest.raises(ValueError, match="nope"):
        load_config(cfg_path, env=ENV, profile="nope")


def test_repo_default_config_is_valid():
    env = ENV | {"PRISM_PROFILE": "dev"}
    cfg = load_config(env=env)
    for role in cfg.roles:
        cfg.resolve_role(role)
