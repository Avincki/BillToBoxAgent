"""Worker entry point — one invoice-processing cycle.

Run by the systemd oneshot timer (task 22) as ``python -m billtobox_agent.worker``.
It loads config, configures logging, builds the real connectors/clients from
config, then runs a single :func:`run_once` pass. ``--dry-run`` logs intended
actions without uploading to Drive or committing approved-item writes.
"""

from __future__ import annotations

import argparse
import asyncio

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from billtobox_agent.config.loader import load_config_from_env
from billtobox_agent.config.models import AppConfig, Source
from billtobox_agent.data import create_engine, create_session_factory
from billtobox_agent.drive import DriveConnector
from billtobox_agent.extraction import build_anthropic_client
from billtobox_agent.mail import GmailConnector, OutlookConnector
from billtobox_agent.mail.base import MailConnector
from billtobox_agent.monitoring import configure_logging
from billtobox_agent.pipeline import RunSummary, WorkerContext, run_once

_log = structlog.get_logger(__name__)


def build_mail_connectors(config: AppConfig) -> dict[str, MailConnector]:
    """Build a connector for each enabled source; skip (log) any that can't be built.

    Doccle and any other source the worker can't build are skipped here, so the
    run simply has no connector for them (run_once records that and moves on).
    Shared with the dashboard's manual-run trigger (task 19).
    """
    connectors: dict[str, MailConnector] = {}
    for source in config.sources.polling:
        try:
            if source == Source.GMAIL:
                connectors[source.value] = GmailConnector.from_config(config.google)
            elif source == Source.OUTLOOK:
                connectors[source.value] = OutlookConnector.from_config(config.microsoft)
            else:
                _log.warning("worker.source_unsupported", source=source.value)
        except Exception:  # a credential/setup failure skips one source
            _log.exception("worker.connector_build_failed", source=source.value)
    return connectors


def _build_context(config: AppConfig, engine: AsyncEngine, *, dry_run: bool) -> WorkerContext:
    return WorkerContext(
        config=config,
        session_factory=create_session_factory(engine),
        mail_connectors=build_mail_connectors(config),
        drive=DriveConnector.from_config(config.google, config.drive),
        anthropic_client=build_anthropic_client(config.anthropic),
        dry_run=dry_run,
    )


async def _run_and_dispose(ctx: WorkerContext, engine: AsyncEngine) -> RunSummary:
    try:
        return await run_once(ctx)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="billtobox-agent-worker",
        description="Run one BillToBox invoice-processing cycle.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log intended actions without uploading to Drive or committing approved-item writes.",
    )
    args = parser.parse_args(argv)

    config = load_config_from_env()
    configure_logging(config.logging)

    engine = create_engine(config.storage.sqlite_path)
    ctx = _build_context(config, engine, dry_run=args.dry_run)
    summary = asyncio.run(_run_and_dispose(ctx, engine))

    _log.info(
        "worker.done",
        run_id=summary.run_id,
        fetched=summary.items_fetched,
        extracted=summary.items_extracted,
        stored=summary.items_stored,
        flagged=summary.items_flagged,
        errors=len(summary.errors),
        dry_run=summary.dry_run,
    )
    return 1 if summary.errors else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
