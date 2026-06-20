"""The linear worker pipeline — ``run_once`` (task 17).

One pass over every enabled mail source, straight-line (not yet the tool-calling
loop — that is task 21):

    open a ``runs`` row
      → per source: fetch-since-watermark
          → per PDF: pre-filter → content-hash dedup → create invoice row
              → extract → confidence gate
                  → approved: ensure_quarter_folder + store_pdf_to_drive
                  → otherwise: flag_for_review
      → advance watermark (inside fetch) → close the ``runs`` row with counts/errors

Every step writes structured ``agent_events`` (the tools do their own; the pipeline
adds pre-filter/dedup decisions). Writes are committed **per item** so a crash
leaves already-stored invoices recorded (their ``content_hash`` blocks re-upload on
the next run). ``dry_run`` performs the full read+extract pass but skips Drive
uploads and never commits — the UnitOfWork rolls back on exit, so nothing persists.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import anthropic
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from billtobox_agent.config.models import AppConfig
from billtobox_agent.data import Invoice, UnitOfWork
from billtobox_agent.data.models import AgentEventType
from billtobox_agent.drive import (
    DriveConnector,
    InvoiceFileFields,
    build_filename,
    ensure_quarter_folder,
    store_pdf_to_drive,
)
from billtobox_agent.extraction import ExtractionResult, extract_invoice, period_for
from billtobox_agent.mail.base import FetchedPdf, MailConnector
from billtobox_agent.mail.fetch import fetch_new_pdfs
from billtobox_agent.mail.prefilter import prefilter
from billtobox_agent.pipeline.dedup import check_duplicate, compute_content_hash
from billtobox_agent.pipeline.status import flag_for_review

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class WorkerContext:
    """Everything ``run_once`` needs — injected so tests can pass fakes."""

    config: AppConfig
    session_factory: async_sessionmaker[AsyncSession]
    # source name (e.g. "gmail") -> connector. Built by the worker for enabled sources.
    mail_connectors: Mapping[str, MailConnector]
    drive: DriveConnector
    anthropic_client: anthropic.Anthropic
    dry_run: bool = False
    # When False, no invoice may be emailed to the live Billtobox service (test runs).
    # Enforced at the send chokepoint (``email_to_billtobox``); recorded per run.
    billtobox_send_enabled: bool = True


@dataclass
class RunSummary:
    run_id: int | None
    items_fetched: int = 0
    items_extracted: int = 0
    items_stored: int = 0
    items_flagged: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


async def run_once(ctx: WorkerContext, *, since_override: date | None = None) -> RunSummary:
    """Process every enabled source once and return a :class:`RunSummary`.

    ``since_override`` (a date, e.g. from the web run form) makes every source fetch
    from that day instead of its stored watermark — used for the first run.
    """
    summary = RunSummary(run_id=None, dry_run=ctx.dry_run)
    since_dt = (
        datetime(since_override.year, since_override.month, since_override.day, tzinfo=UTC)
        if since_override is not None
        else None
    )

    async with UnitOfWork(ctx.session_factory) as uow:
        run = await uow.runs.start()
        run_id = run.id
        if not ctx.dry_run:
            summary.run_id = run_id
            await uow.commit()  # persist the run row up front for stable FK references
        await _record_run_start(uow, run_id, ctx, since_dt)

        step = 0
        for source in ctx.config.sources.polling:
            connector = ctx.mail_connectors.get(source)
            if connector is None:
                msg = f"no connector available for source {source.value!r}"
                _log.warning("pipeline.source_skipped", source=source.value, reason=msg)
                summary.errors.append(msg)
                continue

            try:
                pdfs = await fetch_new_pdfs(connector, uow, since_override=since_dt)
            except Exception as exc:  # isolate one source's failure
                _log.exception("pipeline.fetch_failed", source=connector.source)
                await uow.source_status.record_error(connector.source, str(exc))
                summary.errors.append(f"{connector.source}: fetch failed: {exc}")
                if not ctx.dry_run:
                    await uow.commit()
                continue

            summary.items_fetched += len(pdfs)
            _log.info("pipeline.fetched", source=connector.source, count=len(pdfs))

            for pdf in pdfs:
                step += 1
                try:
                    await _process_pdf(ctx, uow, pdf, run_id=run_id, step=step, summary=summary)
                except Exception as exc:  # isolate one item's failure
                    _log.exception(
                        "pipeline.item_failed",
                        source=connector.source,
                        message_id=pdf.message.message_id,
                    )
                    summary.errors.append(f"{connector.source}/{pdf.message.message_id}: {exc}")
                finally:
                    if not ctx.dry_run:
                        await uow.commit()

            if not ctx.dry_run:
                await uow.commit()  # persist the advanced watermark even with zero PDFs

        await uow.runs.finish(
            run,
            items_fetched=summary.items_fetched,
            items_extracted=summary.items_extracted,
            items_stored=summary.items_stored,
            items_flagged=summary.items_flagged,
            error_summary="; ".join(summary.errors) or None,
        )
        if not ctx.dry_run:
            await uow.commit()
        # dry_run: never commit — the UnitOfWork rolls back on exit (no persisted writes).

    _log.info(
        "pipeline.run_complete",
        run_id=summary.run_id,
        fetched=summary.items_fetched,
        extracted=summary.items_extracted,
        stored=summary.items_stored,
        flagged=summary.items_flagged,
        errors=len(summary.errors),
        dry_run=summary.dry_run,
    )
    return summary


async def _record_run_start(
    uow: UnitOfWork,
    run_id: int | None,
    ctx: WorkerContext,
    since_dt: datetime | None,
) -> None:
    """Audit the run's mode so the dashboard shows whether it was a dry/no-send run."""
    await uow.agent_events.add(
        event_type=AgentEventType.DECISION,
        summary=(
            f"Run started (dry_run={ctx.dry_run}, "
            f"billtobox_send={'on' if ctx.billtobox_send_enabled else 'off'})"
        ),
        run_id=run_id,
        step=0,
        tool="run_once",
        outputs={
            "dry_run": ctx.dry_run,
            "billtobox_send_enabled": ctx.billtobox_send_enabled,
            "since_override": since_dt.isoformat() if since_dt else None,
        },
    )


