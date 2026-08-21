import pathlib

import pytest
import yaml

from research_harness.config.loader import load_config, load_config_from_dict
from research_harness.kernel.errors import ConfigurationError


def test_valid_config():
    cfg = load_config_from_dict(
        {
            "runtime": {"autonomy": "high"},
            "plugins": ["model.openrouter"],
            "models": {"roles": {"fast": {"provider": "openrouter", "model": "m1"}}},
            "session": {"root": ".research/sessions"},
            "loop": {"max_steps": 5},
        }
    )
    assert cfg.runtime.autonomy == "high"
    assert cfg.loop.max_steps == 5


def test_defaults():
    cfg = load_config_from_dict({})
    assert cfg.runtime.autonomy == "high"
    assert cfg.session.root == ".research/sessions"
    assert cfg.loop.max_steps == 8


def test_malformed_yaml(tmp_path: pathlib.Path):
    p = tmp_path / "bad.yaml"
    p.write_text("a: [unclosed\n")
    with pytest.raises(ConfigurationError, match="failed to parse"):
        load_config(p)


def test_missing_file():
    with pytest.raises(ConfigurationError, match="not found"):
        load_config("/nonexistent/path.yaml")


def test_invalid_autonomy():
    with pytest.raises(ConfigurationError):
        load_config_from_dict({"runtime": {"autonomy": "bad"}})


def test_invalid_max_steps():
    with pytest.raises(ConfigurationError):
        load_config_from_dict({"loop": {"max_steps": 0}})
    with pytest.raises(ConfigurationError):
        load_config_from_dict({"loop": {"max_steps": 101}})


def test_duplicate_plugins():
    with pytest.raises(ConfigurationError):
        load_config_from_dict({"plugins": ["a", "a"]})


def test_extra_fields_forbidden():
    with pytest.raises(ConfigurationError):
        load_config_from_dict({"unknown_field": 123})


def test_yaml_file_loading(tmp_path: pathlib.Path):
    data = {
        "runtime": {"autonomy": "interactive"},
        "plugins": ["model.openrouter", "tool.echo"],
        "models": {"roles": {"fast": {"provider": "openrouter", "model": "x"}}},
        "loop": {"max_steps": 3},
    }
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(data))
    cfg = load_config(p)
    assert cfg.runtime.autonomy == "interactive"
    assert len(cfg.plugins) == 2


def test_env_secret_not_in_config():
    # Ensure config does not contain api keys
    cfg = load_config_from_dict(
        {
            "models": {"roles": {"fast": {"provider": "openrouter", "model": "m"}}},
        }
    )
    dumped = cfg.model_dump()
    assert "OPENROUTER_API_KEY" not in str(dumped)
    assert "api_key" not in str(dumped).lower()


def test_non_dict_top_level(tmp_path: pathlib.Path):
    p = tmp_path / "bad.yaml"
    p.write_text("- item1\n- item2\n")
    with pytest.raises(ConfigurationError, match="must be a mapping"):
        load_config(p)
