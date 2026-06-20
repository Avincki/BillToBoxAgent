"""PDF upload pipeline tool (task 15).

Builds a sanitised filename from the invoice fields, uploads the PDF to the
quarter folder produced by :func:`ensure_quarter_folder` (task 14) — suffixing
``_2``/``_3``/... on a name collision — then records the resulting Drive file id
and logical path on the ``invoices`` row (status → ``stored``). Drive calls are
synchronous (googleapiclient), so they run in a worker thread; each call and
result is written as a redacted ``agent_events`` row (the PDF bytes are never
stored — :func:`redact` replaces them with a hash + length).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date

from billtobox_agent.data import UnitOfWork
from billtobox_agent.data.models import AgentEventLevel, AgentEventType
from billtobox_agent.drive.connector import DriveConnector

# Characters illegal on common filesystems (and control chars) — stripped so the
# Drive filename is portable if the PDF is later downloaded.
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class InvoiceFileFields:
    """The invoice fields used to name the stored PDF (``{source}_{date}_{amount}``)."""

    source: str
    invoice_date: date
    amount: float | None


def _sanitise(component: str) -> str:
    """Drop illegal characters and collapse internal whitespace in one name part."""
    cleaned = _ILLEGAL.sub("", component)
    return _WHITESPACE.sub(" ", cleaned).strip()


def build_filename(source: str, invoice_date: date, amount: float | None) -> str:
    """Return ``{source}_{YYYY-MM-DD}_{amount}.pdf``, each part sanitised.

    The amount is dot-decimal with two places (``100.00``); a missing amount
    becomes ``unknown`` so the filename stays well-formed.
    """
    amount_str = f"{amount:.2f}" if amount is not None else "unknown"
    parts = (source, invoice_date.isoformat(), amount_str)
    stem = "_".join(_sanitise(part) for part in parts)
    return f"{stem}.pdf"


async def store_pdf_to_drive(
    connector: DriveConnector,
    pdf_bytes: bytes,
    fields: InvoiceFileFields,
    *,
    folder_id: str,
    folder_path: str,
    uow: UnitOfWork,
    invoice_id: int,
    run_id: int | None = None,
    step: int = 0,
) -> tuple[str, str]:
    """Upload ``pdf_bytes`` to ``folder_id`` and record it on the invoice.

    Returns ``(drive_file_id, drive_path)`` where ``drive_path`` is the logical
    path ``<folder_path>/<final_name>``. The invoice row is updated with both and
    its status set to ``stored``.
    """
    base_name = build_filename(fields.source, fields.invoice_date, fields.amount)

    await uow.agent_events.add(
        event_type=AgentEventType.TOOL_CALL,
        summary=f"store_pdf_to_drive {base_name}",
        run_id=run_id,
        invoice_id=invoice_id,
        step=step,
        tool="store_pdf_to_drive",
        inputs={
            "filename": base_name,
            "folder_id": folder_id,
            "source": fields.source,
            "invoice_date": fields.invoice_date.isoformat(),
            "amount": fields.amount,
            "pdf_bytes": pdf_bytes,
        },
    )
    try:
        file_id, final_name = await asyncio.to_thread(
            connector.store_pdf, base_name, pdf_bytes, folder_id
        )
    except Exception as exc:
        await uow.agent_events.add(
            event_type=AgentEventType.ERROR,
            summary=f"store_pdf_to_drive failed: {type(exc).__name__}",
            run_id=run_id,
            invoice_id=invoice_id,
            step=step,
            tool="store_pdf_to_drive",
            level=AgentEventLevel.ERROR,
            outputs={"error": str(exc)},
        )
        raise

    drive_path = f"{folder_path}/{final_name}"
    await uow.invoices.mark_stored(invoice_id, drive_file_id=file_id, drive_path=drive_path)

    await uow.agent_events.add(
        event_type=AgentEventType.TOOL_RESULT,
        summary=f"store_pdf_to_drive -> {drive_path}",
        run_id=run_id,
        invoice_id=invoice_id,
        step=step,
        tool="store_pdf_to_drive",
        outputs={"drive_file_id": file_id, "drive_path": drive_path},
    )
    return file_id, drive_path
