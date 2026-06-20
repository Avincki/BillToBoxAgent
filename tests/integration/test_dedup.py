from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from billtobox_agent.data import (
    AgentEventType,
    Invoice,
    UnitOfWork,
    create_engine,
    create_session_factory,
    init_schema,
)
from billtobox_agent.pipeline import check_duplicate, compute_content_hash
from billtobox_agent.utils import sha256_hex

_PDF = b"%PDF-1.7 a real-ish invoice"
_OTHER = b"%PDF-1.7 a different invoice"


async def _factory(tmp_path: Path) -> tuple[AsyncEngine, async_sessionmaker[Any]]:
    engine = create_engine(tmp_path / "dedup.db")
    await init_schema(engine)
    return engine, create_session_factory(engine)


def test_compute_content_hash_matches_sha256_and_is_content_addressed() -> None:
    assert compute_content_hash(_PDF) == sha256_hex(_PDF)
    assert compute_content_hash(_PDF) == compute_content_hash(_PDF)  # stable
    assert compute_content_hash(_PDF) != compute_content_hash(_OTHER)


async def test_new_hash_is_not_a_duplicate(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)

    async with UnitOfWork(factory) as uow:
        result = await check_duplicate(uow, compute_content_hash(_PDF))
        n_events = len(await uow.agent_events.list())

    await engine.dispose()

    assert result is False
    assert n_events == 0  # nothing to audit when it is not a duplicate


async def test_seen_hash_is_duplicate_without_reinserting(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    content_hash = compute_content_hash(_PDF)

    # First sighting: not a duplicate, so the caller inserts the invoice.
    async with UnitOfWork(factory) as uow:
        first = await check_duplicate(uow, content_hash)
        invoice = await uow.invoices.add(
            Invoice(source="gmail", source_message_id="m1", content_hash=content_hash)
        )
        await uow.commit()
        invoice_id = invoice.id

    # Same hash again: a duplicate — True, audited, and nothing new inserted.
    # A different hash is still not a duplicate.
    async with UnitOfWork(factory) as uow:
        second = await check_duplicate(uow, content_hash, run_id=None, step=2)
        other = await check_duplicate(uow, compute_content_hash(_OTHER))
        await uow.commit()

    async with UnitOfWork(factory) as uow:
        n_invoices = len(await uow.invoices.list())
        dup_events = [e for e in await uow.agent_events.list() if e.tool == "check_duplicate"]
        event_invoice_id = dup_events[0].invoice_id if dup_events else None
        event_type = dup_events[0].event_type if dup_events else None
        event_outputs = dup_events[0].outputs_json if dup_events else None

    await engine.dispose()

    assert first is False
    assert second is True
    assert other is False
    assert n_invoices == 1  # the duplicate was never inserted
    assert len(dup_events) == 1  # exactly one skip recorded
    assert event_invoice_id == invoice_id  # event links back to the original invoice
    assert event_type == AgentEventType.DECISION
    assert event_outputs is not None
    assert event_outputs["content_hash"] == content_hash
    assert event_outputs["duplicate_of_invoice_id"] == invoice_id
