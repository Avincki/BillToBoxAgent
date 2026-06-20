"""Dashboard entry point — ``python main.py`` serves the read-only dashboard.

Run by the long-lived ``billtobox-dashboard.service`` systemd unit (task 22). The
worker (invoice processing) is a separate timer-driven process; this serves only
the read-only views + the SSE log stream, bound to ``web.host``/``web.port`` from
config (default ``127.0.0.1:9003``; Caddy fronts public TLS — CONVENTIONS.md §11).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

from billtobox_agent.config.loader import ConfigError, load_config, resolve_config_path
from billtobox_agent.monitoring import configure_logging


def main() -> int:
    config_path = resolve_config_path()
    if not config_path.exists():
        example = Path("config.example.yaml")
        hint = f" — copy {example} to {config_path} and edit it" if example.exists() else ""
        print(f"config not found: {config_path}{hint}", file=sys.stderr)
        return 1
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"config error:\n{exc}", file=sys.stderr)
        return 1

    configure_logging(config.logging)
    # Pin the path the uvicorn factory re-reads to exactly what we validated.
    os.environ["BTB_CONFIG"] = str(config_path.resolve())

    uvicorn.run(
        "billtobox_agent.web.app:create_app",
        factory=True,
        host=config.web.host,
        port=config.web.port,
        log_config=None,  # reuse the root logger configured above
        timeout_graceful_shutdown=10,  # force-close lingering SSE streams on shutdown
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
