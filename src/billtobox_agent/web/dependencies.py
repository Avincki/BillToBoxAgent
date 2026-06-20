"""FastAPI dependencies: config, per-request UnitOfWork, and the same-origin guard."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from billtobox_agent.config.models import AppConfig
from billtobox_agent.data import UnitOfWork


def get_config(request: Request) -> AppConfig:
    config: AppConfig = request.app.state.config
    return config


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


def get_uow(request: Request) -> UnitOfWork:
    """A fresh UnitOfWork per request — the route enters ``async with`` itself."""
    return UnitOfWork(get_session_factory(request))


def require_same_origin(request: Request) -> None:
    """CSRF guard for state-changing routes (task 19): require Origin == this host.

    The dashboard has no authentication — anyone on the tailnet can reach it, and
    so can a malicious page in another tab via a cross-site form POST. Browsers
    always set ``Origin`` on POST and won't let JS forge it cross-origin, so
    matching ``Origin`` to ``scheme://Host`` rejects cross-site submissions
    without sessions or tokens. (Read-only task-18 routes don't use this yet.)
    """
    origin = request.headers.get("origin")
    host = request.headers.get("host", "")
    if not origin or not host:
        raise HTTPException(status_code=403, detail="missing Origin or Host header")
    if origin != f"{request.url.scheme}://{host}":
        raise HTTPException(status_code=403, detail="cross-origin request blocked")


ConfigDep = Annotated[AppConfig, Depends(get_config)]
UowDep = Annotated[UnitOfWork, Depends(get_uow)]
