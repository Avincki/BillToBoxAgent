from __future__ import annotations

import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest

from billtobox_agent.billtobox import (
    BilltoboxSendError,
    SmtpTransport,
    email_to_billtobox,
)
from billtobox_agent.config.models import BilltoboxConfig, SmtpConfig
from billtobox_agent.data import (
    Invoice,
    InvoiceStatus,
    UnitOfWork,
    create_engine,
    create_session_factory,
    init_schema,
)
from billtobox_agent.drive import DriveConnector

_PDF = b"%PDF-1.7 the stored invoice"


# ----- fakes ------------------------------------------------------------------


class _Resp:
    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self) -> Any:
        return self._result


class _FakeFiles:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def get_media(self, *, fileId: str) -> _Resp:
        return _Resp(self._content)


class FakeDriveService:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def files(self) -> _FakeFiles:
        return _FakeFiles(self._content)


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


def _billtobox() -> BilltoboxConfig:
    return BilltoboxConfig(mailbox_address="box@billtobox.example", sender_address="me@example.com")


async def _factory(tmp_path: Path) -> Any:
    engine = create_engine(tmp_path / "btb.db")
    await init_schema(engine)
    return engine, create_session_factory(engine)


async def _seed(
    factory: Any,
    *,
    status: str,
    drive_file_id: str | None = "file-seed",
    uploaded_at: datetime | None = None,
) -> int:
    async with UnitOfWork(factory) as uow:
        invoice = await uow.invoices.add(
            Invoice(
                source="gmail",
                source_message_id="m1",
                content_hash="h1",
                status=status,
                drive_file_id=drive_file_id,
                drive_path="Invoices/2026/Q2/gmail_2026-05-31_149.95.pdf",
                uploaded_at=uploaded_at,
            )
        )
        await uow.commit()
        return invoice.id


# ----- tests ------------------------------------------------------------------


async def test_send_attaches_pdf_and_marks_uploaded(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    invoice_id = await _seed(factory, status=InvoiceStatus.UPLOAD_APPROVED.value)
    drive = DriveConnector(FakeDriveService(_PDF))  # type: ignore[arg-type]
    transport = FakeTransport()

    async with UnitOfWork(factory) as uow:
        filename = await email_to_billtobox(
            uow, drive, invoice_id, billtobox=_billtobox(), transport=transport
        )
        await uow.commit()
        invoice = await uow.invoices.get(invoice_id)
        assert invoice is not None
        status = invoice.status
        uploaded_at = invoice.uploaded_at
        tools = [e.tool for e in await uow.agent_events.list(invoice_id=invoice_id)]

    await engine.dispose()

    assert filename == "gmail_2026-05-31_149.95.pdf"
    assert len(transport.sent) == 1
    message = transport.sent[0]
    assert message["Subject"] == filename
    assert message["From"] == "me@example.com"
    assert message["To"] == "box@billtobox.example"
    attachments = list(message.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_content() == _PDF

    assert status == InvoiceStatus.UPLOADED
    assert uploaded_at is not None
    assert "email_to_billtobox" in tools


async def test_second_send_raises_and_sends_nothing(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    invoice_id = await _seed(factory, status=InvoiceStatus.UPLOAD_APPROVED.value)
    drive = DriveConnector(FakeDriveService(_PDF))  # type: ignore[arg-type]
    transport = FakeTransport()
    billtobox = _billtobox()

    async with UnitOfWork(factory) as uow:
        await email_to_billtobox(uow, drive, invoice_id, billtobox=billtobox, transport=transport)
        await uow.commit()

    # Status is now 'uploaded' — a second call is refused and sends nothing.
    async with UnitOfWork(factory) as uow:
        with pytest.raises(BilltoboxSendError):
            await email_to_billtobox(
                uow, drive, invoice_id, billtobox=billtobox, transport=transport
            )

    await engine.dispose()
    assert len(transport.sent) == 1  # only the first send


async def test_send_refused_when_disabled(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    invoice_id = await _seed(factory, status=InvoiceStatus.UPLOAD_APPROVED.value)
    drive = DriveConnector(FakeDriveService(_PDF))  # type: ignore[arg-type]
    transport = FakeTransport()

    async with UnitOfWork(factory) as uow:
        with pytest.raises(BilltoboxSendError, match="disabled"):
            await email_to_billtobox(
                uow,
                drive,
                invoice_id,
                billtobox=_billtobox(),
                transport=transport,
                send_enabled=False,
            )
        invoice = await uow.invoices.get(invoice_id)
        assert invoice is not None
        status = invoice.status  # still approved, untouched — a test run sends nothing

    await engine.dispose()
    assert transport.sent == []  # the live service was never contacted
    assert status == InvoiceStatus.UPLOAD_APPROVED


async def test_send_refused_when_not_upload_approved(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    invoice_id = await _seed(factory, status=InvoiceStatus.STORED.value)
    drive = DriveConnector(FakeDriveService(_PDF))  # type: ignore[arg-type]
    transport = FakeTransport()

    async with UnitOfWork(factory) as uow:
        with pytest.raises(BilltoboxSendError, match="not upload_approved"):
            await email_to_billtobox(
                uow, drive, invoice_id, billtobox=_billtobox(), transport=transport
            )

    await engine.dispose()
    assert transport.sent == []


async def test_send_refused_when_already_uploaded(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    invoice_id = await _seed(
        factory,
        status=InvoiceStatus.UPLOAD_APPROVED.value,
        uploaded_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    drive = DriveConnector(FakeDriveService(_PDF))  # type: ignore[arg-type]
    transport = FakeTransport()

    async with UnitOfWork(factory) as uow:
        with pytest.raises(BilltoboxSendError, match="already uploaded"):
            await email_to_billtobox(
                uow, drive, invoice_id, billtobox=_billtobox(), transport=transport
            )

    await engine.dispose()
    assert transport.sent == []


async def test_send_refused_without_stored_pdf(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    invoice_id = await _seed(
        factory, status=InvoiceStatus.UPLOAD_APPROVED.value, drive_file_id=None
    )
    drive = DriveConnector(FakeDriveService(_PDF))  # type: ignore[arg-type]
    transport = FakeTransport()

    async with UnitOfWork(factory) as uow:
        with pytest.raises(BilltoboxSendError, match="no stored PDF"):
            await email_to_billtobox(
                uow, drive, invoice_id, billtobox=_billtobox(), transport=transport
            )

    await engine.dispose()
    assert transport.sent == []


def test_smtp_transport_uses_starttls_and_login(monkeypatch: Any) -> None:
    calls: dict[str, Any] = {}

    class _FakeSMTP:
        def __init__(self, host: str, port: int) -> None:
            calls["host"], calls["port"] = host, port

        def __enter__(self) -> _FakeSMTP:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def starttls(self) -> None:
            calls["starttls"] = True

        def login(self, username: str, password: str) -> None:
            calls["login"] = (username, password)

        def send_message(self, message: EmailMessage) -> None:
            calls["sent"] = message

    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    config = SmtpConfig(host="smtp.example", port=587, username="user", password="app-pw")
    message = EmailMessage()
    message["Subject"] = "test"

    SmtpTransport(config).send(message)

    assert (calls["host"], calls["port"]) == ("smtp.example", 587)
    assert calls["starttls"] is True
    assert calls["login"] == ("user", "app-pw")
    assert calls["sent"] is message
