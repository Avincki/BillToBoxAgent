"""Load and validate the YAML configuration file into an :class:`AppConfig`."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from billtobox_agent.config.models import AppConfig

CONFIG_PATH_ENV = "BTB_CONFIG"
DEFAULT_CONFIG_FILENAME = "config.yaml"


class ConfigError(Exception):
    """Raised when configuration cannot be loaded or is invalid."""


def resolve_config_path() -> Path:
    """Config file path: ``$BTB_CONFIG`` if set, else ``config.yaml`` in the cwd."""
    return Path(os.environ.get(CONFIG_PATH_ENV, DEFAULT_CONFIG_FILENAME))


def load_config(path: str | Path) -> AppConfig:
    """Load and validate a YAML config file.

    Raises :class:`ConfigError` with a descriptive message for a missing file,
    a YAML parse failure, or a Pydantic validation failure (so a misconfigured
    deployment fails fast at startup with an actionable message).
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {p}") from exc
    except OSError as exc:
        raise ConfigError(f"could not read config file {p}: {exc}") from exc

    try:
        data: Any = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML parse error in {p}: {exc}") from exc

    if data is None:
        raise ConfigError(f"config file is empty: {p}")
    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a mapping, got {type(data).__name__}: {p}")

    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid config in {p}:\n{_format_validation_error(exc)}") from exc


def load_config_from_env() -> AppConfig:
    """Load the config from the path in ``$BTB_CONFIG`` (default ``config.yaml``)."""
    return load_config(resolve_config_path())


def _format_validation_error(exc: ValidationError) -> str:
    lines: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "<root>"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)
