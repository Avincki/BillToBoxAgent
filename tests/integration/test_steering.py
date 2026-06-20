from __future__ import annotations

import base64
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from billtobox_agent.config.models import (
    AnthropicConfig,
    AppConfig,
    BilltoboxConfig,
    GoogleConfig,
    LoggingConfig,
    MicrosoftConfig,
    SmtpConfig,
    Source,
    SourcesConfig,
    StorageConfig,
)
from billtobox_agent.data import Invoice, InvoiceStatus, UnitOfWork
from billtobox_agent.drive import DriveConnector
from billtobox_agent.mail.base import FetchedPdf, MailMessageRef
from billtobox_agent.web import create_app

_APPROVED = b"%PDF-1.7 approved invoice"
# The model returns refreshed fields on a re-extract, so the change is observable.
_RESPONSES: dict[bytes, str] = {
    _APPROVED: json.dumps(
        {
            "is_invoice": True,
            "confidence": 0.99,
            "vendor": "KPN Updated",
            "invoice_date": "2026-05-31",
            "amount": 200.00,
            "currency": "EUR",
        }
    )
}


# ----- fakes ------------------------------------------------------------------


class FakeMailConnector:
    source = "gmail"

    def search(self, since: datetime | None = None) -> list[MailMessageRef]:
        ts = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        if since is not None and ts <= since:
            return []
        return [MailMessageRef("gmail", "mr1", "Invoice run", "billing@kpn.com", ts)]

    def download_pdfs(self, ref: MailMessageRef) -> list[FetchedPdf]:
        return [FetchedPdf(message=ref, filename="run.pdf", pdf_bytes=_APPROVED)]


def _response(text: str) -> Any:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


class _FakeMessages:
    def __init__(self, responses: dict[bytes, str]) -> None:
        self._responses = responses

    def create(self, **kwargs: Any) -> Any:
        b64 = kwargs["messages"][0]["content"][0]["source"]["data"]
        return _response(self._responses[base64.standard_b64decode(b64)])


class FakeAnthropic:
    def __init__(self, responses: dict[bytes, str]) -> None:
        self.messages = _FakeMessages(responses)


_NAME_RE = re.compile(r"name = '([^']*)'")
_PARENT_RE = re.compile(r"'([^']*)' in parents")


def _name_and_parent(query: str) -> tuple[str, str]:
    name = _NAME_RE.search(query)
    parent = _PARENT_RE.search(query)
    assert name is not None and parent is not None, query
    return name.group(1), parent.group(1)


class _Resp:
    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self) -> Any:
        return self._result


class _Files:
    def __init__(self, store: FakeDriveService) -> None:
        self._store = store

    def list(
        self, *, q: str, spaces: str = "drive", fields: str | None = None, pageSize: int = 10
    ) -> _Resp:
        name, parent = _name_and_parent(q)
        pool = self._store.stored if "mimeType !=" in q else self._store.dirs
        matches = [
            {"id": f["id"], "name": f["name"]}
            for f in pool
            if f["name"] == name and f["parent"] == parent
        ]
        return _Resp({"files": matches})

    def create(
        self, *, body: dict[str, Any], media_body: Any = None, fields: str | None = None
    ) -> _Resp:
        name = body["name"]
        parent = body["parents"][0]
        self._store.counter += 1
        if media_body is not None:
            fid = f"file-{self._store.counter}"
            content = media_body.getbytes(0, media_body.size())
            self._store.stored.append(
                {"id": fid, "name": name, "parent": parent, "content": content}
            )
        else:
            fid = f"fld-{self._store.counter}"
            self._store.dirs.append({"id": fid, "name": name, "parent": parent})
        return _Resp({"id": fid})

    def get(self, *, fileId: str, fields: str | None = None) -> _Resp:
        rec = self._store.by_id(fileId)
        return _Resp({"id": fileId, "parents": [rec["parent"]] if rec else []})

    def update(
        self,
        *,
        fileId: str,
        addParents: str | None = None,
        removeParents: str | None = None,
        fields: str | None = None,
    ) -> _Resp:
        rec = self._store.by_id(fileId)
        if rec is not None and addParents is not None:
            rec["parent"] = addParents
        self._store.move_calls.append((fileId, addParents, removeParents))
        return _Resp({"id": fileId, "parents": [addParents]})

    def get_media(self, *, fileId: str) -> _Resp:
        rec = self._store.by_id(fileId)
        return _Resp(rec["content"] if rec else b"")


