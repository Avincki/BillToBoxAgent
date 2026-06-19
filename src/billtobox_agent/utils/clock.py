"""Local-timezone helpers.

The app stores UTC internally and renders user-facing times in the local display
zone (Europe/Brussels). On Windows the ``tzdata`` package supplies the zone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Brussels")


def now_local() -> datetime:
    """Current time as a tz-aware datetime in the local display zone."""
    return datetime.now(LOCAL_TZ)


def to_local(dt: datetime) -> datetime:
    """Convert ``dt`` to the local zone; a naive datetime is treated as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(LOCAL_TZ)
