"""FastAPI app factory + lifespan for the read-only dashboard (task 18).

Unlike HomeEnergyCenter (which runs its worker loop inside the web process), the
BillToBox dashboard is worker-free: a separate timer process does the writing
(decisions.md §13-F). The lifespan only opens the shared SQLite database and
records the session start time used by the log-stream replay window.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from billtobox_agent.config.loader import load_config, resolve_config_path
from billtobox_agent.config.models import AppConfig
from billtobox_agent.data import create_engine, create_session_factory, init_schema
from billtobox_agent.monitoring import configure_logging
from billtobox_agent.web.api import api_router
from billtobox_agent.web.views import views_router

_WEB_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _WEB_DIR / "static"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    config: AppConfig = app.state.config
    configure_logging(config.logging)
    engine = create_engine(config.storage.sqlite_path)
    # Idempotent: production schema is owned by Alembic + the worker, but this
    # makes the dashboard robust if it happens to start against a fresh database.
    await init_schema(engine)
    app.state.db_engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.session_started_at = datetime.now(UTC)
    try:
        yield
    finally:
        await engine.dispose()


def create_app(
    config: AppConfig | None = None,
    *,
    config_path: str | Path | None = None,
) -> FastAPI:
    """Build the dashboard app. Pass ``config`` directly (tests) or let it load
    from ``config_path``/``$BTB_CONFIG`` (production via the uvicorn factory)."""
    if config is None:
        path = Path(config_path) if config_path is not None else resolve_config_path()
        config = load_config(path)

    app = FastAPI(
        title="BillToBoxAgent",
        description="Read-only dashboard for the BillToBox invoice-processing agent.",
        version="0.1.0",
        lifespan=_lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.config = config

    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    app.include_router(api_router)
    app.include_router(views_router)
    return app
