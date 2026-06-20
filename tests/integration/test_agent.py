from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from billtobox_agent.agent import AgentContext, run_agent
from billtobox_agent.agent.loop import _AgentState, _h_get_agent_events
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
from billtobox_agent.data import (
    AgentEventType,
    InvoiceStatus,
    UnitOfWork,
    create_engine,
    create_session_factory,
    init_schema,
)
from billtobox_agent.drive import DriveConnector
from billtobox_agent.mail.base import FetchedPdf, MailMessageRef
from billtobox_agent.pipeline import RunSummary, compute_content_hash

_APPROVED = b"%PDF-1.7 approved invoice"
_LOWCONF = b"%PDF-1.7 low-confidence invoice"

_EXTRACTIONS = {
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
}


# ----- fake mail connector ----------------------------------------------------


class FakeMailConnector:
    source = "gmail"

    def __init__(self, messages: list[tuple[str, str, str, bytes]]) -> None:
        # (message_id, subject, sender, pdf_bytes)
        self._messages = messages

    def search(self, since: datetime | None = None) -> list[MailMessageRef]:
        refs = []
        for i, (mid, subject, sender, _pdf) in enumerate(self._messages):
            ts = datetime(2026, 6, 1, 12, i, 0, tzinfo=UTC)
            if since is None or ts > since:
                refs.append(MailMessageRef("gmail", mid, subject, sender, ts))
        return refs

    def download_pdfs(self, ref: MailMessageRef) -> list[FetchedPdf]:
        for mid, _subject, _sender, pdf in self._messages:
            if mid == ref.message_id:
                return [FetchedPdf(message=ref, filename=f"{mid}.pdf", pdf_bytes=pdf)]
        return []


# ----- fake Anthropic client (orchestration + extraction in one) --------------


def _tool_response(calls: list[tuple[str, dict[str, Any]]]) -> Any:
    blocks = [
        SimpleNamespace(type="tool_use", id=f"tu-{i}", name=name, input=inp)
        for i, (name, inp) in enumerate(calls)
    ]
    return SimpleNamespace(stop_reason="tool_use", content=blocks)


def _end_response() -> Any:
    return SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text="ok")]
    )


class _FakeMessages:
    def __init__(self, client: FakeAgentClient) -> None:
        self._client = client

    def create(self, **kwargs: Any) -> Any:
        if "tools" in kwargs:  # orchestration turn
            return self._client.next_turn()
        # extraction call (document block, no tools)
        b64 = kwargs["messages"][0]["content"][0]["source"]["data"]
        pdf = base64.standard_b64decode(b64)
        text = self._client.extractions[pdf]
        return SimpleNamespace(
            stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)]
        )


class FakeAgentClient:
    def __init__(
        self,
        turns: list[list[tuple[str, dict[str, Any]]] | None],
        extractions: dict[bytes, str],
    ) -> None:
        self._turns = list(turns)
        self.extractions = extractions
        self.messages = _FakeMessages(self)
        self.orchestration_calls = 0

    def next_turn(self) -> Any:
        self.orchestration_calls += 1
        if not self._turns:
            return _end_response()
        turn = self._turns.pop(0)
        return _end_response() if turn is None else _tool_response(turn)


# ----- fake Drive service (folders + file upload, optionally failing) ---------

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
        self, *, q: str, spaces: str = "drive", fields: str | None = None, pageSize: int = 10
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
            self._store.upload_attempts.append(name)
            if self._store.fail_upload:
                raise RuntimeError("drive upload failed")
            fid = f"file-{self._store.counter}"
            self._store.uploaded.append({"id": fid, "name": name, "parent": parent})
        else:
            fid = f"fld-{self._store.counter}"
            self._store.dirs.append({"id": fid, "name": name, "parent": parent})
        return _Resp({"id": fid})


class FakeDriveService:
    def __init__(self, *, fail_upload: bool = False) -> None:
        self.dirs: list[dict[str, Any]] = []
        self.uploaded: list[dict[str, Any]] = []
        self.upload_attempts: list[str] = []
        self.counter = 0
        self.fail_upload = fail_upload

    def files(self) -> _Files:
        return _Files(self)


# ----- context assembly -------------------------------------------------------


