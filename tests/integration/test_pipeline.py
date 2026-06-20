from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from billtobox_agent.config.models import (
    AnthropicConfig,
    AppConfig,
    BilltoboxConfig,
    GoogleConfig,
    MicrosoftConfig,
    SmtpConfig,
    Source,
    SourcesConfig,
)
from billtobox_agent.data import (
    InvoiceStatus,
    UnitOfWork,
    create_engine,
    create_session_factory,
    init_schema,
)
from billtobox_agent.drive import DriveConnector
from billtobox_agent.mail.base import FetchedPdf, MailMessageRef
from billtobox_agent.pipeline import WorkerContext, run_once

# ----- canned PDFs + the model's responses for each ---------------------------

_APPROVED = b"%PDF-1.7 approved invoice"
_LOWCONF = b"%PDF-1.7 low-confidence invoice"
_NOTINV = b"%PDF-1.7 not an invoice"
_REJECT = b"%PDF-1.7 a newsletter"  # passes magic-byte check, fails sender/subject

_RESPONSES: dict[bytes, str] = {
    _APPROVED: json.dumps(
        {
            "is_invoice": True,
            "confidence": 0.96,
            "vendor": "KPN",
            "invoice_date": "2026-05-31",
            "amount": 149.95,
            "currency": "EUR",
        }
    ),
    _LOWCONF: json.dumps(
        {
            "is_invoice": True,
            "confidence": 0.40,
            "vendor": "Acme",
            "invoice_date": "2026-03-10",
            "amount": 50.0,
            "currency": "EUR",
        }
    ),
    _NOTINV: json.dumps(
        {
            "is_invoice": False,
            "confidence": 0.9,
            "vendor": None,
            "invoice_date": None,
            "amount": None,
            "currency": None,
        }
    ),
}


def _ts(minute: int) -> datetime:
    return datetime(2026, 6, 1, 12, minute, 0, tzinfo=UTC)


def _messages() -> list[tuple[str, str, str, datetime, list[tuple[str, bytes]]]]:
    return [
        ("m1", "Invoice 1", "billing@kpn.com", _ts(1), [("inv1.pdf", _APPROVED)]),
        ("m2", "Factuur 2", "ar@acme.com", _ts(2), [("fac2.pdf", _LOWCONF)]),
        ("m3", "Rekening 3", "no@reply.com", _ts(3), [("rek3.pdf", _NOTINV)]),
        ("m4", "weekly newsletter", "news@spam.com", _ts(4), [("news.pdf", _REJECT)]),
        # same bytes as m1 from a different message -> content-hash duplicate.
        ("m5", "Invoice duplicate", "billing@kpn.com", _ts(5), [("inv5.pdf", _APPROVED)]),
    ]


# ----- fake mail connector ----------------------------------------------------


class FakeMailConnector:
    def __init__(
        self,
        source: str,
        messages: list[tuple[str, str, str, datetime, list[tuple[str, bytes]]]],
    ) -> None:
        self.source = source
        self._messages = messages

    def search(self, since: datetime | None = None) -> list[MailMessageRef]:
        refs = [
            MailMessageRef(self.source, mid, subject, sender, ts)
            for (mid, subject, sender, ts, _pdfs) in self._messages
            if since is None or ts > since
        ]
        refs.sort(key=lambda r: r.received_at)
        return refs

    def download_pdfs(self, ref: MailMessageRef) -> list[FetchedPdf]:
        for mid, _subject, _sender, _ts, pdfs in self._messages:
            if mid == ref.message_id:
                return [FetchedPdf(message=ref, filename=fn, pdf_bytes=b) for fn, b in pdfs]
        return []


# ----- fake Anthropic client (document block -> canned JSON) ------------------


def _response(text: str) -> Any:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


class _FakeMessages:
    def __init__(self, responses: dict[bytes, str]) -> None:
        self._responses = responses
        self.calls = 0

    def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        b64 = kwargs["messages"][0]["content"][0]["source"]["data"]
        pdf = base64.standard_b64decode(b64)
        return _response(self._responses[pdf])


