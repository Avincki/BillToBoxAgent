"""Google OAuth: one-time consent + headless token load/refresh.

Scopes are least-privilege: Gmail read-only and Drive ``drive.file`` (the app can
only touch files it creates). One consent covers both Gmail (task 8) and Drive
(task 14). The interactive consent runs once via ``scripts/auth_google.py``; the
worker thereafter loads and auto-refreshes the stored refresh token.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from billtobox_agent.config.models import GoogleConfig

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.file",
]


class GoogleAuthError(Exception):
    """Raised when Google credentials are missing or cannot be refreshed."""


def _client_config(config: GoogleConfig) -> dict[str, Any]:
    return {
        "installed": {
            "client_id": config.client_id,
            "client_secret": config.client_secret.get_secret_value(),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def run_consent_flow(config: GoogleConfig) -> Credentials:
    """Run the interactive consent flow (opens a browser). Returns credentials."""
    flow = InstalledAppFlow.from_client_config(_client_config(config), GOOGLE_SCOPES)
    return flow.run_local_server(port=0)


def save_credentials(credentials: Credentials, token_path: str | Path) -> None:
    path = Path(token_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(credentials.to_json(), encoding="utf-8")


def load_credentials(config: GoogleConfig) -> Credentials:
    """Load stored credentials, refreshing (and re-saving) if expired.

    Raises :class:`GoogleAuthError` if no usable token exists — the operator must
    run ``scripts/auth_google.py`` once on a machine with a browser.
    """
    token_path = Path(config.token_path)
    if not token_path.exists():
        raise GoogleAuthError(
            f"Google token not found at {token_path}; run scripts/auth_google.py to authorize"
        )
    credentials = Credentials.from_authorized_user_file(str(token_path), GOOGLE_SCOPES)
    if credentials.valid:
        return credentials
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        save_credentials(credentials, token_path)
        return credentials
    raise GoogleAuthError(f"Google token at {token_path} is invalid; re-run scripts/auth_google.py")