class FakeDriveService:
    def __init__(self) -> None:
        self.dirs: list[dict[str, Any]] = []
        self.stored: list[dict[str, Any]] = []
        self.move_calls: list[tuple[str, str | None, str | None]] = []
        self.counter = 0

    def files(self) -> _Files:
        return _Files(self)

    def by_id(self, file_id: str) -> dict[str, Any] | None:
        return next((f for f in self.stored if f["id"] == file_id), None)


# ----- context ----------------------------------------------------------------


def _make_config(tmp_path: Any) -> AppConfig:
    return AppConfig(
        anthropic=AnthropicConfig(api_key="sk-ant-x"),
        google=GoogleConfig(client_id="g", client_secret="gs"),
        microsoft=MicrosoftConfig(client_id="m"),
        billtobox=BilltoboxConfig(
            mailbox_address="box@billtobox.example", sender_address="me@example.com"
        ),
        smtp=SmtpConfig(host="smtp.example", username="u", password="p"),
        storage=StorageConfig(sqlite_path=tmp_path / "steer.db"),
        logging=LoggingConfig(log_dir=tmp_path / "logs"),
        sources=SourcesConfig(polling=(Source.GMAIL,)),
    )


async def _seed(app: object, drive: FakeDriveService) -> int:
    drive.stored.append(
        {
            "id": "file-seed",
            "name": "gmail_2026-05-31_149.95.pdf",
            "parent": "old-q2-folder",
            "content": _APPROVED,
        }
    )
    factory = app.state.session_factory  # type: ignore[attr-defined]
    async with UnitOfWork(factory) as uow:
        invoice = await uow.invoices.add(
            Invoice(
                source="gmail",
                source_message_id="seed",
                content_hash="seedhash",
                vendor="KPN",
                invoice_date=date(2026, 5, 31),
                amount=149.95,
                currency="EUR",
                confidence=0.96,
                fy_label="2026",
                quarter="Q2",
                status=InvoiceStatus.STORED.value,
                drive_file_id="file-seed",
                drive_path="Invoices/2026/Q2/gmail_2026-05-31_149.95.pdf",
            )
        )
        await uow.commit()
        return invoice.id


@pytest_asyncio.fixture
async def steering(
    tmp_path: Any,
) -> AsyncIterator[tuple[AsyncClient, int, FakeDriveService]]:
    drive_service = FakeDriveService()
    app = create_app(
        _make_config(tmp_path),
        drive=DriveConnector(drive_service),
        anthropic_client=FakeAnthropic(_RESPONSES),  # type: ignore[arg-type]
        mail_connectors={"gmail": FakeMailConnector()},  # type: ignore[dict-item]
    )
    async with app.router.lifespan_context(app):
        invoice_id = await _seed(app, drive_service)
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test", headers={"Origin": "http://test"}
        ) as client:
            yield client, invoice_id, drive_service


async def _read_invoice(factory: Any, invoice_id: int) -> dict[str, Any]:
    async with UnitOfWork(factory) as uow:
        inv = await uow.invoices.get(invoice_id)
        assert inv is not None
        tools = [e.tool for e in await uow.agent_events.list(invoice_id=invoice_id, limit=100)]
        return {
            "status": inv.status,
            "vendor": inv.vendor,
            "amount": inv.amount,
            "confidence": inv.confidence,
            "fy_label": inv.fy_label,
            "quarter": inv.quarter,
            "drive_path": inv.drive_path,
            "tools": tools,
        }


def _factory(client: AsyncClient) -> Any:
    return client._transport.app.state.session_factory  # type: ignore[attr-defined,union-attr]


async def test_approve_sets_status_and_audits(
    steering: tuple[AsyncClient, int, FakeDriveService],
) -> None:
    client, invoice_id, _drive = steering
    resp = await client.post(f"/invoices/{invoice_id}/approve")
    assert resp.status_code == 303
    row = await _read_invoice(_factory(client), invoice_id)
    assert row["status"] == InvoiceStatus.APPROVED
    assert "approve_invoice" in row["tools"]


