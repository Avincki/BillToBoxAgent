"""One-time Microsoft device-code consent (Outlook / Microsoft 365, Mail.Read).

Run on any machine; it prints a URL + code to enter in a browser. Reads the config
from ``$BTB_CONFIG`` (default ``config.yaml``).

    python scripts/auth_ms.py
"""

from __future__ import annotations

from billtobox_agent.config import load_config_from_env
from billtobox_agent.mail.ms_auth import MS_SCOPES, run_device_flow


def main() -> None:
    config = load_config_from_env()
    run_device_flow(config.microsoft)
    print(f"Saved Microsoft token cache to {config.microsoft.token_path}")
    print(f"Scopes granted: {', '.join(MS_SCOPES)}")


if __name__ == "__main__":
    main()
