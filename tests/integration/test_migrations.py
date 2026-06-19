from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_TABLES = {"invoices", "runs", "source_status", "agent_events"}


def _alembic_config() -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    # Absolute script_location so the test runs regardless of the cwd.
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    return cfg


def _table_names(db_path: Path) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row[0] for row in rows}
    finally:
        con.close()


def test_upgrade_then_downgrade_is_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "m.db"
    monkeypatch.delenv("BTB_DB_URL", raising=False)
    monkeypatch.setenv("BTB_SQLITE_PATH", str(db))
    cfg = _alembic_config()

    command.upgrade(cfg, "head")
    assert _table_names(db) >= _APP_TABLES

    command.downgrade(cfg, "base")
    assert _APP_TABLES.isdisjoint(_table_names(db))
