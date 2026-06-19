"""Redaction for the ``agent_events`` audit log.

Every agent-event writer passes tool inputs/outputs through :func:`redact` so
secrets and raw PDF bytes never land in the database or the dashboard
(decisions.md §2).
"""

from __future__ import annotations

from typing import Any

from billtobox_agent.utils.hashing import sha256_hex

_SECRET_HINTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
)
_REDACTED = "***"
_MAX_STR = 2000


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _SECRET_HINTS)


def redact(value: Any) -> Any:
    """Return a JSON-safe copy with secrets masked and raw bytes summarised.

    * dict values under secret-ish keys (``password``/``token``/``api_key``/...) → ``***``
    * ``bytes``/``bytearray`` → ``{"__bytes__": {"sha256": ..., "len": ...}}`` — the raw
      PDF bytes are never stored, only their hash and length
    * over-long strings are truncated
    * lists/tuples and nested dicts are processed recursively
    """
    if isinstance(value, dict):
        return {
            key: (_REDACTED if _is_secret_key(str(key)) else redact(val))
            for key, val in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return {"__bytes__": {"sha256": sha256_hex(bytes(value)), "len": len(value)}}
    if isinstance(value, str) and len(value) > _MAX_STR:
        return value[:_MAX_STR] + f"...[truncated {len(value) - _MAX_STR} chars]"
    return value
