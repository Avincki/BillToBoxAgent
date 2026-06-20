from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from billtobox_agent.data import (
    AgentEventLevel,
    AgentEventType,
    Invoice,
    InvoiceStatus,
    UnitOfWork,
    create_engine,
    create_session_factory,
    init_schema,
)
from billtobox_agent.drive import (
    DriveConnector,
    InvoiceFileFields,
    build_filename,
    store_pdf_to_drive,
)

_PDF = b"%PDF-1.7 a stored invoice"

# ----- a minimal fake of the googleapiclient Drive v3 resource ----------------
# Tracks uploaded files so find-before-upload sees collisions, and captures the
# media bytes (via the real MediaInMemoryUpload the connector builds) so the
# round-trip can be asserted.

_NAME_RE = re.compile(r"name = '([^']*)'")
_PARENT_RE = re.compile(r"'([^']*)' in parents")


def _name_and_parent(query: str) -> tuple[str, str]:
    name = _NAME_RE.search(query)
    parent = _PARENT_RE.search(query)
    assert name is not None and parent is not None, query
    return name.group(1), parent.group(1)


class _Resp:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def execute(self) -> dict[str, Any]:
        return self._result


class _Files:
    def __init__(self, store: FakeDriveService) -> None:
        self._store = store

    def list(
        self,
        *,
        q: str,
        spaces: str = "drive",
        fields: str | None = None,
        pageSize: int | None = None,
    ) -> _Resp:
        self._store.list_calls.append(q)
        name, parent = _name_and_parent(q)
        matches = [
            {"id": f["id"], "name": f["name"]}
            for f in self._store.uploaded
            if f["name"] == name and f["parent"] == parent
        ]
        return _Resp({"files": matches})

    def create(
        self,
        *,
        body: dict[str, Any],
        media_body: Any = None,
        fields: str | None = None,
    ) -> _Resp:
        if self._store.fail_create:
            raise RuntimeError("drive down")
        name = body["name"]
        parent = body["parents"][0]
        content = media_body.getbytes(0, media_body.size()) if media_body is not None else None
        self._store.counter += 1
        file_id = f"file-{self._store.counter}"
        self._store.uploaded.append(
            {"id": file_id, "name": name, "parent": parent, "content": content}
        )
        self._store.create_calls.append((name, parent))
        return _Resp({"id": file_id})


class FakeDriveService:
    def __init__(self, *, fail_create: bool = False) -> None:
        self.uploaded: list[dict[str, Any]] = []
        self.list_calls: list[str] = []
        self.create_calls: list[tuple[str, str]] = []
        self.counter = 0
        self.fail_create = fail_create

    def files(self) -> _Files:
        return _Files(self)


# ----- filename builder (pure) ------------------------------------------------


def test_build_filename_formats_source_date_amount() -> None:
    assert build_filename("gmail", date(2026, 1, 15), 100.0) == "gmail_2026-01-15_100.00.pdf"


def test_build_filename_amount_is_two_decimals() -> None:
    assert build_filename("outlook", date(2026, 12, 31), 1234.5) == "outlook_2026-12-31_1234.50.pdf"


def test_build_filename_missing_amount_is_unknown() -> None:
    assert build_filename("gmail", date(2026, 1, 1), None) == "gmail_2026-01-01_unknown.pdf"


def test_build_filename_strips_illegal_and_collapses_whitespace() -> None:
    got = build_filename("we/ird  name", date(2026, 1, 1), 10.0)
    assert got == "weird name_2026-01-01_10.00.pdf"


# ----- connector collision handling -------------------------------------------


def test_store_pdf_suffixes_on_collision() -> None:
    connector = DriveConnector(FakeDriveService())

    id1, name1 = connector.store_pdf("inv.pdf", b"a", "F1")
    id2, name2 = connector.store_pdf("inv.pdf", b"b", "F1")
    id3, name3 = connector.store_pdf("inv.pdf", b"c", "F1")

    assert [name1, name2, name3] == ["inv.pdf", "inv_2.pdf", "inv_3.pdf"]
    assert len({id1, id2, id3}) == 3  # three distinct files created


def test_store_pdf_same_name_different_folder_does_not_collide() -> None:
    connector = DriveConnector(FakeDriveService())

    _id1, name1 = connector.store_pdf("inv.pdf", b"a", "F1")
    _id2, name2 = connector.store_pdf("inv.pdf", b"b", "F2")

    assert name1 == name2 == "inv.pdf"  # different parents, no suffix


# ----- async pipeline tool ----------------------------------------------------