async def _process_pdf(
    ctx: WorkerContext,
    uow: UnitOfWork,
    pdf: FetchedPdf,
    *,
    run_id: int | None,
    step: int,
    summary: RunSummary,
) -> None:
    config = ctx.config
    ref = pdf.message
    pdf_bytes = pdf.pdf_bytes

    # 1. cheap, model-free gate — never reaches the paid model call.
    if not prefilter(ref, pdf_bytes, config.prefilter):
        await uow.agent_events.add(
            event_type=AgentEventType.DECISION,
            summary=f"Pre-filter rejected message {ref.message_id}",
            run_id=run_id,
            step=step,
            tool="prefilter",
            outputs={"source": ref.source, "message_id": ref.message_id, "subject": ref.subject},
        )
        _log.info("pipeline.prefiltered_out", message_id=ref.message_id)
        return

    # 2. content-hash dedup (skips before any model call; emits its own event on a hit).
    content_hash = compute_content_hash(pdf_bytes)
    if await check_duplicate(uow, content_hash, run_id=run_id, step=step):
        _log.info("pipeline.duplicate_skipped", message_id=ref.message_id)
        return

    # 3. create the invoice row so downstream tools can link their agent_events to it.
    invoice = await uow.invoices.add(
        Invoice(
            source=ref.source,
            source_message_id=ref.message_id,
            content_hash=content_hash,
            run_id=run_id,
        )
    )
    invoice_id = invoice.id

    # 4. extract — a failure flags for review rather than losing the item.
    try:
        result = await extract_invoice(
            ctx.anthropic_client,
            pdf_bytes,
            config=config.anthropic,
            confidence_threshold=config.extraction.confidence_threshold,
            uow=uow,
            run_id=run_id,
            invoice_id=invoice_id,
            step=step,
        )
    except Exception as exc:  # any extraction failure → human review
        await flag_for_review(
            uow, invoice_id, f"extraction failed: {exc}", run_id=run_id, step=step
        )
        summary.items_flagged += 1
        return

    summary.items_extracted += 1

    # 5. persist the extracted fields + computed accounting period.
    fy_label, quarter = _period_for(config, result.invoice_date)
    await uow.invoices.record_extraction(
        invoice_id,
        vendor=result.vendor,
        invoice_date=result.invoice_date,
        amount=result.amount,
        currency=result.currency,
        confidence=result.confidence,
        fy_label=fy_label,
        quarter=quarter,
    )

    # 6. confidence gate: auto-approve (with a usable date) → store; otherwise flag.
    if result.auto_approve and result.invoice_date is not None:
        await _store_approved(
            ctx,
            uow,
            pdf,
            invoice_date=result.invoice_date,
            amount=result.amount,
            invoice_id=invoice_id,
            run_id=run_id,
            step=step,
            summary=summary,
        )
    else:
        await flag_for_review(uow, invoice_id, _flag_reason(result), run_id=run_id, step=step)
        summary.items_flagged += 1


async def _store_approved(
    ctx: WorkerContext,
    uow: UnitOfWork,
    pdf: FetchedPdf,
    *,
    invoice_date: date,
    amount: float | None,
    invoice_id: int,
    run_id: int | None,
    step: int,
    summary: RunSummary,
) -> None:
    source = pdf.message.source
    fy_label, quarter = period_for(
        invoice_date,
        ctx.config.accounting.fiscal_year_start_month,
        ctx.config.accounting.fy_label_prefix,
    )
    folder_path = f"{ctx.drive.root_folder_name}/{fy_label}/{quarter}"

    if ctx.dry_run:
        filename = build_filename(source, invoice_date, amount)
        _log.info(
            "pipeline.dry_run_would_store",
            invoice_id=invoice_id,
            path=f"{folder_path}/{filename}",
        )
        summary.items_stored += 1
        return

    try:
        folder_id = await ensure_quarter_folder(
            ctx.drive,
            invoice_date,
            accounting=ctx.config.accounting,
            uow=uow,
            run_id=run_id,
            invoice_id=invoice_id,
            step=step,
        )
        await store_pdf_to_drive(
            ctx.drive,
            pdf.pdf_bytes,
            InvoiceFileFields(source=source, invoice_date=invoice_date, amount=amount),
            folder_id=folder_id,
            folder_path=folder_path,
            uow=uow,
            invoice_id=invoice_id,
            run_id=run_id,
            step=step,
        )
        summary.items_stored += 1
    except Exception as exc:  # a Drive failure flags rather than crashes the run
        _log.exception("pipeline.store_failed", invoice_id=invoice_id)
        await flag_for_review(
            uow, invoice_id, f"drive upload failed: {exc}", run_id=run_id, step=step
        )
        summary.items_flagged += 1


def _period_for(config: AppConfig, invoice_date: date | None) -> tuple[str | None, str | None]:
    if invoice_date is None:
        return None, None
    return period_for(
        invoice_date,
        config.accounting.fiscal_year_start_month,
        config.accounting.fy_label_prefix,
    )


def _flag_reason(result: ExtractionResult) -> str:
    if not result.is_invoice:
        return "not classified as an invoice"
    if not result.auto_approve:
        return f"confidence {result.confidence:.2f} below threshold"
    return "missing invoice_date (cannot determine accounting quarter)"
