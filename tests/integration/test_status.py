from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from billtobox_agent.data import (
    AgentEventType,
    Invoice,
    InvoiceStatus,
    UnitOfWork,
    create_engine,
    create_session_factory,
    init_schema,
)
from billtobox_agent.pipeline import flag_for_review, queue_billtobox_upload


async def _factory(tmp_path: Path) -> Any:
    engine = create_engine(tmp_path / "status.db")
    await init_schema(engine)
    return engine, create_session_factory(engine)


async def _make_invoice(factory: Any) -> int:
    async with UnitOfWork(factory) as uow:
        invoice = await uow.invoices.add(
            Invoice(source="gmail", source_message_id="m1", content_hash="h1")
        )
        await uow.commit()
        return invoice.id


# ----- flag_for_review --------------------------------------------------------


async def test_flag_for_review_sets_status_and_records_reason(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    invoice_id = await _make_invoice(factory)

    # No external client is passed in — by construction this touches only SQLite.
    async with UnitOfWork(factory) as uow:
        await flag_for_review(uow, invoice_id, "confidence 0.40 below threshold", step=3)
        await uow.commit()
        invoice = await uow.invoices.get(invoice_id)
        assert invoice is not None
        status = invoice.status
        events = [e for e in await uow.agent_events.list() if e.tool == "flag_for_review"]
        event_type = events[0].event_type
        event_outputs = events[0].outputs_json
        event_invoice_id = events[0].invoice_id

    await engine.dispose()

    assert status == InvoiceStatus.REVIEWED
    assert len(events) == 1
    assert event_type == AgentEventType.DECISION
    assert event_invoice_id == invoice_id
    assert event_outputs == {
        "status": "reviewed",
        "reason": "confidence 0.40 below threshold",
    }


# ----- queue_billtobox_upload -------------------------------------------------


async def test_queue_billtobox_upload_sets_status_and_audits(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    invoice_id = await _make_invoice(factory)

    async with UnitOfWork(factory) as uow:
        await queue_billtobox_upload(uow, invoice_id)
        await uow.commit()
        invoice = await uow.invoices.get(invoice_id)
        assert invoice is not None
        status = invoice.status
        events = [e for e in await uow.agent_events.list() if e.tool == "queue_billtobox_upload"]
        event_type = events[0].event_type
        event_outputs = events[0].outputs_json

    await engine.dispose()

    # Status flips to upload_approved; nothing is sent (no SMTP/Drive client involved).
    assert status == InvoiceStatus.UPLOAD_APPROVED
    assert len(events) == 1
    assert event_type == AgentEventType.DECISION
    assert event_outputs == {"status": "upload_approved"}


async def test_set_status_on_missing_invoice_raises(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)

    async with UnitOfWork(factory) as uow:
        with pytest.raises(ValueError, match="not found"):
            await flag_for_review(uow, 999, "no such invoice")

    await engine.dispose()
