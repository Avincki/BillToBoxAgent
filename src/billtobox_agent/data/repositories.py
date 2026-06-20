"""Repositories — focused query/write helpers over the ORM models.

The agent worker is the sole writer; the dashboard reads and makes small status
writes. Every ``agent_events`` write goes through :meth:`AgentEventsRepository.add`,
which redacts inputs/outputs at the boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billtobox_agent.data.models import (
    AgentEvent,
    AgentEventLevel,
    AgentEventType,
    Invoice,
    InvoiceStatus,
    Run,
    SourceStatus,
)
from billtobox_agent.utils.redact import redact


class InvoicesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, invoice: Invoice) -> Invoice:
        self._session.add(invoice)
        await self._session.flush()
        return invoice

    async def get(self, invoice_id: int) -> Invoice | None:
        return await self._session.get(Invoice, invoice_id)

    async def get_by_content_hash(self, content_hash: str) -> Invoice | None:
        result = await self._session.execute(
            select(Invoice).where(Invoice.content_hash == content_hash)
        )
        return result.scalar_one_or_none()

    async def exists_content_hash(self, content_hash: str) -> bool:
        return await self.get_by_content_hash(content_hash) is not None

    async def mark_stored(self, invoice_id: int, *, drive_file_id: str, drive_path: str) -> Invoice:
        """Record a successful Drive upload: set the file id/path and status ``stored``."""
        invoice = await self.get(invoice_id)
        if invoice is None:
            raise ValueError(f"invoice {invoice_id} not found")
        invoice.drive_file_id = drive_file_id
        invoice.drive_path = drive_path
        invoice.status = InvoiceStatus.STORED.value
        await self._session.flush()
        return invoice

    async def set_status(self, invoice_id: int, status: InvoiceStatus) -> Invoice:
        """Transition an invoice to ``status`` (a SQLite-only status write)."""
        invoice = await self.get(invoice_id)
        if invoice is None:
            raise ValueError(f"invoice {invoice_id} not found")
        invoice.status = status.value
        await self._session.flush()
        return invoice

    async def record_extraction(
        self,
        invoice_id: int,
        *,
        vendor: str | None,
        invoice_date: date | None,
        amount: float | None,
        currency: str | None,
        confidence: float,
        fy_label: str | None,
        quarter: str | None,
    ) -> Invoice:
        """Persist the Claude-extracted fields + computed accounting period on the row."""
        invoice = await self.get(invoice_id)
        if invoice is None:
            raise ValueError(f"invoice {invoice_id} not found")
        invoice.vendor = vendor
        invoice.invoice_date = invoice_date
        invoice.amount = amount
        invoice.currency = currency
        invoice.confidence = confidence
        invoice.fy_label = fy_label
        invoice.quarter = quarter
        await self._session.flush()
        return invoice

    async def update_fields(
        self,
        invoice_id: int,
        *,
        vendor: str | None,
        invoice_date: date | None,
        amount: float | None,
        currency: str | None,
        fy_label: str | None,
        quarter: str | None,
    ) -> Invoice:
        """Apply a human field edit (task 19) + the recomputed period. Leaves
        confidence, status, and Drive location untouched."""
        invoice = await self.get(invoice_id)
        if invoice is None:
            raise ValueError(f"invoice {invoice_id} not found")
        invoice.vendor = vendor
        invoice.invoice_date = invoice_date
        invoice.amount = amount
        invoice.currency = currency
        invoice.fy_label = fy_label
        invoice.quarter = quarter
        await self._session.flush()
        return invoice

    async def set_drive_path(self, invoice_id: int, drive_path: str) -> Invoice:
        """Update only the logical Drive path (after a quarter-change file move)."""
        invoice = await self.get(invoice_id)
        if invoice is None:
            raise ValueError(f"invoice {invoice_id} not found")
        invoice.drive_path = drive_path
        await self._session.flush()
        return invoice

    async def exists_source_message_id(self, source: str, source_message_id: str) -> bool:
        result = await self._session.execute(
            select(Invoice.id)
            .where(Invoice.source == source, Invoice.source_message_id == source_message_id)
            .limit(1)
        )
        return result.first() is not None

    async def list(self) -> Sequence[Invoice]:
        result = await self._session.execute(select(Invoice).order_by(Invoice.created_at.desc()))
        return result.scalars().all()

    async def list_by_status(self, status: str) -> Sequence[Invoice]:
        result = await self._session.execute(
            select(Invoice).where(Invoice.status == status).order_by(Invoice.created_at.desc())
        )
        return result.scalars().all()

    async def list_by_statuses(self, statuses: Sequence[str]) -> Sequence[Invoice]:
        """Invoices in any of ``statuses`` — backs the dashboard exceptions queue."""
        result = await self._session.execute(
            select(Invoice).where(Invoice.status.in_(statuses)).order_by(Invoice.created_at.desc())
        )
        return result.scalars().all()


class RunsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(self) -> Run:
        run = Run()
        self._session.add(run)
        await self._session.flush()
        return run

    async def get(self, run_id: int) -> Run | None:
        return await self._session.get(Run, run_id)

    async def list(self, limit: int = 50) -> Sequence[Run]:
        result = await self._session.execute(
            select(Run).order_by(Run.started_at.desc()).limit(limit)
        )
        return result.scalars().all()

    async def finish(
        self,
        run: Run,
        *,
        items_fetched: int = 0,
        items_extracted: int = 0,
        items_stored: int = 0,
        items_flagged: int = 0,
        error_summary: str | None = None,
    ) -> Run:
        run.ended_at = datetime.now(UTC)
        run.items_fetched = items_fetched
        run.items_extracted = items_extracted
        run.items_stored = items_stored
        run.items_flagged = items_flagged
        run.error_summary = error_summary
        await self._session.flush()
        return run


class SourceStatusRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, source: str) -> SourceStatus | None:
        return await self._session.get(SourceStatus, source)

    async def list(self) -> Sequence[SourceStatus]:
        """All per-source health rows — backs the dashboard /debug table."""
        result = await self._session.execute(select(SourceStatus).order_by(SourceStatus.source))
        return result.scalars().all()

    async def get_or_create(self, source: str) -> SourceStatus:
        row = await self.get(source)
        if row is None:
            row = SourceStatus(source=source)
            self._session.add(row)
            await self._session.flush()
        return row

    async def get_watermark(self, source: str) -> datetime | None:
        row = await self.get(source)
        return row.watermark if row is not None else None

    async def set_watermark(self, source: str, watermark: datetime) -> SourceStatus:
        row = await self.get_or_create(source)
        row.watermark = watermark
        row.last_success_at = datetime.now(UTC)
        await self._session.flush()
        return row

    async def record_error(self, source: str, message: str) -> SourceStatus:
        row = await self.get_or_create(source)
        row.last_error_at = datetime.now(UTC)
        row.last_error_message = message
        await self._session.flush()
        return row


class AgentEventsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        event_type: AgentEventType | str,
        summary: str,
        run_id: int | None = None,
        invoice_id: int | None = None,
        step: int = 0,
        tool: str | None = None,
        level: AgentEventLevel | str = AgentEventLevel.INFO,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
    ) -> AgentEvent:
        event = AgentEvent(
            run_id=run_id,
            invoice_id=invoice_id,
            step=step,
            event_type=str(event_type),
            tool=tool,
            level=str(level),
            summary=summary,
            inputs_json=redact(inputs) if inputs is not None else None,
            outputs_json=redact(outputs) if outputs is not None else None,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def list(
        self,
        *,
        run_id: int | None = None,
        invoice_id: int | None = None,
        level: str | None = None,
        limit: int = 100,
    ) -> Sequence[AgentEvent]:
        stmt = select(AgentEvent)
        if run_id is not None:
            stmt = stmt.where(AgentEvent.run_id == run_id)
        if invoice_id is not None:
            stmt = stmt.where(AgentEvent.invoice_id == invoice_id)
        if level is not None:
            stmt = stmt.where(AgentEvent.level == level)
        stmt = stmt.order_by(AgentEvent.id.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return result.scalars().all()
