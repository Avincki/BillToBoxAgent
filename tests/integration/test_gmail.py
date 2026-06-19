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
from billtobox_agent.mail import GMAIL_QUERY, GmailConnector, fetch_new_pdfs

_PDF1 = b"%PDF-1.7 invoice one"
_PDF2 = b"%PDF-1.7 invoice two"


def _messages() -> list[dict[str, Any]]:
    return [
        {
            "id": "m1",
            "internal_date_ms": 1_700_000_000_000,
            "subject": "Invoice 1",
            "sender": "billing@kpn.com",
            "pdfs": [("invoice1.pdf", _PDF1)],
        },
        {
            "id": "m2",
            "internal_date_ms": 1_700_100_000_000,
            "subject": "Factuur 2",
            "sender": "facturen@proximus.be",
            "pdfs": [("factuur2.pdf", _PDF2)],
        },
    ]


# ----- a minimal fake of the googleapiclient Gmail resource -------------------
# camelCase params mirror the real API kwargs; builtins (id/format) are shadowed
# the same way the Google client does.


class _Req:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def execute(self) -> dict[str, Any]:
        return self._result


class _Attachments:
    def __init__(self, store: FakeGmailService) -> None:
        self._store = store

    def get(self, *, userId: str, messageId: str, id: str) -> _Req:
        index = int(id.rsplit("-", 1)[1])
        _filename, data = self._store.by_id[messageId]["pdfs"][index]
        return _Req({"data": base64.urlsafe_b64encode(data).decode()})


class _Messages:
    def __init__(self, store: FakeGmailService) -> None:
        self._store = store

    def list(self, *, userId: str, q: str, pageToken: str | None = None) -> _Req:
        self._store.last_query = q
        return _Req({"messages": [{"id": m["id"]} for m in self._store.messages]})

    def get(
        self,
        *,
        userId: str,
        id: str,
        format: str | None = None,
        metadataHeaders: list[str] | None = None,
    ) -> _Req:
        message = self._store.by_id[id]
        if format == "metadata":
            return _Req(
                {
                    "id": id,
                    "internalDate": str(message["internal_date_ms"]),
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": message["subject"]},
                            {"name": "From", "value": message["sender"]},
                        ]
                    },
                }
            )
        parts = [
            {
                "filename": filename,
                "mimeType": "application/pdf",
                "body": {"attachmentId": f"{id}-att-{i}"},
            }
            for i, (filename, _data) in enumerate(message["pdfs"])
        ]
        return _Req({"id": id, "payload": {"parts": parts}})

    def attachments(self) -> _Attachments:
        return _Attachments(self._store)


class _Users:
    def __init__(self, store: FakeGmailService) -> None:
        self._store = store

    def messages(self) -> _Messages:
        return _Messages(self._store)


class FakeGmailService:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = messages
        self.by_id = {m["id"]: m for m in messages}
        self.last_query = ""

    def users(self) -> _Users:
        return _Users(self)


# ----- connector tests --------------------------------------------------------


def test_search_builds_query_and_parses_refs() -> None:
    service = FakeGmailService(_messages())
    connector = GmailConnector(service)

    refs = connector.search()

    assert service.last_query == GMAIL_QUERY
    assert [r.message_id for r in refs] == ["m1", "m2"]  # sorted by received_at
    assert refs[0].sender == "billing@kpn.com"
    assert refs[0].subject == "Invoice 1"
    assert refs[0].received_at == datetime.fromtimestamp(1_700_000_000, tz=UTC)


def test_search_with_since_adds_after_clause() -> None:
    service = FakeGmailService(_messages())
    connector = GmailConnector(service)

    connector.search(datetime.fromtimestamp(1_700_050_000, tz=UTC))

    assert "after:1700050000" in service.last_query


def test_download_pdfs_decodes_attachment_bytes() -> None:
    connector = GmailConnector(FakeGmailService(_messages()))
    ref = connector.search()[0]

    pdfs = connector.download_pdfs(ref)

    assert len(pdfs) == 1
    assert pdfs[0].filename == "invoice1.pdf"
    assert pdfs[0].pdf_bytes == _PDF1
    assert pdfs[0].message.message_id == "m1"


# ----- fetch (watermark + dedup) tests ----------------------------------------


async def _fresh_engine(tmp_path: Path) -> Any:
    engine = create_engine(tmp_path / "t.db")
    await init_schema(engine)
    return engine


async def test_fetch_new_pdfs_advances_watermark(tmp_path: Path) -> None:
    engine = await _fresh_engine(tmp_path)
    factory = create_session_factory(engine)
    connector = GmailConnector(FakeGmailService(_messages()))

    async with UnitOfWork(factory) as uow:
        pdfs = await fetch_new_pdfs(connector, uow)
        await uow.commit()
        count = len(pdfs)

    async with UnitOfWork(factory) as uow:
        watermark = await uow.source_status.get_watermark("gmail")

    await engine.dispose()

    assert count == 2
    assert watermark is not None
    got = watermark if watermark.tzinfo else watermark.replace(tzinfo=UTC)
    assert got == datetime.fromtimestamp(1_700_100_000, tz=UTC)


async def test_fetch_skips_already_processed_message(tmp_path: Path) -> None:
    engine = await _fresh_engine(tmp_path)
    factory = create_session_factory(engine)

    async with UnitOfWork(factory) as uow:
        await uow.invoices.add(
            Invoice(source="gmail", source_message_id="m1", content_hash="already")
        )
        await uow.commit()

    connector = GmailConnector(FakeGmailService(_messages()))
    async with UnitOfWork(factory) as uow:
        pdfs = await fetch_new_pdfs(connector, uow)
        await uow.commit()
        filenames = [p.filename for p in pdfs]

    await engine.dispose()

    assert filenames == ["factuur2.pdf"]  # m1 skipped (already an invoice)
