"""Minimal Microsoft Graph HTTP client (read-only GETs over ``requests``).

The Outlook connector depends on the :class:`GraphHttp` protocol so tests inject a
fake; production uses :class:`GraphClient`, which adds a Bearer token from MSAL.
"""

from __future__ import annotations

from typing import Any, Protocol

import requests

_BASE_URL = "https://graph.microsoft.com/v1.0"
_TIMEOUT_S = 30


class GraphError(Exception):
    """Raised on an unexpected Graph response."""


class GraphHttp(Protocol):
    def get(self, path: str, params: dict[str, Any] | None = ...) -> dict[str, Any]: ...


class GraphClient:
    def __init__(
        self,
        token_provider: Any,
        session: requests.Session | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._session = session or requests.Session()

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{_BASE_URL}{path}"
        token = self._token_provider()
        response = self._session.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT_S,
        )
        response.raise_for_status()
        payload: Any = response.json()
        if not isinstance(payload, dict):
            raise GraphError(f"unexpected Graph response type: {type(payload).__name__}")
        return payload