class FakeAnthropic:
    def __init__(self, responses: dict[bytes, str]) -> None:
        self.messages = _FakeMessages(responses)


# ----- fake Drive service (folders + files in one resource) -------------------

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
        name, parent = _name_and_parent(q)
        pool = self._store.uploaded if "mimeType !=" in q else self._store.dirs
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
            self._store.uploaded.append(
                {"id": fid, "name": name, "parent": parent, "content": content}
            )
            self._store.create_calls.append(("file", name, parent))
        else:
            fid = f"fld-{self._store.counter}"
            self._store.dirs.append({"id": fid, "name": name, "parent": parent})
            self._store.create_calls.append(("folder", name, parent))
        return _Resp({"id": fid})


class FakeDriveService:
    def __init__(self) -> None:
        self.dirs: list[dict[str, Any]] = []
        self.uploaded: list[dict[str, Any]] = []
        self.create_calls: list[tuple[str, str, str]] = []
        self.counter = 0

    def files(self) -> _Files:
        return _Files(self)


# ----- context assembly -------------------------------------------------------


def _config() -> AppConfig:
    return AppConfig(
        anthropic=AnthropicConfig(api_key="sk-ant-test"),
        google=GoogleConfig(client_id="gid", client_secret="gsec"),
        microsoft=MicrosoftConfig(client_id="mid"),
        billtobox=BilltoboxConfig(
            mailbox_address="box@billtobox.example", sender_address="me@example.com"
        ),
        smtp=SmtpConfig(host="smtp.example.com", username="u", password="p"),
        sources=SourcesConfig(polling=(Source.GMAIL,)),
    )


async def _context(
    tmp_path: Path, *, dry_run: bool = False
) -> tuple[Any, WorkerContext, FakeDriveService]:
    engine = create_engine(tmp_path / "pipeline.db")
    await init_schema(engine)
    factory = create_session_factory(engine)
    drive_service = FakeDriveService()
    ctx = WorkerContext(
        config=_config(),
        session_factory=factory,
        mail_connectors={"gmail": FakeMailConnector("gmail", _messages())},
        drive=DriveConnector(drive_service),
        anthropic_client=FakeAnthropic(_RESPONSES),  # type: ignore[arg-type]
        dry_run=dry_run,
    )
    return engine, ctx, drive_service


# ----- tests ------------------------------------------------------------------


