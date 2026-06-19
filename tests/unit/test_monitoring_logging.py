from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import structlog

from billtobox_agent.config.models import LoggingConfig
from billtobox_agent.monitoring import configure_logging
from billtobox_agent.utils.clock import LOCAL_TZ


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture(autouse=True)
def _reset_root_logging() -> Iterator[None]:
    """Strip our handlers between tests so each test starts clean."""
    yield
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not getattr(h, "_billtobox_agent_handler", False)]
    structlog.contextvars.clear_contextvars()


def test_configure_logging_creates_log_dir_and_file(tmp_path: Path) -> None:
    cfg = LoggingConfig(log_dir=tmp_path / "logs", level="INFO", retention_days=3)
    configure_logging(cfg)

    log = structlog.stdlib.get_logger("test")
    log.info("hello world", run="t1")

    log_file = tmp_path / "logs" / "billtobox_agent.log"
    assert log_file.exists()
    records = _read_jsonl(log_file)
    assert any(r.get("event") == "hello world" and r.get("run") == "t1" for r in records)


def test_log_records_carry_iso_timestamp_and_level(tmp_path: Path) -> None:
    cfg = LoggingConfig(log_dir=tmp_path / "logs", level="INFO")
    configure_logging(cfg)

    log = structlog.stdlib.get_logger("test")
    log.warning("watch out", code=42)

    records = _read_jsonl(tmp_path / "logs" / "billtobox_agent.log")
    rec = next(r for r in records if r.get("event") == "watch out")
    assert rec["level"] == "warning"
    assert rec["code"] == 42
    assert "timestamp" in rec
    # Local (Brussels) ISO timestamp carrying a UTC offset — not UTC "Z".
    parsed = datetime.fromisoformat(rec["timestamp"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == parsed.astimezone(LOCAL_TZ).utcoffset()


def test_bound_contextvars_appear_in_records(tmp_path: Path) -> None:
    cfg = LoggingConfig(log_dir=tmp_path / "logs", level="INFO")
    configure_logging(cfg)
    log = structlog.stdlib.get_logger("test")

    with structlog.contextvars.bound_contextvars(run_id="42"):
        log.info("inside run", source="gmail")
    log.info("outside run")

    records = _read_jsonl(tmp_path / "logs" / "billtobox_agent.log")
    inside = next(r for r in records if r.get("event") == "inside run")
    outside = next(r for r in records if r.get("event") == "outside run")
    assert inside["run_id"] == "42"
    assert inside["source"] == "gmail"
    assert "run_id" not in outside


def test_stdlib_loggers_also_route_through_pipeline(tmp_path: Path) -> None:
    """Libraries using ``logging.getLogger`` (uvicorn, sqlalchemy) should write
    to the same JSON file."""
    cfg = LoggingConfig(log_dir=tmp_path / "logs", level="INFO")
    configure_logging(cfg)

    stdlib_log = logging.getLogger("uvicorn.access")
    stdlib_log.info("access %s %s", "GET", "/")

    records = _read_jsonl(tmp_path / "logs" / "billtobox_agent.log")
    assert any("access" in str(r.get("event", "")) for r in records)


def test_configure_logging_is_idempotent(tmp_path: Path) -> None:
    cfg = LoggingConfig(log_dir=tmp_path / "logs", level="INFO")
    configure_logging(cfg)
    configure_logging(cfg)

    log = structlog.stdlib.get_logger("test")
    log.info("once")

    records = _read_jsonl(tmp_path / "logs" / "billtobox_agent.log")
    matches = [r for r in records if r.get("event") == "once"]
    assert len(matches) == 1


def test_does_not_clobber_foreign_handlers(tmp_path: Path) -> None:
    """A pre-existing third-party handler (e.g. pytest's caplog) must survive
    ``configure_logging`` calls."""
    foreign = logging.NullHandler()
    root = logging.getLogger()
    root.addHandler(foreign)
    try:
        cfg = LoggingConfig(log_dir=tmp_path / "logs", level="INFO")
        configure_logging(cfg)
        assert foreign in root.handlers
    finally:
        root.removeHandler(foreign)


def test_level_threshold_respected(tmp_path: Path) -> None:
    cfg = LoggingConfig(log_dir=tmp_path / "logs", level="WARNING")
    configure_logging(cfg)
    log = structlog.stdlib.get_logger("test")
    log.info("filtered out")
    log.warning("kept")

    records = _read_jsonl(tmp_path / "logs" / "billtobox_agent.log")
    events = [r["event"] for r in records]
    assert "filtered out" not in events
    assert "kept" in events
