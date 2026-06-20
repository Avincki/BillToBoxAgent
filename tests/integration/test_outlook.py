from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from billtobox_agent.data import (
    Invoice,
    UnitOfWork,
    create_engine,
    create_session_factory,
    init_schema,
)
from billtobox_agent.mail import FetchedPdf, OutlookConnector, fetch_new_pdfs
from billtobox_agent.mail.prefilter import PDF_MAGIC

_PDF1 = b"%PDF-1.7 outlook one"
_PDF2 = b"%PDF-1.7 outlook two"


def _messages() -> list[dict[str, Any]]:
    return [
        {
            "id": "o1",
            "subject": "Invoice 1",
            "sender": "billing@kpn.com",
            "receivedDateTime": "2026-05-01T10:00:00Z",
            "attachments": [_pdf_attachment("invoice1.pdf", _PDF1)],
        },
        {
            "id": "o2",
            "subject": "Factuur 2",
            "sender": "facturen@proximus.be",
            "receivedDateTime": "2026-05-02T10:00:00Z",
            "attachments": [
                {"@odata.type": "#microsoft.graph.itemAttachment", "name": "calendar.ics"},
                _pdf_attachment("factuur2.pdf", _PDF2),
            ],
        },
    ]


def _pdf_attachment(name: str, data: bytes) -> dict[str, Any]:
    return {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": name,
        "contentType": "application/pdf",
        "contentBytes": base64.b64encode(data).decode(),
    }


class FakeGraphClient:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = messages
        self._by_id = {m["id"]: m for m in messages}
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((path, params))
        if path == "/me/messages":
            return {
                "value": [
                    {
                        "id": m["id"],
                        "subject": m["subject"],
                        "from": {"emailAddress": {"address": m["sender"]}},
                        "receivedDateTime": m["receivedDateTime"],
                    }
                    for m in self._messages
                ]
            }
        message_id = path.split("/")[3]  # /me/messages/{id}[/attachments]
        if path.endswith("/attachments"):
            return {"value": self._by_id[message_id]["attachments"]}
        return {"body": self._by_id[message_id].get("body", {})}  # /me/messages/{id}?$select=body


# ----- connector tests --------------------------------------------------------


def test_search_filters_attachments_and_parses_refs() -> None:
    client = FakeGraphClient(_messages())
    refs = OutlookConnector(client, render_bodyless=False).search()

    list_call = next(p for path, p in client.calls if path == "/me/messages")
    assert list_call is not None
    assert list_call["$filter"] == "hasAttachments eq true"
    assert [r.message_id for r in refs] == ["o1", "o2"]
    assert refs[0].sender == "billing@kpn.com"
    assert refs[0].received_at == datetime(2026, 5, 1, 10, 0, tzinfo=UTC)


def test_search_bodyless_mode_drops_attachment_filter() -> None:
    client = FakeGraphClient(_messages())
    OutlookConnector(client).search()  # render_bodyless=True by default

    list_call = next(p for path, p in client.calls if path == "/me/messages")
    assert "$filter" not in list_call  # no since, no attachment requirement


def test_search_with_since_adds_received_filter() -> None:
    client = FakeGraphClient(_messages())
    OutlookConnector(client, render_bodyless=False).search(datetime(2026, 5, 1, 12, 0, tzinfo=UTC))

    list_call = next(p for path, p in client.calls if path == "/me/messages")
    assert "receivedDateTime gt 2026-05-01T12:00:00Z" in list_call["$filter"]


def test_download_pdfs_renders_body_when_no_attachment() -> None:
    message = {
        "id": "o3",
        "subject": "Factuur mei",
        "sender": "facturen@kpn.be",
        "receivedDateTime": "2026-05-03T09:00:00Z",
        "attachments": [],
        "body": {"contentType": "html", "content": "<p>Bedrag: 149,95 EUR</p>"},
    }
    connector = OutlookConnector(FakeGraphClient([message]))
    ref = next(r for r in connector.search() if r.message_id == "o3")

    pdfs = connector.download_pdfs(ref)

    assert len(pdfs) == 1
    assert pdfs[0].pdf_bytes.startswith(PDF_MAGIC)  # a real, prefilter-passing PDF
    assert pdfs[0].filename == "outlook-email-20260503.pdf"


def test_download_pdfs_skips_body_render_when_disabled() -> None:
    message = {
        "id": "o4",
        "subject": "Factuur mei",
        "sender": "facturen@kpn.be",
        "receivedDateTime": "2026-05-04T09:00:00Z",
        "attachments": [],
        "body": {"contentType": "text", "content": "Bedrag: 149,95 EUR"},
    }
    connector = OutlookConnector(FakeGraphClient([message]), render_bodyless=False)
    ref = next(r for r in connector.search() if r.message_id == "o4")

    assert connector.download_pdfs(ref) == []  # no attachment, rendering off


def test_download_pdfs_decodes_and_skips_non_pdf() -> None:
    client = FakeGraphClient(_messages())
    connector = OutlookConnector(client)
    ref = next(r for r in connector.search() if r.message_id == "o2")

    pdfs = connector.download_pdfs(ref)

    assert len(pdfs) == 1  # the .ics itemAttachment is skipped
    assert isinstance(pdfs[0], FetchedPdf)
    assert pdfs[0].filename == "factuur2.pdf"
    assert pdfs[0].pdf_bytes == _PDF2


# ----- fetch (shared watermark + dedup) tests ---------------------------------


async def _fresh_engine(tmp_path: Path) -> Any:
    engine = create_engine(tmp_path / "t.db")
    await init_schema(engine)
    return engine


async def test_fetch_new_pdfs_advances_watermark(tmp_path: Path) -> None:
    engine = await _fresh_engine(tmp_path)
    factory = create_session_factory(engine)
    connector = OutlookConnector(FakeGraphClient(_messages()))

    async with UnitOfWork(factory) as uow:
        pdfs = await fetch_new_pdfs(connector, uow)
        await uow.commit()
        count = len(pdfs)

    async with UnitOfWork(factory) as uow:
        watermark = await uow.source_status.get_watermark("outlook")

    await engine.dispose()

    assert count == 2
    assert watermark is not None
    got = watermark if watermark.tzinfo else watermark.replace(tzinfo=UTC)
    assert got == datetime(2026, 5, 2, 10, 0, tzinfo=UTC)


async def test_fetch_skips_already_processed_message(tmp_path: Path) -> None:
    engine = await _fresh_engine(tmp_path)
    factory = create_session_factory(engine)

    async with UnitOfWork(factory) as uow:
        await uow.invoices.add(
            Invoice(source="outlook", source_message_id="o1", content_hash="already")
        )
        await uow.commit()

    connector = OutlookConnector(FakeGraphClient(_messages()))
    async with UnitOfWork(factory) as uow:
        pdfs = await fetch_new_pdfs(connector, uow)
        await uow.commit()
        filenames = [p.filename for p in pdfs]

    await engine.dispose()

    assert filenames == ["factuur2.pdf"]  # o1 skipped (already an invoice)