def _make_config(tmp_path: Path, *, known_vendors: tuple[str, ...] = ()) -> AppConfig:
    return AppConfig(
        anthropic=AnthropicConfig(api_key="sk-ant-x"),
        google=GoogleConfig(client_id="g", client_secret="gs"),
        microsoft=MicrosoftConfig(client_id="m"),
        billtobox=BilltoboxConfig(
            mailbox_address="box@billtobox.example",
            sender_address="me@example.com",
            known_vendors=known_vendors,
        ),
        smtp=SmtpConfig(host="smtp.example", username="u", password="p"),
        storage=StorageConfig(sqlite_path=tmp_path / "agent.db"),
        logging=LoggingConfig(log_dir=tmp_path / "logs"),
        sources=SourcesConfig(polling=(Source.GMAIL,)),
    )


async def _factory(tmp_path: Path) -> Any:
    engine = create_engine(tmp_path / "agent.db")
    await init_schema(engine)
    return engine, create_session_factory(engine)


def _ctx(
    config: AppConfig,
    factory: Any,
    client: FakeAgentClient,
    drive: FakeDriveService,
    messages: list[tuple[str, str, str, bytes]],
) -> AgentContext:
    return AgentContext(
        config=config,
        session_factory=factory,
        mail_connectors={"gmail": FakeMailConnector(messages)},  # type: ignore[dict-item]
        drive=DriveConnector(drive),
        anthropic_client=client,  # type: ignore[arg-type]
    )


# ----- tests ------------------------------------------------------------------