async def test_reject_sets_status_and_audits(
    steering: tuple[AsyncClient, int, FakeDriveService],
) -> None:
    client, invoice_id, _drive = steering
    resp = await client.post(f"/invoices/{invoice_id}/reject", data={"reason": "not mine"})
    assert resp.status_code == 303
    row = await _read_invoice(_factory(client), invoice_id)
    assert row["status"] == InvoiceStatus.REJECTED
    assert "reject_invoice" in row["tools"]


async def test_queue_send_sets_status(
    steering: tuple[AsyncClient, int, FakeDriveService],
) -> None:
    client, invoice_id, _drive = steering
    resp = await client.post(f"/invoices/{invoice_id}/queue-send")
    assert resp.status_code == 303
    row = await _read_invoice(_factory(client), invoice_id)
    assert row["status"] == InvoiceStatus.UPLOAD_APPROVED
    assert "queue_billtobox_upload" in row["tools"]


async def test_edit_without_quarter_change_does_not_move(
    steering: tuple[AsyncClient, int, FakeDriveService],
) -> None:
    client, invoice_id, drive = steering
    resp = await client.post(
        f"/invoices/{invoice_id}/edit",
        data={
            "vendor": "KPN BV",
            "invoice_date": "2026-05-31",
            "amount": "149.95",
            "currency": "EUR",
        },
    )
    assert resp.status_code == 303
    row = await _read_invoice(_factory(client), invoice_id)
    assert row["vendor"] == "KPN BV"
    assert (row["fy_label"], row["quarter"]) == ("2026", "Q2")  # unchanged
    assert drive.move_calls == []  # same quarter → no Drive move
    assert "edit_invoice" in row["tools"]


async def test_edit_changing_quarter_moves_drive_file(
    steering: tuple[AsyncClient, int, FakeDriveService],
) -> None:
    client, invoice_id, drive = steering
    resp = await client.post(
        f"/invoices/{invoice_id}/edit",
        data={"vendor": "KPN", "invoice_date": "2026-01-15", "amount": "149.95", "currency": "EUR"},
    )
    assert resp.status_code == 303
    row = await _read_invoice(_factory(client), invoice_id)
    assert (row["fy_label"], row["quarter"]) == ("2026", "Q1")  # moved quarter
    assert row["drive_path"] == "Invoices/2026/Q1/gmail_2026-05-31_149.95.pdf"
    # Exactly one move, and the stored PDF now lives under the new quarter folder.
    assert len(drive.move_calls) == 1
    moved_file, new_parent, _old = drive.move_calls[0]
    assert moved_file == "file-seed"
    assert drive.by_id("file-seed")["parent"] == new_parent


async def test_reextract_refreshes_fields(
    steering: tuple[AsyncClient, int, FakeDriveService],
) -> None:
    client, invoice_id, _drive = steering
    resp = await client.post(f"/invoices/{invoice_id}/reextract")
    assert resp.status_code == 303
    row = await _read_invoice(_factory(client), invoice_id)
    assert row["vendor"] == "KPN Updated"  # the model's refreshed value
    assert row["amount"] == 200.00
    assert row["confidence"] == 0.99
    assert "extract_invoice" in row["tools"]


async def test_manual_run_executes(
    steering: tuple[AsyncClient, int, FakeDriveService],
) -> None:
    client, _invoice_id, _drive = steering
    resp = await client.post("/run")
    assert resp.status_code == 303
    factory = _factory(client)
    async with UnitOfWork(factory) as uow:
        runs = await uow.runs.list()
        invoice_count = len(await uow.invoices.list())
    assert len(runs) == 1  # the manual run recorded a run row
    assert invoice_count == 2  # the seeded invoice + the one fetched by the run


async def test_steering_post_requires_same_origin(
    steering: tuple[AsyncClient, int, FakeDriveService],
) -> None:
    client, invoice_id, _drive = steering
    resp = await client.post(
        f"/invoices/{invoice_id}/approve", headers={"Origin": "http://evil.example"}
    )
    assert resp.status_code == 403
