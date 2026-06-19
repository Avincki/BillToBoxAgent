"""Async SQLite engine/session factory + schema bootstrap.

Mirrors HomeEnergyCenter's data/database.py, adding a ``connect`` listener that
enables **WAL** journal mode and foreign-key enforcement on every connection —
BillToBox runs two processes (timer worker + dashboard) against one database, so
WAL + single-writer discipline matters (decisions.md §13-D).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from billtobox_agent.data.models import Base


def make_sqlite_url(path: str | Path) -> str:
    """Build an aiosqlite URL from a filesystem path or the literal ':memory:'."""
    if str(path) == ":memory:":
        return "sqlite+aiosqlite:///:memory:"
    return f"sqlite+aiosqlite:///{Path(path).resolve().as_posix()}"


def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """Enable WAL + foreign keys on each new connection.

    WAL needs a file-backed database; for ``:memory:`` SQLite keeps the
    ``memory`` journal mode (the PRAGMA is a harmless no-op there).
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def create_engine(sqlite_path: str | Path, *, echo: bool = False) -> AsyncEngine:
    # SQLite won't create parent directories; do it for relative paths like data/billtobox.db.
    if str(sqlite_path) != ":memory:":
        parent = Path(sqlite_path).resolve().parent
        parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(make_sqlite_url(sqlite_path), echo=echo, future=True)
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_schema(engine: AsyncEngine) -> None:
    """Create tables from ORM metadata.

    Used for fresh installs and tests; production runs Alembic migrations.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_schema(engine: AsyncEngine) -> None:
    """Drop all tables. Tests only."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
