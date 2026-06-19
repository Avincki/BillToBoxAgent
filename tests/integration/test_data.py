from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from billtobox_agent.data import (
    AgentEventType,
    Invoice,
    UnitOfWork,
    create_engine,
    create_session_factory,
    init_schema,
)

_INVOICE_COLUMNS = {
    "id",
    "source",
    "source_message_id",
    "content_hash",
    "vendor",
    "invoice_date",
    "amount",
    "currency",
    "fy_label",
    "quarter",
    "confidence",
    "status",
    "drive_file_id",
    "drive_path",
    "run_id",
    "created_at",
    "updated_at",
}


async def _engine(tmp_path: Path) -> AsyncEngine:
    engine = create_engine(tmp_path / "t.db")
    await init_schema(engine)
    return engine


async def test_schema_tables_and_columns(tmp_path: Path) -> None:
    engine = await _engine(tmp_path)

    def _introspect(sync_conn: object) -> dict[str, set[str]]:
        insp = inspect(sync_conn)
        return {t: {c["name"] for c in insp.get_columns(t)} for t in insp.get_table_names()}

    async with engine.connect() as conn:
        schema = await conn.run_sync(_introspect)
    await engine.dispose()

    assert {"invoices", "runs", "source_status", "agent_events"} <= set(schema)
    assert schema["invoices"] >= _INVOICE_COLUMNS
    assert {"started_at", "ended_at", "items_fetched", "error_summary"} <= schema["runs"]
    assert {"source", "watermark", "last_error_message"} <= schema["source_status"]
    assert {"run_id", "invoice_id", "event_type", "inputs_json", "outputs_json"} <= schema[
        "agent_events"
    ]


async def test_wal_and_foreign_keys_enabled(tmp_path: Path) -> None:
    engine = create_engine(tmp_path / "wal.db")
    async with engine.connect() as conn:
        journal = (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar()
        foreign_keys = (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar()
    await engine.dispose()
    assert journal == "wal"
    assert foreign_keys == 1


async def test_content_hash_unique_constraint(tmp_path: Path) -> None:
    engine = await _engine(tmp_path)
    factory = create_session_factory(engine)

    async with factory() as session:
        session.add(Invoice(source="gmail", source_message_id="m1", content_hash="dup"))
        await session.commit()

    async with factory() as session:
        session.add(Invoice(source="outlook", source_message_id="m2", content_hash="dup"))
        with pytest.raises(IntegrityError):
            await session.commit()

    await engine.dispose()


async def test_unit_of_work_and_agent_event_redaction(tmp_path: Path) -> None:
    engine = await _engine(tmp_path)
    factory = create_session_factory(engine)

    async with UnitOfWork(factory) as uow:
        run = await uow.runs.start()
        invoice = await uow.invoices.add(
            Invoice(source="gmail", source_message_id="m1", content_hash="h1", run_id=run.id)
        )
        await uow.agent_events.add(
            event_type=AgentEventType.TOOL_CALL,
            summary="extract_invoice",
            run_id=run.id,
            invoice_id=invoice.id,
            tool="extract_invoice",
            inputs={"api_key": "sk-secret", "pdf_bytes": b"%PDF-1.7 ..."},
        )
        await uow.commit()
        run_id, invoice_id = run.id, invoice.id

    # Read back in a fresh unit of work; capture plain values while the session
    # is open (ORM instances detach once the unit of work closes).
    async with UnitOfWork(factory) as uow:
        found = await uow.invoices.get_by_content_hash("h1")
        found_id = found.id if found is not None else None
        exists = await uow.invoices.exists_content_hash("h1")
        events = await uow.agent_events.list(run_id=run_id)
        events_count = len(events)
        stored_inputs = events[0].inputs_json if events else None
        watermark = await uow.source_status.get_watermark("gmail")

    await engine.dispose()

    assert found_id == invoice_id
    assert exists is True
    assert watermark is None
    assert events_count == 1
    assert stored_inputs is not None
    assert stored_inputs["api_key"] == "***"
    assert stored_inputs["pdf_bytes"]["__bytes__"]["len"] > 0
    assert "sk-secret" not in str(stored_inputs)