async def test_run_once_end_to_end(tmp_path: Path) -> None:
    engine, ctx, drive = await _context(tmp_path)

    summary = await run_once(ctx)

    assert (summary.items_fetched, summary.items_extracted) == (5, 3)
    assert (summary.items_stored, summary.items_flagged) == (1, 2)
    assert summary.errors == []

    async with UnitOfWork(ctx.session_factory) as uow:
        invoices = await uow.invoices.list()
        # Pull values into plain locals while the session is open (avoid detached reads).
        mids = {inv.source_message_id for inv in invoices}
        statuses = {inv.source_message_id: inv.status for inv in invoices}
        m1 = next(inv for inv in invoices if inv.source_message_id == "m1")
        m1_fields = (m1.vendor, m1.amount, m1.confidence, m1.fy_label, m1.quarter)
        m1_drive = (m1.drive_file_id, m1.drive_path)
        m2_confidence = next(inv.confidence for inv in invoices if inv.source_message_id == "m2")
        run = await uow.runs.get(summary.run_id)
        assert run is not None
        run_counts = (run.items_fetched, run.items_extracted, run.items_stored, run.items_flagged)
        run_ended = run.ended_at is not None
        run_error = run.error_summary
        tools_seen = {e.tool for e in await uow.agent_events.list(limit=500)}
        watermark = await uow.source_status.get_watermark("gmail")

    await engine.dispose()

    # Only the three non-rejected, non-duplicate messages became rows.
    assert mids == {"m1", "m2", "m3"}

    # m1: auto-approved -> stored to Drive with fields + computed period recorded.
    assert statuses["m1"] == InvoiceStatus.STORED
    assert m1_fields == ("KPN", 149.95, 0.96, "2026", "Q2")
    assert m1_drive == (drive.uploaded[0]["id"], "Invoices/2026/Q2/gmail_2026-05-31_149.95.pdf")

    # m2: low confidence -> flagged for review (fields still recorded).
    assert statuses["m2"] == InvoiceStatus.REVIEWED
    assert m2_confidence == 0.40
    # m3: not an invoice -> flagged for review.
    assert statuses["m3"] == InvoiceStatus.REVIEWED

    # Run row mirrors the summary counts.
    assert run_counts == (5, 3, 1, 2)
    assert run_ended
    assert run_error is None

    # Every pipeline step left an audit trail.
    assert {
        "prefilter",
        "check_duplicate",
        "extract_invoice",
        "ensure_quarter_folder",
        "store_pdf_to_drive",
        "flag_for_review",
    } <= tools_seen

    # Drive: the quarter tree was created once and the PDF uploaded into it.
    assert drive.create_calls == [
        ("folder", "Invoices", "root"),
        ("folder", "2026", "fld-1"),
        ("folder", "Q2", "fld-2"),
        ("file", "gmail_2026-05-31_149.95.pdf", "fld-3"),
    ]
    assert drive.uploaded[0]["content"] == _APPROVED

    # Watermark advanced to the newest message seen.
    assert watermark is not None
    got = watermark if watermark.tzinfo else watermark.replace(tzinfo=UTC)
    assert got == _ts(5)


async def test_rerun_reprocesses_nothing(tmp_path: Path) -> None:
    engine, ctx, drive = await _context(tmp_path)

    await run_once(ctx)
    creates_after_first = list(drive.create_calls)

    second = await run_once(ctx)

    async with UnitOfWork(ctx.session_factory) as uow:
        invoice_count = len(await uow.invoices.list())

    await engine.dispose()

    # Watermark holds: nothing new is fetched, so nothing is processed or uploaded.
    assert (second.items_fetched, second.items_extracted) == (0, 0)
    assert (second.items_stored, second.items_flagged) == (0, 0)
    assert invoice_count == 3  # unchanged
    assert drive.create_calls == creates_after_first  # no new Drive writes


async def test_content_hash_holds_when_watermark_is_bypassed(tmp_path: Path) -> None:
    engine, ctx, drive = await _context(tmp_path)

    await run_once(ctx)
    creates_after_first = list(drive.create_calls)

    # Force a full re-fetch by resetting the watermark; dedup must still hold.
    async with UnitOfWork(ctx.session_factory) as uow:
        await uow.source_status.set_watermark("gmail", _ts(0))
        await uow.commit()

    second = await run_once(ctx)

    async with UnitOfWork(ctx.session_factory) as uow:
        invoice_count = len(await uow.invoices.list())

    await engine.dispose()

    # m1/m2/m3 skip on source_message_id; m5 skips on content_hash; m4 re-rejected.
    assert second.items_extracted == 0
    assert second.items_stored == 0
    assert invoice_count == 3  # no new rows
    assert drive.create_calls == creates_after_first  # no new uploads


async def test_dry_run_makes_no_writes(tmp_path: Path) -> None:
    engine, ctx, drive = await _context(tmp_path, dry_run=True)

    summary = await run_once(ctx)

    async with UnitOfWork(ctx.session_factory) as uow:
        invoices = await uow.invoices.list()
        events = await uow.agent_events.list(limit=500)

    await engine.dispose()

    # The read+extract pass still computes intended dispositions...
    assert summary.run_id is None
    assert (summary.items_fetched, summary.items_extracted) == (5, 3)
    assert (summary.items_stored, summary.items_flagged) == (1, 2)
    # ...but nothing is committed and no Drive upload happens.
    assert invoices == []
    assert events == []
    assert drive.create_calls == []
