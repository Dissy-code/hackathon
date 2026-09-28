from pathlib import Path

import pytest

from prism.config import ConfigError, load_config

CONFIG = """
llm:
  base_url: "${LLM_BASE_URL}"
  api_key: "${LLM_API_KEY}"
  models:
    text: "${LLM_MODEL}"
    vision: "${LLM_VISION_MODEL:-}"
role_defaults: { temperature: 0.6, top_p: 0.95, top_k: 20, think: false, max_tokens: 1000 }
roles:
  planner: { model: text, think: true, max_tokens: 9000 }
  audit:   { model: vision, temperature: 0.1 }
"""

ENV = {"LLM_BASE_URL": "http://llm/v1", "LLM_API_KEY": "sk-test", "LLM_MODEL": "small-a3b"}


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(CONFIG, encoding="utf-8")
    return p


def test_env_interpolation(cfg_path):
    cfg = load_config(cfg_path, env=ENV)
    assert (cfg.llm.base_url, cfg.llm.api_key) == ("http://llm/v1", "sk-test")


def test_missing_required_env_var_is_reported(cfg_path):
    with pytest.raises(ConfigError, match="LLM_API_KEY"):
        load_config(cfg_path, env={"LLM_BASE_URL": "x", "LLM_MODEL": "m"})


def test_role_overrides_defaults(cfg_path):
    r = load_config(cfg_path, env=ENV).resolve_role("planner")
    assert (r.model, r.think, r.max_tokens, r.temperature, r.top_k) == ("small-a3b", True, 9000, 0.6, 20)
    assert r.llm.api_key == "sk-test"


def test_empty_vision_model_falls_back_to_text(cfg_path):
    assert load_config(cfg_path, env=ENV).resolve_role("audit").model == "small-a3b"
    env = ENV | {"LLM_VISION_MODEL": "vlm-8b"}
    assert load_config(cfg_path, env=env).resolve_role("audit").model == "vlm-8b"


def test_switching_model_is_env_only(cfg_path):
    env = ENV | {"LLM_BASE_URL": "https://other/v1", "LLM_MODEL": "qwen-27b"}
    r = load_config(cfg_path, env=env).resolve_role("planner")
    assert (r.model, r.llm.base_url, r.think) == ("qwen-27b", "https://other/v1", True)


def test_unknown_role(cfg_path):
    with pytest.raises(ConfigError, match="nope"):
        load_config(cfg_path, env=ENV).resolve_role("nope")


def test_repo_default_config_is_valid():
    cfg = load_config(env=ENV)
    for role in cfg.roles:
        cfg.resolve_role(role)
