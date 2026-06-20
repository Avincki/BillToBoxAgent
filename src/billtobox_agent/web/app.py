"""FastAPI app factory + lifespan for the dashboard (tasks 18-19).

Unlike HomeEnergyCenter (which runs its worker loop inside the web process), the
BillToBox dashboard's writing is done by a separate timer process (decisions.md
§13-F). The lifespan opens the shared SQLite database, records the session start
time (log-stream replay window), and resolves the *steering components* — the
Drive connector, Anthropic client, and mail connectors that the task-19 actions
(edit-move / re-extract / manual run) need. These are injected directly in tests
and built best-effort from config in production (a missing OAuth token leaves a
component ``None``, so the read-only views still start).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import anthropic
import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from billtobox_agent.config.loader import load_config, resolve_config_path
from billtobox_agent.config.models import AppConfig
from billtobox_agent.data import create_engine, create_session_factory, init_schema
from billtobox_agent.drive import DriveConnector
from billtobox_agent.extraction import build_anthropic_client
from billtobox_agent.mail.base import MailConnector
from billtobox_agent.monitoring import configure_logging
from billtobox_agent.web.api import api_router
from billtobox_agent.web.steering import steering_router
from billtobox_agent.web.views import views_router
from billtobox_agent.worker import build_mail_connectors

_log = structlog.get_logger(__name__)

_WEB_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _WEB_DIR / "static"


def _try_build_drive(config: AppConfig) -> DriveConnector | None:
    try:
        return DriveConnector.from_config(config.google, config.drive)
    except Exception:  # missing/invalid token — dashboard still serves read-only views
        _log.warning("dashboard.drive_unavailable", exc_info=True)
        return None


def _try_build_anthropic(config: AppConfig) -> anthropic.Anthropic | None:
    try:
        return build_anthropic_client(config.anthropic)
    except Exception:
        _log.warning("dashboard.anthropic_unavailable", exc_info=True)
        return None


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

    # Steering components: injected (tests) or built best-effort from config.
    inj_drive, inj_anthropic, inj_mail = app.state.injected_components
    app.state.drive = inj_drive if inj_drive is not None else _try_build_drive(config)
    app.state.anthropic_client = (
        inj_anthropic if inj_anthropic is not None else _try_build_anthropic(config)
    )
    app.state.mail_connectors = inj_mail if inj_mail is not None else build_mail_connectors(config)
    try:
        yield
    finally:
        await engine.dispose()


def create_app(
    config: AppConfig | None = None,
    *,
    config_path: str | Path | None = None,
    drive: DriveConnector | None = None,
    anthropic_client: anthropic.Anthropic | None = None,
    mail_connectors: Mapping[str, MailConnector] | None = None,
) -> FastAPI:
    """Build the dashboard app. Pass ``config`` directly (tests) or let it load
    from ``config_path``/``$BTB_CONFIG``. The steering components may be injected
    (tests) or are otherwise built from config in the lifespan."""
    if config is None:
        path = Path(config_path) if config_path is not None else resolve_config_path()
        config = load_config(path)

    app = FastAPI(
        title="BillToBoxAgent",
        description="Dashboard for the BillToBox invoice-processing agent.",
        version="0.1.0",
        lifespan=_lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.config = config
    app.state.injected_components = (drive, anthropic_client, mail_connectors)

    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    app.include_router(api_router)
    app.include_router(steering_router)
    app.include_router(views_router)
    return app