async def _factory(tmp_path: Path) -> Any:
    engine = create_engine(tmp_path / "drive.db")
    await init_schema(engine)
    return engine, create_session_factory(engine)


async def _make_invoice(factory: Any, content_hash: str) -> int:
    async with UnitOfWork(factory) as uow:
        invoice = await uow.invoices.add(
            Invoice(source="gmail", source_message_id="m1", content_hash=content_hash)
        )
        await uow.commit()
        return invoice.id


async def test_store_uploads_records_row_and_audits(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    invoice_id = await _make_invoice(factory, "h1")
    service = FakeDriveService()
    connector = DriveConnector(service)
    fields = InvoiceFileFields(source="gmail", invoice_date=date(2026, 1, 15), amount=100.0)

    async with UnitOfWork(factory) as uow:
        file_id, drive_path = await store_pdf_to_drive(
            connector,
            _PDF,
            fields,
            folder_id="Q1ID",
            folder_path="Invoices/2026/Q1",
            uow=uow,
            invoice_id=invoice_id,
        )
        await uow.commit()
        invoice = await uow.invoices.get(invoice_id)
        assert invoice is not None
        inv_file_id = invoice.drive_file_id
        inv_path = invoice.drive_path
        inv_status = invoice.status
        events = await uow.agent_events.list()
        result_outputs = next(
            e.outputs_json for e in events if e.event_type == AgentEventType.TOOL_RESULT
        )
        call_inputs = next(
            e.inputs_json for e in events if e.event_type == AgentEventType.TOOL_CALL
        )

    await engine.dispose()

    assert file_id == "file-1"
    assert drive_path == "Invoices/2026/Q1/gmail_2026-01-15_100.00.pdf"
    assert service.create_calls == [("gmail_2026-01-15_100.00.pdf", "Q1ID")]
    assert service.uploaded[0]["content"] == _PDF  # media bytes round-tripped intact

    # The invoice row carries the Drive location and is now 'stored'.
    assert inv_file_id == "file-1"
    assert inv_path == drive_path
    assert inv_status == InvoiceStatus.STORED

    # The audit trail records the result and never the raw PDF bytes.
    assert result_outputs == {"drive_file_id": "file-1", "drive_path": drive_path}
    assert call_inputs is not None
    assert call_inputs["pdf_bytes"]["__bytes__"]["len"] == len(_PDF)
    assert "sha256" in call_inputs["pdf_bytes"]["__bytes__"]


async def test_store_suffixes_filename_on_existing(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    invoice_id = await _make_invoice(factory, "h1")
    service = FakeDriveService()
    # A file with the target name already lives in the folder.
    service.uploaded.append(
        {"id": "existing", "name": "gmail_2026-01-15_100.00.pdf", "parent": "Q1ID", "content": b""}
    )
    connector = DriveConnector(service)
    fields = InvoiceFileFields(source="gmail", invoice_date=date(2026, 1, 15), amount=100.0)

    async with UnitOfWork(factory) as uow:
        _file_id, drive_path = await store_pdf_to_drive(
            connector,
            _PDF,
            fields,
            folder_id="Q1ID",
            folder_path="Invoices/2026/Q1",
            uow=uow,
            invoice_id=invoice_id,
        )
        await uow.commit()

    await engine.dispose()

    assert drive_path == "Invoices/2026/Q1/gmail_2026-01-15_100.00_2.pdf"


async def test_store_records_error_and_leaves_row_untouched(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    invoice_id = await _make_invoice(factory, "h1")
    connector = DriveConnector(FakeDriveService(fail_create=True))
    fields = InvoiceFileFields(source="gmail", invoice_date=date(2026, 1, 1), amount=10.0)

    async with UnitOfWork(factory) as uow:
        with pytest.raises(RuntimeError):
            await store_pdf_to_drive(
                connector,
                _PDF,
                fields,
                folder_id="Q1ID",
                folder_path="Invoices/2026/Q1",
                uow=uow,
                invoice_id=invoice_id,
            )
        await uow.commit()
        invoice = await uow.invoices.get(invoice_id)
        assert invoice is not None
        inv_file_id = invoice.drive_file_id
        inv_status = invoice.status
        events = await uow.agent_events.list()
        error_levels = [e.level for e in events if e.tool == "store_pdf_to_drive"]

    await engine.dispose()

    assert inv_file_id is None  # upload failed → no Drive location recorded
    assert inv_status == InvoiceStatus.NEW  # status unchanged
    assert AgentEventLevel.ERROR in error_levels
