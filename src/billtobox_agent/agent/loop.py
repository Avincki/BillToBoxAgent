"""The agent tool-calling loop (task 21).

Refactors the task-17 linear ``run_once`` into a Claude-driven loop (Messages API
tool-use pattern — `claude-api` reference): the model decides which tool to call
next, the harness executes it and returns the result, until the model stops. The
deterministic pipeline functions from tasks 8-16 become the tool *implementations*;
the orchestration order is the model's to choose.

Design notes that make this safe and re-entrant:

* **PDF bytes never enter the model's context.** Tools reference fetched PDFs by a
  small ``pdf_ref`` handle; the harness holds the bytes. Tool results are compact
  JSON (ids, fields, decisions) — the audit trail (`agent_events`) keeps the full,
  redacted record.
* **Re-entrancy.** ``search_mail`` skips messages already turned into invoices
  (``source_message_id``) and advances the watermark; ``check_duplicate`` skips a
  PDF whose ``content_hash`` is already stored. A crash + restart therefore
  reprocesses nothing — the recovery state is watermark + dedup + ``agent_events``.
* **Self-correction.** ``store_pdf_to_drive`` retries a transient Drive failure up
  to ``max_drive_attempts`` before returning an error; the model then flags the item
  for review. The model can also call ``get_agent_events`` to inspect its own prior
  steps.

The 10th tool, ``email_to_billtobox``, is task 20 (Phase 6) and not registered yet —
the autonomous agent never *sends*; it queues for the human-approved send.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date
from typing import Any, cast

import anthropic
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from billtobox_agent.config.models import AppConfig
from billtobox_agent.data import Invoice, UnitOfWork
from billtobox_agent.drive import (
    DriveConnector,
    InvoiceFileFields,
    ensure_quarter_folder,
    store_pdf_to_drive,
)
from billtobox_agent.extraction import ExtractionResult, extract_invoice, period_for
from billtobox_agent.mail.base import FetchedPdf, MailConnector
from billtobox_agent.pipeline import (
    RunSummary,
    check_duplicate,
    compute_content_hash,
    flag_for_review,
    queue_billtobox_upload,
)

_log = structlog.get_logger(__name__)

# Orchestration turns emit only small tool-use blocks; 4096 is ample headroom.
_ORCHESTRATION_MAX_TOKENS = 4096


@dataclass(frozen=True)
class AgentContext:
    """Everything the agent loop needs — injected so tests pass fakes."""

    config: AppConfig
    session_factory: async_sessionmaker[AsyncSession]
    mail_connectors: Mapping[str, MailConnector]
    drive: DriveConnector
    anthropic_client: anthropic.Anthropic
    max_steps: int = 60  # safety cap on loop iterations
    max_drive_attempts: int = 3  # transient Drive failures retried before flagging


@dataclass
class _PdfEntry:
    fetched: FetchedPdf
    content_hash: str
    source: str
    message_id: str
    invoice_id: int | None = None
    result: ExtractionResult | None = None
    folder_id: str | None = None
    folder_path: str | None = None


@dataclass
class _AgentState:
    ctx: AgentContext
    uow: UnitOfWork
    run_id: int
    summary: RunSummary
    pdfs: dict[str, _PdfEntry] = field(default_factory=dict)
    refs: dict[tuple[str, str], Any] = field(default_factory=dict)
    counter: int = 0
    step: int = 0


# ----- tool schemas -----------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_mail",
        "description": (
            "List new candidate invoice messages for one source (gmail/outlook). "
            "Already-processed messages and ones older than the watermark are excluded."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"source": {"type": "string", "enum": ["gmail", "outlook"]}},
            "required": ["source"],
        },
    },
    {
        "name": "get_pdf",
        "description": "Download the PDF for a message. Returns a pdf_ref handle and content_hash.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "message_id": {"type": "string"},
            },
            "required": ["source", "message_id"],
        },
    },
    {
        "name": "check_duplicate",
        "description": (
            "Check whether a content_hash was already processed. If is_duplicate is true, "
            "skip this PDF entirely — do not extract or store it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"content_hash": {"type": "string"}},
            "required": ["content_hash"],
        },
    },
    {
        "name": "extract_invoice",
        "description": "Extract invoice fields from a fetched PDF (returns fields + auto_approve).",
        "input_schema": {
            "type": "object",
            "properties": {"pdf_ref": {"type": "string"}},
            "required": ["pdf_ref"],
        },
    },
    {
        "name": "ensure_quarter_folder",
        "description": "Find-or-create the Drive quarter folder for an extracted invoice.",
        "input_schema": {
            "type": "object",
            "properties": {"pdf_ref": {"type": "string"}},
            "required": ["pdf_ref"],
        },
    },
    {
        "name": "store_pdf_to_drive",
        "description": (
            "Upload the PDF into a quarter folder. On a transient failure it retries, then "
            "returns an error — flag the item for review if it cannot be stored."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pdf_ref": {"type": "string"},
                "folder_id": {"type": "string"},
            },
            "required": ["pdf_ref", "folder_id"],
        },
    },
    {
        "name": "queue_billtobox_upload",
        "description": (
            "Mark a stored invoice human-approved for the Billtobox send. Use only for "
            "high-confidence invoices from a known/trusted vendor. Sends nothing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"pdf_ref": {"type": "string"}},
            "required": ["pdf_ref"],
        },
    },
    {
        "name": "flag_for_review",
        "description": "Route an item to the human exceptions queue with a reason.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pdf_ref": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["pdf_ref", "reason"],
        },
    },
    {
        "name": "get_agent_events",
        "description": "Inspect prior redacted agent steps (to self-correct / avoid repeats).",
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "integer"},
                "run_id": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            "required": [],
        },
    },
]


# ----- tool handlers ----------------------------------------------------------


def _period(config: AppConfig, invoice_date: date | None) -> tuple[str | None, str | None]:
    if invoice_date is None:
        return None, None
    return period_for(
        invoice_date,
        config.accounting.fiscal_year_start_month,
        config.accounting.fy_label_prefix,
    )


async def _h_search_mail(state: _AgentState, args: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    source = str(args.get("source", ""))
    connector = state.ctx.mail_connectors.get(source)
    if connector is None:
        return {"error": f"no connector for source {source!r}"}, True

    stored = await state.uow.source_status.get_watermark(connector.source)
    since = stored.replace(tzinfo=UTC) if (stored is not None and stored.tzinfo is None) else stored
    refs = await asyncio.to_thread(connector.search, since)

    messages: list[dict[str, Any]] = []
    newest = since
    for ref in refs:
        newest = ref.received_at if newest is None else max(newest, ref.received_at)
        if await state.uow.invoices.exists_source_message_id(connector.source, ref.message_id):
            continue  # already an invoice from a prior run — re-entrancy
        state.refs[(connector.source, ref.message_id)] = ref
        messages.append(
            {
                "message_id": ref.message_id,
                "subject": ref.subject,
                "sender": ref.sender,
                "received_at": ref.received_at.isoformat(),
            }
        )
    if newest is not None and newest != since:
        await state.uow.source_status.set_watermark(connector.source, newest)
    return {"source": connector.source, "messages": messages}, False


async def _h_get_pdf(state: _AgentState, args: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    source = str(args.get("source", ""))
    message_id = str(args.get("message_id", ""))
    ref = state.refs.get((source, message_id))
    if ref is None:
        return {"error": "unknown message_id; call search_mail first"}, True
    connector = state.ctx.mail_connectors[source]
    pdfs = await asyncio.to_thread(connector.download_pdfs, ref)
    if not pdfs:
        return {"error": "no PDF attachments on this message"}, True

    fetched = pdfs[0]  # one invoice PDF per message in this flow
    state.counter += 1
    pdf_ref = f"pdf-{state.counter}"
    content_hash = compute_content_hash(fetched.pdf_bytes)
    state.pdfs[pdf_ref] = _PdfEntry(
        fetched=fetched, content_hash=content_hash, source=source, message_id=message_id
    )
    state.summary.items_fetched += 1
    return {
        "pdf_ref": pdf_ref,
        "filename": fetched.filename,
        "content_hash": content_hash,
        "size_bytes": len(fetched.pdf_bytes),
    }, False


async def _h_check_duplicate(
    state: _AgentState, args: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    content_hash = str(args.get("content_hash", ""))
    is_dup = await check_duplicate(state.uow, content_hash, run_id=state.run_id, step=state.step)
    return {"content_hash": content_hash, "is_duplicate": is_dup}, False


async def _ensure_invoice_row(state: _AgentState, entry: _PdfEntry) -> int:
    if entry.invoice_id is None:
        invoice = await state.uow.invoices.add(
            Invoice(
                source=entry.source,
                source_message_id=entry.message_id,
                content_hash=entry.content_hash,
                run_id=state.run_id,
            )
        )
        entry.invoice_id = invoice.id
    return entry.invoice_id


async def _h_extract_invoice(
    state: _AgentState, args: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    pdf_ref = str(args.get("pdf_ref", ""))
    entry = state.pdfs.get(pdf_ref)
    if entry is None:
        return {"error": "unknown pdf_ref"}, True
    invoice_id = await _ensure_invoice_row(state, entry)
    try:
        result = await extract_invoice(
            state.ctx.anthropic_client,
            entry.fetched.pdf_bytes,
            config=state.ctx.config.anthropic,
            confidence_threshold=state.ctx.config.extraction.confidence_threshold,
            uow=state.uow,
            run_id=state.run_id,
            invoice_id=invoice_id,
            step=state.step,
        )
    except Exception as exc:
        return {"error": f"extraction failed: {exc}"}, True

    entry.result = result
    state.summary.items_extracted += 1
    fy_label, quarter = _period(state.ctx.config, result.invoice_date)
    await state.uow.invoices.record_extraction(
        invoice_id,
        vendor=result.vendor,
        invoice_date=result.invoice_date,
        amount=result.amount,
        currency=result.currency,
        confidence=result.confidence,
        fy_label=fy_label,
        quarter=quarter,
    )
    return {
        "pdf_ref": pdf_ref,
        "invoice_id": invoice_id,
        "is_invoice": result.is_invoice,
        "confidence": result.confidence,
        "vendor": result.vendor,
        "invoice_date": result.invoice_date.isoformat() if result.invoice_date else None,
        "amount": result.amount,
        "currency": result.currency,
        "auto_approve": result.auto_approve,
    }, False


async def _h_ensure_quarter_folder(
    state: _AgentState, args: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    pdf_ref = str(args.get("pdf_ref", ""))
    entry = state.pdfs.get(pdf_ref)
    if entry is None or entry.result is None:
        return {"error": "extract the invoice first"}, True
    if entry.result.invoice_date is None or entry.invoice_id is None:
        return {"error": "no invoice_date; cannot determine the quarter"}, True

    folder_id = await ensure_quarter_folder(
        state.ctx.drive,
        entry.result.invoice_date,
        accounting=state.ctx.config.accounting,
        uow=state.uow,
        run_id=state.run_id,
        invoice_id=entry.invoice_id,
        step=state.step,
    )
    fy_label, quarter = _period(state.ctx.config, entry.result.invoice_date)
    entry.folder_id = folder_id
    entry.folder_path = f"{state.ctx.drive.root_folder_name}/{fy_label}/{quarter}"
    return {"pdf_ref": pdf_ref, "folder_id": folder_id, "folder_path": entry.folder_path}, False


async def _h_store_pdf_to_drive(
    state: _AgentState, args: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    pdf_ref = str(args.get("pdf_ref", ""))
    entry = state.pdfs.get(pdf_ref)
    if entry is None or entry.result is None or entry.result.invoice_date is None:
        return {"error": "extract + ensure_quarter_folder first"}, True
    # Trust the harness-tracked folder from ensure_quarter_folder over the echoed arg.
    folder_id = entry.folder_id or args.get("folder_id")
    if not folder_id or entry.folder_path is None or entry.invoice_id is None:
        return {"error": "call ensure_quarter_folder first"}, True

    fields = InvoiceFileFields(
        source=entry.source, invoice_date=entry.result.invoice_date, amount=entry.result.amount
    )
    last_error: Exception | None = None
    for _attempt in range(state.ctx.max_drive_attempts):
        try:
            file_id, drive_path = await store_pdf_to_drive(
                state.ctx.drive,
                entry.fetched.pdf_bytes,
                fields,
                folder_id=str(folder_id),
                folder_path=entry.folder_path,
                uow=state.uow,
                run_id=state.run_id,
                invoice_id=entry.invoice_id,
                step=state.step,
            )
        except Exception as exc:  # transient Drive failure — retry, then give up
            last_error = exc
            continue
        state.summary.items_stored += 1
        return {"pdf_ref": pdf_ref, "drive_file_id": file_id, "drive_path": drive_path}, False

    return {
        "error": f"drive upload failed after {state.ctx.max_drive_attempts} attempts: {last_error}"
    }, True


async def _h_queue_billtobox_upload(
    state: _AgentState, args: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    pdf_ref = str(args.get("pdf_ref", ""))
    entry = state.pdfs.get(pdf_ref)
    if entry is None or entry.invoice_id is None:
        return {"error": "unknown or unextracted pdf_ref"}, True
    await queue_billtobox_upload(state.uow, entry.invoice_id, run_id=state.run_id, step=state.step)
    return {"pdf_ref": pdf_ref, "status": "upload_approved"}, False


async def _h_flag_for_review(
    state: _AgentState, args: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    pdf_ref = str(args.get("pdf_ref", ""))
    reason = str(args.get("reason", "flagged by agent"))
    entry = state.pdfs.get(pdf_ref)
    if entry is None:
        return {"error": "unknown pdf_ref"}, True
    invoice_id = await _ensure_invoice_row(state, entry)
    await flag_for_review(state.uow, invoice_id, reason, run_id=state.run_id, step=state.step)
    state.summary.items_flagged += 1
    return {"pdf_ref": pdf_ref, "status": "reviewed"}, False


async def _h_get_agent_events(
    state: _AgentState, args: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    invoice_id = args.get("invoice_id")
    run_id = args.get("run_id")
    limit = min(int(args.get("limit", 50)), 200)
    events = await state.uow.agent_events.list(
        run_id=int(run_id) if run_id is not None else None,
        invoice_id=int(invoice_id) if invoice_id is not None else None,
        limit=limit,
    )
    return {
        "events": [
            {
                "step": e.step,
                "event_type": e.event_type,
                "tool": e.tool,
                "level": e.level,
                "summary": e.summary,
                "outputs": e.outputs_json,  # already redacted at write time
            }
            for e in events
        ]
    }, False


_HANDLERS = {
    "search_mail": _h_search_mail,
    "get_pdf": _h_get_pdf,
    "check_duplicate": _h_check_duplicate,
    "extract_invoice": _h_extract_invoice,
    "ensure_quarter_folder": _h_ensure_quarter_folder,
    "store_pdf_to_drive": _h_store_pdf_to_drive,
    "queue_billtobox_upload": _h_queue_billtobox_upload,
    "flag_for_review": _h_flag_for_review,
    "get_agent_events": _h_get_agent_events,
}


async def _dispatch(
    state: _AgentState, name: str, tool_input: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool {name!r}"}, True
    try:
        return await handler(state, tool_input)
    except Exception as exc:  # never let one tool kill the loop
        _log.exception("agent.tool_failed", tool=name)
        return {"error": f"{type(exc).__name__}: {exc}"}, True


# ----- the loop ---------------------------------------------------------------


def _system_prompt(config: AppConfig) -> str:
    vendors = ", ".join(config.billtobox.known_vendors) or "(none configured)"
    return (
        "You are the BillToBox invoice-processing agent. For each enabled mail source, find new "
        "invoice PDFs and file them to Google Drive by accounting quarter.\n\n"
        "Per source: search_mail(source); then for each message: get_pdf -> check_duplicate "
        "(skip the PDF entirely if is_duplicate) -> extract_invoice. If it is not an invoice, or "
        "auto_approve is false, or there is no invoice_date: flag_for_review with a clear reason. "
        "Otherwise: ensure_quarter_folder then store_pdf_to_drive; if store keeps failing, "
        "flag_for_review. Only call queue_billtobox_upload for high-confidence invoices from a "
        f"known/trusted vendor (configured known vendors: {vendors}); it never sends, only marks "
        "the item human-approved.\n\n"
        "Never reprocess a content_hash that check_duplicate reports as a duplicate. Use "
        "get_agent_events if unsure whether something was already done. Stop when every source is "
        "processed."
    )


def _kickoff(config: AppConfig) -> str:
    sources = ", ".join(s.value for s in config.sources.polling)
    return f"Process new invoices now. Enabled sources, in order: {sources}."


def _create(ctx: AgentContext, system: str, messages: list[dict[str, Any]]) -> Any:
    # tools/messages are raw dicts (the documented manual-loop shape); the SDK accepts
    # them at runtime, but its param types are stricter — cast past the static check.
    return ctx.anthropic_client.messages.create(
        model=ctx.config.anthropic.model,
        max_tokens=_ORCHESTRATION_MAX_TOKENS,
        system=system,
        tools=cast("Any", TOOLS),
        messages=cast("Any", messages),
    )


async def run_agent(ctx: AgentContext) -> RunSummary:
    """Drive the tool-calling loop until the model stops, returning a RunSummary."""
    summary = RunSummary(run_id=None)

    async with UnitOfWork(ctx.session_factory) as uow:
        run = await uow.runs.start()
        run_id = run.id
        summary.run_id = run_id
        await uow.commit()  # stable run row for FK references

        state = _AgentState(ctx=ctx, uow=uow, run_id=run_id, summary=summary)
        system = _system_prompt(ctx.config)
        messages: list[dict[str, Any]] = [{"role": "user", "content": _kickoff(ctx.config)}]

        for _turn in range(ctx.max_steps):
            response = await asyncio.to_thread(_create, ctx, system, messages)
            stop_reason = getattr(response, "stop_reason", None)
            if stop_reason == "pause_turn":  # server-tool pause — resume
                messages.append({"role": "assistant", "content": response.content})
                continue
            if stop_reason in ("refusal",):
                _log.warning("agent.refusal", stop_reason=stop_reason)
                break

            tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                break  # end_turn / no tools — the agent is done

            messages.append({"role": "assistant", "content": response.content})
            results: list[dict[str, Any]] = []
            for block in tool_uses:
                state.step += 1
                output, is_error = await _dispatch(state, block.name, dict(block.input))
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(output, default=str),
                        "is_error": is_error,
                    }
                )
            messages.append({"role": "user", "content": results})
            await uow.commit()  # persist progress after each turn (re-entrancy)

        await uow.runs.finish(
            run,
            items_fetched=summary.items_fetched,
            items_extracted=summary.items_extracted,
            items_stored=summary.items_stored,
            items_flagged=summary.items_flagged,
            error_summary="; ".join(summary.errors) or None,
        )
        await uow.commit()

    _log.info(
        "agent.run_complete",
        run_id=summary.run_id,
        fetched=summary.items_fetched,
        extracted=summary.items_extracted,
        stored=summary.items_stored,
        flagged=summary.items_flagged,
    )
    return summary
