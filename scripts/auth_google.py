"""One-time Google OAuth consent (run on a machine with a browser).

Mints the refresh token the headless worker reuses for Gmail (read-only) and Drive
(drive.file). Reads the config from ``$BTB_CONFIG`` (default ``config.yaml``).

    python scripts/auth_google.py
"""

from __future__ import annotations

from billtobox_agent.config import load_config_from_env
from billtobox_agent.mail.google_auth import GOOGLE_SCOPES, run_consent_flow, save_credentials


def main() -> None:
    config = load_config_from_env()
    credentials = run_consent_flow(config.google)
    save_credentials(credentials, config.google.token_path)
    print(f"Saved Google credentials to {config.google.token_path}")
    print(f"Scopes granted: {', '.join(GOOGLE_SCOPES)}")


if __name__ == "__main__":
    main()
