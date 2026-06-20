"""Dashboard steering tools (task 19): human edits + re-extraction.

* :func:`edit_invoice` applies a field edit and, **only when the new
  ``invoice_date`` moves the accounting quarter of an already-stored PDF**, moves
  the Drive file to the new ``Invoices/<fy>/<quarter>/`` folder (``files.update``).
* :func:`reextract_invoice` re-downloads the stored PDF from Drive and runs Claude
  extraction again, persisting the refreshed fields.

Both go through the same repositories + Drive connector + ``agent_events`` as the
worker, so a dashboard edit is as auditable as an automated step. The web layer
calls these; it never writes the database or Drive directly.
"""

from __future__ import annotations

import asyncio
from datetime import date

import anthropic

from billtobox_agent.config.models import AccountingConfig, AnthropicConfig, ExtractionConfig
from billtobox_agent.data import UnitOfWork
from billtobox_agent.data.models import AgentEventType
from billtobox_agent.drive import DriveConnector, build_filename
from billtobox_agent.extraction import ExtractionResult, extract_invoice, period_for


def _period(
    accounting: AccountingConfig, invoice_date: date | None
) -> tuple[str | None, str | None]:
    if invoice_date is None:
        return None, None
    return period_for(
        invoice_date,
        accounting.fiscal_year_start_month,
        accounting.fy_label_prefix,
    )


async def edit_invoice(
    uow: UnitOfWork,
    drive: DriveConnector | None,
    invoice_id: int,
    *,
    vendor: str | None,
    invoice_date: date | None,
    amount: float | None,
    currency: str | None,
    accounting: AccountingConfig,
    run_id: int | None = None,
    step: int = 0,
) -> None:
    """Apply a field edit; move the stored PDF only if the quarter changed.

    ``drive`` may be ``None`` for a field-only edit; it is required only when a
    stored PDF must follow a quarter change (raises if unavailable in that case).
    """
    invoice = await uow.invoices.get(invoice_id)
    if invoice is None:
        raise ValueError(f"invoice {invoice_id} not found")

    old_period = _period(accounting, invoice.invoice_date)
    drive_file_id = invoice.drive_file_id
    old_drive_path = invoice.drive_path
    source = invoice.source

    new_fy, new_quarter = _period(accounting, invoice_date)
    await uow.invoices.update_fields(
        invoice_id,
        vendor=vendor,
        invoice_date=invoice_date,
        amount=amount,
        currency=currency,
        fy_label=new_fy,
        quarter=new_quarter,
    )

    moved = False
    new_drive_path = old_drive_path
    quarter_changed = (new_fy, new_quarter) != old_period
    if drive_file_id and invoice_date is not None and quarter_changed:
        if drive is None:
            raise ValueError("Drive connector unavailable; cannot move file for the new quarter")
        fy_label, quarter = period_for(
            invoice_date,
            accounting.fiscal_year_start_month,
            accounting.fy_label_prefix,
        )
        new_folder_id = await asyncio.to_thread(drive.ensure_quarter_path, fy_label, quarter)
        await asyncio.to_thread(drive.move_file, drive_file_id, new_folder_id)
        filename = (
            old_drive_path.rsplit("/", 1)[-1]
            if old_drive_path
            else build_filename(source, invoice_date, amount)
        )
        new_drive_path = f"{drive.root_folder_name}/{fy_label}/{quarter}/{filename}"
        await uow.invoices.set_drive_path(invoice_id, new_drive_path)
        moved = True

    summary = "Invoice fields edited" + (" + Drive file moved" if moved else "")
    await uow.agent_events.add(
        event_type=AgentEventType.DECISION,
        summary=summary,
        run_id=run_id,
        invoice_id=invoice_id,
        step=step,
        tool="edit_invoice",
        outputs={
            "vendor": vendor,
            "invoice_date": invoice_date.isoformat() if invoice_date else None,
            "amount": amount,
            "currency": currency,
            "fy_label": new_fy,
            "quarter": new_quarter,
            "drive_moved": moved,
            "drive_path": new_drive_path,
        },
    )


async def reextract_invoice(
    uow: UnitOfWork,
    drive: DriveConnector,
    anthropic_client: anthropic.Anthropic,
    invoice_id: int,
    *,
    anthropic_config: AnthropicConfig,
    extraction_config: ExtractionConfig,
    accounting: AccountingConfig,
    run_id: int | None = None,
    step: int = 0,
) -> ExtractionResult:
    """Re-download the stored PDF from Drive and run extraction again."""
    invoice = await uow.invoices.get(invoice_id)
    if invoice is None:
        raise ValueError(f"invoice {invoice_id} not found")
    if not invoice.drive_file_id:
        raise ValueError(f"invoice {invoice_id} has no stored PDF to re-extract")

    pdf_bytes = await asyncio.to_thread(drive.download_pdf, invoice.drive_file_id)
    result = await extract_invoice(
        anthropic_client,
        pdf_bytes,
        config=anthropic_config,
        confidence_threshold=extraction_config.confidence_threshold,
        uow=uow,
        run_id=run_id,
        invoice_id=invoice_id,
        step=step,
    )
    new_fy, new_quarter = _period(accounting, result.invoice_date)
    await uow.invoices.record_extraction(
        invoice_id,
        vendor=result.vendor,
        invoice_date=result.invoice_date,
        amount=result.amount,
        currency=result.currency,
        confidence=result.confidence,
        fy_label=new_fy,
        quarter=new_quarter,
    )
    return result
