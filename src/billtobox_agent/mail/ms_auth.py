"""Microsoft OAuth via MSAL (delegated Mail.Read), device-code flow.

The one-time consent runs via ``scripts/auth_ms.py`` (device code — works for both
outlook.com and Microsoft 365). MSAL persists a token cache at ``microsoft.token_path``;
the worker then acquires tokens silently (auto-refresh). A public client is used, so no
client secret is needed.
"""

from __future__ import annotations

from pathlib import Path

import msal

from billtobox_agent.config.models import MicrosoftConfig

MS_SCOPES = ["Mail.Read"]


class MicrosoftAuthError(Exception):
    """Raised when a Microsoft token is missing or cannot be refreshed."""


def _load_cache(token_path: str | Path) -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    path = Path(token_path)
    if path.exists():
        cache.deserialize(path.read_text(encoding="utf-8"))
    return cache


def _save_cache(cache: msal.SerializableTokenCache, token_path: str | Path) -> None:
    if cache.has_state_changed:
        path = Path(token_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cache.serialize(), encoding="utf-8")


def _app(
    config: MicrosoftConfig, cache: msal.SerializableTokenCache
) -> msal.PublicClientApplication:
    return msal.PublicClientApplication(
        config.client_id,
        authority=f"https://login.microsoftonline.com/{config.tenant_id}",
        token_cache=cache,
    )


def acquire_token(config: MicrosoftConfig) -> str:
    """Acquire an access token silently from the cached refresh token."""
    cache = _load_cache(config.token_path)
    app = _app(config, cache)
    accounts = app.get_accounts()
    if not accounts:
        raise MicrosoftAuthError(
            f"No Microsoft token cache at {config.token_path}; run scripts/auth_ms.py"
        )
    result = app.acquire_token_silent(MS_SCOPES, account=accounts[0])
    _save_cache(cache, config.token_path)
    if not result or "access_token" not in result:
        raise MicrosoftAuthError("Microsoft token refresh failed; re-run scripts/auth_ms.py")
    return str(result["access_token"])


def run_device_flow(config: MicrosoftConfig) -> None:
    """Interactive device-code consent; prints a URL + code, then blocks until done."""
    cache = _load_cache(config.token_path)
    app = _app(config, cache)
    flow = app.initiate_device_flow(scopes=MS_SCOPES)
    if "user_code" not in flow:
        raise MicrosoftAuthError(f"Failed to start device flow: {flow.get('error_description')}")
    print(flow["message"])
    result = app.acquire_token_by_device_flow(flow)
    _save_cache(cache, config.token_path)
    if "access_token" not in result:
        raise MicrosoftAuthError(f"Device flow failed: {result.get('error_description')}")
