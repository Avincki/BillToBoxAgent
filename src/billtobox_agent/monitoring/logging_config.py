"""Central structlog + stdlib logging setup (mirrors HomeEnergyCenter).

One :func:`configure_logging` call wires everything:

* The root stdlib logger gets two handlers — a rotating JSON file handler under
  ``config.log_dir`` and a console handler on stderr — so libraries that use plain
  ``logging.getLogger(...)`` (uvicorn, sqlalchemy, google/msal) flow through the same
  pipeline. The JSON file is what the dashboard's ``/logs`` SSE stream tails (task 18).

* structlog feeds events into stdlib via :class:`structlog.stdlib.LoggerFactory`, so
  application code can use either ``logging.getLogger(__name__)`` or
  ``structlog.get_logger(__name__)`` and get coherent output.

The function is idempotent: it removes any handlers it previously installed before
re-installing fresh ones, so tests and repeat calls don't double-log.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import structlog
from structlog.types import EventDict, Processor, WrappedLogger

from billtobox_agent.config.models import LoggingConfig
from billtobox_agent.utils.clock import now_local

_HANDLER_TAG = "_billtobox_agent_handler"
_LOG_FILENAME = "billtobox_agent.log"
_FILE_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB before rotation


def _local_timestamper(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """structlog processor: stamp each event with local-zone ISO time.

    Every log line carries Brussels wall-clock time (with UTC offset), matching the
    rest of the user-facing surface. Internal timestamps stay UTC.
    """
    event_dict["timestamp"] = now_local().isoformat(timespec="milliseconds")
    return event_dict


def configure_logging(config: LoggingConfig) -> None:
    """Install handlers + configure structlog from a :class:`LoggingConfig`.

    Idempotent — calling twice replaces our previous handlers without duplicating output.
    """
    log_dir = Path(config.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _local_timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            # Hand off to the stdlib formatter, which calls a renderer.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    file_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        foreign_pre_chain=shared_processors,
    )

    file_handler: logging.Handler = logging.handlers.RotatingFileHandler(
        log_dir / _LOG_FILENAME,
        maxBytes=_FILE_MAX_BYTES,
        backupCount=max(1, config.retention_days),
        encoding="utf-8",
    )
    file_handler.setFormatter(file_formatter)
    setattr(file_handler, _HANDLER_TAG, True)

    console_handler: logging.Handler = logging.StreamHandler(stream=sys.stderr)
    console_handler.setFormatter(console_formatter)
    setattr(console_handler, _HANDLER_TAG, True)

    root = logging.getLogger()
    # Clear handlers we previously installed (idempotency); leave any foreign
    # handlers (e.g. pytest's caplog) in place.
    root.handlers = [h for h in root.handlers if not getattr(h, _HANDLER_TAG, False)]
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    root.setLevel(config.level)

    # Chatty HTTP libraries used by the connectors; keep them at WARNING so the
    # live log viewer stays readable. Library-level problems still surface.
    for noisy in ("urllib3", "httpx", "httpcore", "googleapiclient.discovery_cache"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


__all__ = ["configure_logging"]
