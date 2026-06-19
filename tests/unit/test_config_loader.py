from __future__ import annotations

from pathlib import Path

import pytest

from billtobox_agent.config import ConfigError, load_config, resolve_config_path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_loads_minimal_fixture() -> None:
    cfg = load_config(_FIXTURES / "config_minimal.yaml")
    assert cfg.extraction.confidence_threshold == 0.85
    assert cfg.web.port == 9003
    assert cfg.anthropic.api_key.get_secret_value() == "test-anthropic-key"


def test_example_config_is_valid() -> None:
    cfg = load_config(_REPO_ROOT / "config.example.yaml")
    assert cfg.billtobox.sender_address  # parsed as a valid email
    assert cfg.smtp.port == 587


def test_missing_file_raises() -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config("does-not-exist-12345.yaml")


def test_bad_yaml_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("key: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML parse error"):
        load_config(p)


def test_empty_file_raises(tmp_path: Path) -> None:
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="empty"):
        load_config(p)


def test_non_mapping_root_raises(tmp_path: Path) -> None:
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a mapping"):
        load_config(p)


def test_validation_error_names_the_field(tmp_path: Path) -> None:
    p = tmp_path / "cfg.yaml"
    p.write_text("google:\n  client_id: x\n", encoding="utf-8")  # missing required sections
    with pytest.raises(ConfigError, match="anthropic"):
        load_config(p)


def test_resolve_config_path_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BTB_CONFIG", raising=False)
    assert resolve_config_path() == Path("config.yaml")
    monkeypatch.setenv("BTB_CONFIG", "custom/path.yaml")
    assert resolve_config_path() == Path("custom/path.yaml")
