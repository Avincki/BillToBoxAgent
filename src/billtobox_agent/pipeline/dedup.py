"""Content-hash deduplication (task 12).

A PDF's SHA-256 content hash is the dedup key: the ``invoices`` table carries a
UNIQUE constraint on ``content_hash`` (``data/models.py``), so a PDF that has
already been processed — even if it arrives again from a different message or a
different source — is recognised by its bytes alone and skipped *before* any
(paid, slower) Claude call.

:func:`compute_content_hash` derives the key from the raw PDF bytes;
:func:`check_duplicate` queries it and, on a hit, records a redacted
``agent_events`` row so the silent skip is visible in the dashboard audit trail.
"""

from __future__ import annotations

from billtobox_agent.data import UnitOfWork
from billtobox_agent.data.models import AgentEventType
from billtobox_agent.utils.hashing import sha256_hex


def compute_content_hash(pdf_bytes: bytes) -> str:
    """SHA-256 hex digest of the PDF bytes — the dedup key stored on the invoice."""
    return sha256_hex(pdf_bytes)


async def check_duplicate(
    uow: UnitOfWork,
    content_hash: str,
    *,
    run_id: int | None = None,
    step: int = 0,
) -> bool:
    """Return ``True`` if ``content_hash`` was already processed (so the caller skips it).

    On a duplicate, emit a ``DECISION`` agent-event linked to the invoice that
    already holds the hash, so the skip is auditable. Returns ``False`` for an
    unseen hash — the caller then proceeds to extract and insert. Inserts nothing
    itself; the UNIQUE ``content_hash`` constraint is the backstop if two runs race.
    """
    existing = await uow.invoices.get_by_content_hash(content_hash)
    if existing is None:
        return False

    await uow.agent_events.add(
        event_type=AgentEventType.DECISION,
        summary="Duplicate PDF skipped — content_hash already processed",
        run_id=run_id,
        invoice_id=existing.id,
        step=step,
        tool="check_duplicate",
        outputs={"content_hash": content_hash, "duplicate_of_invoice_id": existing.id},
    )
    return True
