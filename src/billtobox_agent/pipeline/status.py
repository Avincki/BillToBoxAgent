"""Disposition tools — the worker's terminal status writes (task 16).

Two SQLite-only pipeline tools that decide what happens to an invoice after
extraction, neither performing any external I/O:

* :func:`flag_for_review` routes a low-confidence or failed item away from
  auto-approval (status → ``reviewed``); the dashboard's exceptions queue keys on
  that status, and the *reason* is recorded in the ``agent_events`` audit trail
  (the locked schema has no per-invoice notes column — the audit trail is where
  the reason lives, per decisions.md §2).
* :func:`queue_billtobox_upload` marks an item human-approved for the Billtobox
  send (status → ``upload_approved``) and **sends nothing** — the actual SMTP send
  is task 20, guarded on exactly this status.

Each emits a redacted ``DECISION`` ``agent_events`` row so the disposition is
auditable, mirroring :func:`billtobox_agent.pipeline.dedup.check_duplicate`.
"""

from __future__ import annotations

from billtobox_agent.data import UnitOfWork
from billtobox_agent.data.models import AgentEventType, InvoiceStatus


async def flag_for_review(
    uow: UnitOfWork,
    invoice_id: int,
    reason: str,
    *,
    run_id: int | None = None,
    step: int = 0,
) -> None:
    """Set ``invoice_id`` to ``reviewed`` and record ``reason`` in the audit trail."""
    await uow.invoices.set_status(invoice_id, InvoiceStatus.REVIEWED)
    await uow.agent_events.add(
        event_type=AgentEventType.DECISION,
        summary=f"Flagged for review: {reason}",
        run_id=run_id,
        invoice_id=invoice_id,
        step=step,
        tool="flag_for_review",
        outputs={"status": InvoiceStatus.REVIEWED.value, "reason": reason},
    )


async def queue_billtobox_upload(
    uow: UnitOfWork,
    invoice_id: int,
    *,
    run_id: int | None = None,
    step: int = 0,
) -> None:
    """Set ``invoice_id`` to ``upload_approved`` (human-approved send). Sends nothing."""
    await uow.invoices.set_status(invoice_id, InvoiceStatus.UPLOAD_APPROVED)
    await uow.agent_events.add(
        event_type=AgentEventType.DECISION,
        summary="Queued for Billtobox upload (awaiting send)",
        run_id=run_id,
        invoice_id=invoice_id,
        step=step,
        tool="queue_billtobox_upload",
        outputs={"status": InvoiceStatus.UPLOAD_APPROVED.value},
    )