async def test_agent_processes_a_batch(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    drive = FakeDriveService()
    h1, h2 = compute_content_hash(_APPROVED), compute_content_hash(_LOWCONF)
    turns: list[list[tuple[str, dict[str, Any]]] | None] = [
        [("search_mail", {"source": "gmail"})],
        [
            ("get_pdf", {"source": "gmail", "message_id": "m1"}),
            ("get_pdf", {"source": "gmail", "message_id": "m2"}),
        ],
        [("check_duplicate", {"content_hash": h1}), ("check_duplicate", {"content_hash": h2})],
        [("extract_invoice", {"pdf_ref": "pdf-1"}), ("extract_invoice", {"pdf_ref": "pdf-2"})],
        [
            ("ensure_quarter_folder", {"pdf_ref": "pdf-1"}),
            ("flag_for_review", {"pdf_ref": "pdf-2", "reason": "low confidence 0.40"}),
        ],
        [("store_pdf_to_drive", {"pdf_ref": "pdf-1", "folder_id": "_"})],
        [("queue_billtobox_upload", {"pdf_ref": "pdf-1"})],
        None,
    ]
    client = FakeAgentClient(turns, _EXTRACTIONS)
    messages = [
        ("m1", "Invoice 1", "billing@kpn.com", _APPROVED),
        ("m2", "Factuur 2", "ar@acme.com", _LOWCONF),
    ]
    ctx = _ctx(_make_config(tmp_path, known_vendors=("KPN",)), factory, client, drive, messages)

    summary = await run_agent(ctx)

    async with UnitOfWork(factory) as uow:
        invoices = await uow.invoices.list()
        by_mid = {inv.source_message_id: inv.status for inv in invoices}
        m1 = next(inv for inv in invoices if inv.source_message_id == "m1")
        m1_drive = m1.drive_file_id
        run = await uow.runs.get(summary.run_id)
        assert run is not None
        run_counts = (run.items_fetched, run.items_extracted, run.items_stored, run.items_flagged)

    await engine.dispose()

    assert (summary.items_fetched, summary.items_extracted) == (2, 2)
    assert (summary.items_stored, summary.items_flagged) == (1, 1)
    assert by_mid == {"m1": InvoiceStatus.UPLOAD_APPROVED, "m2": InvoiceStatus.REVIEWED}
    assert m1_drive is not None
    assert run_counts == (2, 2, 1, 1)


async def test_transient_drive_failure_is_retried_then_flagged(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    drive = FakeDriveService(fail_upload=True)  # every upload raises
    h1 = compute_content_hash(_APPROVED)
    turns: list[list[tuple[str, dict[str, Any]]] | None] = [
        [("search_mail", {"source": "gmail"})],
        [("get_pdf", {"source": "gmail", "message_id": "m1"})],
        [("check_duplicate", {"content_hash": h1})],
        [("extract_invoice", {"pdf_ref": "pdf-1"})],
        [("ensure_quarter_folder", {"pdf_ref": "pdf-1"})],
        [("store_pdf_to_drive", {"pdf_ref": "pdf-1", "folder_id": "_"})],  # retried, fails
        [("flag_for_review", {"pdf_ref": "pdf-1", "reason": "drive upload failed"})],
        None,
    ]
    client = FakeAgentClient(turns, _EXTRACTIONS)
    messages = [("m1", "Invoice 1", "billing@kpn.com", _APPROVED)]
    ctx = _ctx(_make_config(tmp_path), factory, client, drive, messages)

    summary = await run_agent(ctx)

    async with UnitOfWork(factory) as uow:
        invoices = await uow.invoices.list()
        status = invoices[0].status

    await engine.dispose()

    assert len(drive.upload_attempts) == ctx.max_drive_attempts  # retried up to the limit
    assert summary.items_stored == 0
    assert summary.items_flagged == 1
    assert status == InvoiceStatus.REVIEWED


async def test_restart_reprocesses_nothing(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    drive = FakeDriveService()
    h1 = compute_content_hash(_APPROVED)
    messages = [("m1", "Invoice 1", "billing@kpn.com", _APPROVED)]

    first_turns: list[list[tuple[str, dict[str, Any]]] | None] = [
        [("search_mail", {"source": "gmail"})],
        [("get_pdf", {"source": "gmail", "message_id": "m1"})],
        [("check_duplicate", {"content_hash": h1})],
        [("extract_invoice", {"pdf_ref": "pdf-1"})],
        [("ensure_quarter_folder", {"pdf_ref": "pdf-1"})],
        [("store_pdf_to_drive", {"pdf_ref": "pdf-1", "folder_id": "_"})],
        None,
    ]
    ctx1 = _ctx(
        _make_config(tmp_path), factory, FakeAgentClient(first_turns, _EXTRACTIONS), drive, messages
    )
    await run_agent(ctx1)
    uploads_after_first = len(drive.uploaded)

    # Simulate a watermark loss on restart — dedup (source_message_id) must still hold.
    async with UnitOfWork(factory) as uow:
        await uow.source_status.set_watermark("gmail", datetime(2026, 1, 1, tzinfo=UTC))
        await uow.commit()

    # Restart: the agent searches again; m1 is excluded because it is already an invoice.
    second_turns: list[list[tuple[str, dict[str, Any]]] | None] = [
        [("search_mail", {"source": "gmail"})],
        None,
    ]
    client2 = FakeAgentClient(second_turns, _EXTRACTIONS)
    ctx2 = _ctx(_make_config(tmp_path), factory, client2, drive, messages)
    summary2 = await run_agent(ctx2)

    async with UnitOfWork(factory) as uow:
        invoice_count = len(await uow.invoices.list())

    await engine.dispose()

    assert (summary2.items_fetched, summary2.items_stored) == (0, 0)
    assert invoice_count == 1  # no new invoice
    assert len(drive.uploaded) == uploads_after_first  # no new Drive upload


async def test_get_agent_events_returns_redacted_steps(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    async with UnitOfWork(factory) as uow:
        run = await uow.runs.start()
        # An event whose inputs carry a secret — stored redacted by the repo.
        await uow.agent_events.add(
            event_type=AgentEventType.TOOL_CALL,
            summary="extract_invoice",
            run_id=run.id,
            tool="extract_invoice",
            inputs={"api_key": "supersecretvalue", "model": "claude-opus-4-8"},
        )
        await uow.commit()
        run_id = run.id

        state = _AgentState(
            ctx=None,  # type: ignore[arg-type]
            uow=uow,
            run_id=run_id,
            summary=RunSummary(run_id=run_id),
        )
        output, is_error = await _h_get_agent_events(state, {"run_id": run_id})

    await engine.dispose()

    assert is_error is False
    events = output["events"]
    assert len(events) == 1
    assert events[0]["tool"] == "extract_invoice"
    rendered = json.dumps(events)
    assert "supersecretvalue" not in rendered  # the secret never surfaces
    assert events[0]["outputs"] is None or "supersecretvalue" not in json.dumps(events[0])
