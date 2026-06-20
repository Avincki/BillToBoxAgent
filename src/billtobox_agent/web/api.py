"""JSON API + the Server-Sent-Events live log stream (task 18).

``GET /api/logs/stream`` tails the rotating JSON log file (``billtobox_agent.log``)
and pushes each line to the browser as an SSE ``data:`` event — mirroring HEC's
``stream_logs``/``_tail_log_sse``: session-replay by default (or ``?replay_hours=N``),
rotation-safe (reopens when the file shrinks/rotates), and non-blocking (all file
I/O runs in a worker thread).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from billtobox_agent.web.dependencies import ConfigDep, UowDep

api_router = APIRouter(prefix="/api")

_LOG_FILENAME = "billtobox_agent.log"
_POLL_INTERVAL_S = 0.4


@api_router.get("/health")
async def health(uow: UowDep) -> dict[str, object]:
    """Per-source health snapshot (last success/error + watermark)."""
    async with uow:
        sources = await uow.source_status.list()
        return {
            "status": "ok",
            "sources": [
                {
                    "source": s.source,
                    "watermark": s.watermark.isoformat() if s.watermark else None,
                    "last_success_at": (
                        s.last_success_at.isoformat() if s.last_success_at else None
                    ),
                    "last_error_at": s.last_error_at.isoformat() if s.last_error_at else None,
                    "last_error_message": s.last_error_message,
                }
                for s in sources
            ],
        }


@api_router.get("/logs/stream")
async def stream_logs(
    request: Request,
    config: ConfigDep,
    replay_hours: Annotated[float | None, Query(gt=0.0, le=720.0)] = None,
) -> StreamingResponse:
    """SSE stream of the rotating JSON log file.

    Default replay window is the current server session; ``?replay_hours=N`` widens
    it to at least the last ``N`` hours (spanning a restart inside that window).
    """
    log_path = Path(config.logging.log_dir) / _LOG_FILENAME
    session_started_at: datetime = request.app.state.session_started_at
    if replay_hours is None:
        replay_since = session_started_at
    else:
        replay_since = min(session_started_at, datetime.now(UTC) - timedelta(hours=replay_hours))
    return StreamingResponse(
        _tail_log_sse(request, log_path, replay_since),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _line_at_or_after(line: str, replay_since: datetime) -> bool:
    """True if a JSON log line's timestamp is at/after ``replay_since`` (UTC compare).

    Non-JSON or timestamp-less lines are kept — they shouldn't appear in our
    structured log, and dropping them would hide surprises.
    """
    try:
        record = json.loads(line)
    except ValueError:
        return True
    ts_text = record.get("timestamp")
    if not isinstance(ts_text, str):
        return True
    try:
        ts = datetime.fromisoformat(ts_text.replace("Z", "+00:00"))
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts >= replay_since


async def _tail_log_sse(
    request: Request, log_path: Path, replay_since: datetime
) -> AsyncIterator[str]:
    # Wait for the file to exist (first start before any logs are written).
    while not await asyncio.to_thread(log_path.exists):
        if await request.is_disconnected():
            return
        await asyncio.sleep(_POLL_INTERVAL_S)

    handle = await asyncio.to_thread(open, log_path, encoding="utf-8", errors="replace")
    try:
        while True:
            if await request.is_disconnected():
                return
            line = await asyncio.to_thread(handle.readline)
            if line:
                if _line_at_or_after(line, replay_since):
                    yield f"data: {line.rstrip()}\n\n"
                continue

            # No new data — detect rotation (file shrank or was replaced).
            try:
                current_size = (await asyncio.to_thread(log_path.stat)).st_size
            except FileNotFoundError:
                current_size = 0
            if current_size < handle.tell():
                await asyncio.to_thread(handle.close)
                while not await asyncio.to_thread(log_path.exists):
                    if await request.is_disconnected():
                        return
                    await asyncio.sleep(_POLL_INTERVAL_S)
                handle = await asyncio.to_thread(open, log_path, encoding="utf-8", errors="replace")
                continue

            await asyncio.sleep(_POLL_INTERVAL_S)
    finally:
        await asyncio.to_thread(handle.close)
