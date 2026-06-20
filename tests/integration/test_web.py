from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path

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
    StorageConfig,
)
from billtobox_agent.data import (
    AgentEventType,
    Invoice,
    InvoiceStatus,
    UnitOfWork,
)
from billtobox_agent.web import create_app
from billtobox_agent.web.api import _LOG_FILENAME, _tail_log_sse


def _make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        anthropic=AnthropicConfig(api_key="sk-ant-x"),
        google=GoogleConfig(client_id="g", client_secret="gs"),
        microsoft=MicrosoftConfig(client_id="m"),
        billtobox=BilltoboxConfig(
            mailbox_address="box@billtobox.example", sender_address="me@example.com"
        ),
        smtp=SmtpConfig(host="smtp.example", username="u", password="p"),
        storage=StorageConfig(sqlite_path=tmp_path / "dash.db"),
        logging=LoggingConfig(log_dir=tmp_path / "logs"),
    )


async def _seed(app: object) -> dict[str, int]:
    factory = app.state.session_factory  # type: ignore[attr-defined]
    async with UnitOfWork(factory) as uow:
        run = await uow.runs.start()
        await uow.runs.finish(
            run, items_fetched=2, items_extracted=2, items_stored=1, items_flagged=1
        )
        stored = await uow.invoices.add(
            Invoice(
                source="gmail",
                source_message_id="m1",
                content_hash="h1",
                vendor="KPN",
                invoice_date=date(2026, 5, 31),
                amount=149.95,
                currency="EUR",
                confidence=0.96,
                fy_label="2026",
                quarter="Q2",
                status=InvoiceStatus.STORED.value,
                drive_file_id="drive-file-1",
                drive_path="Invoices/2026/Q2/gmail_2026-05-31_149.95.pdf",
                run_id=run.id,
            )
        )
        flagged = await uow.invoices.add(
            Invoice(
                source="outlook",
                source_message_id="m2",
                content_hash="h2",
                vendor="Acme",
                confidence=0.40,
                status=InvoiceStatus.REVIEWED.value,
                run_id=run.id,
            )
        )
        # A redacted event: the secret must be scrubbed before it ever reaches a view.
        await uow.agent_events.add(
            event_type=AgentEventType.TOOL_CALL,
            summary="extract_invoice call",
            run_id=run.id,
            invoice_id=stored.id,
            tool="extract_invoice",
            inputs={"api_key": "supersecretvalue", "model": "claude-opus-4-8"},
        )
        await uow.source_status.set_watermark("gmail", datetime(2026, 6, 1, 12, 0, tzinfo=UTC))
        await uow.commit()
        return {"stored": stored.id, "flagged": flagged.id, "run": run.id}


@pytest_asyncio.fixture
async def web(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, dict[str, int]]]:
    app = create_app(_make_config(tmp_path))
    async with app.router.lifespan_context(app):
        ids = await _seed(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test", headers={"Origin": "http://test"}
        ) as client:
            yield client, ids


# ----- read-only views --------------------------------------------------------


async def test_invoice_list_renders(web: tuple[AsyncClient, dict[str, int]]) -> None:
    client, _ids = web
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "KPN" in resp.text
    assert "drive.google.com/file/d/drive-file-1" in resp.text


async def test_invoice_detail_shows_redacted_audit_trail(
    web: tuple[AsyncClient, dict[str, int]],
) -> None:
    client, ids = web
    resp = await client.get(f"/invoices/{ids['stored']}")
    assert resp.status_code == 200
    assert "extract_invoice" in resp.text
    # The secret is stored redacted, so it can never appear in the rendered page.
    assert "supersecretvalue" not in resp.text
    assert "***" in resp.text


async def test_invoice_detail_404(web: tuple[AsyncClient, dict[str, int]]) -> None:
    client, _ids = web
    resp = await client.get("/invoices/999999")
    assert resp.status_code == 404


async def test_exceptions_lists_only_flagged(web: tuple[AsyncClient, dict[str, int]]) -> None:
    client, _ids = web
    resp = await client.get("/exceptions")
    assert resp.status_code == 200
    assert "Acme" in resp.text  # the reviewed invoice
    assert "KPN" not in resp.text  # the stored invoice is not an exception


async def test_runs_renders(web: tuple[AsyncClient, dict[str, int]]) -> None:
    client, _ids = web
    resp = await client.get("/runs")
    assert resp.status_code == 200
    assert "Run history" in resp.text


async def test_activity_renders_redacted_events(
    web: tuple[AsyncClient, dict[str, int]],
) -> None:
    client, _ids = web
    resp = await client.get("/activity")
    assert resp.status_code == 200
    assert "extract_invoice" in resp.text
    assert "supersecretvalue" not in resp.text


async def test_activity_level_filter(web: tuple[AsyncClient, dict[str, int]]) -> None:
    client, _ids = web
    resp = await client.get("/activity", params={"level": "error"})
    assert resp.status_code == 200
    assert "No events." in resp.text  # the seeded event is info, not error


async def test_debug_shows_source_health(web: tuple[AsyncClient, dict[str, int]]) -> None:
    client, _ids = web
    resp = await client.get("/debug")
    assert resp.status_code == 200
    assert "Source health" in resp.text
    assert "gmail" in resp.text


async def test_logs_page_renders(web: tuple[AsyncClient, dict[str, int]]) -> None:
    client, _ids = web
    resp = await client.get("/logs")
    assert resp.status_code == 200
    assert "/api/logs/stream" in resp.text


# ----- JSON API + SSE ---------------------------------------------------------


async def test_health_endpoint(web: tuple[AsyncClient, dict[str, int]]) -> None:
    client, _ids = web
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert any(s["source"] == "gmail" for s in body["sources"])


async def test_tail_log_sse_replays_a_line(tmp_path: Path) -> None:
    # Drive the SSE generator directly (consuming the infinite stream over HTTP
    # would hang): it must replay a line within the window as an SSE `data:` event.
    log_path = tmp_path / _LOG_FILENAME
    log_path.write_text(
        '{"event": "marker_x", "level": "info", "timestamp": "2026-06-20T12:00:00+02:00"}\n',
        encoding="utf-8",
    )

    class _FakeRequest:
        async def is_disconnected(self) -> bool:
            return False

    replay_since = datetime(2026, 6, 1, tzinfo=UTC)
    gen = _tail_log_sse(_FakeRequest(), log_path, replay_since)  # type: ignore[arg-type]
    try:
        got = await gen.__anext__()
    finally:
        await gen.aclose()

    assert got.startswith("data: ")
    assert "marker_x" in got


def test_default_dashboard_binds_localhost(tmp_path: Path) -> None:
    # "Nothing binds to a public interface": the default host is loopback-only.
    config = _make_config(tmp_path)
    assert config.web.host == "127.0.0.1"
