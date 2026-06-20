"""SQLAlchemy ORM models for the BillToBoxAgent SQLite database.

Tables: ``invoices`` (one row per invoice, dedup via the unique ``content_hash``),
``runs`` (per-run summary), ``source_status`` (per-source watermark + health), and
``agent_events`` (the fine-grained, redacted audit trail — decisions.md §2).
Timestamps are timezone-aware UTC, matching HomeEnergyCenter's convention.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Source(StrEnum):
    GMAIL = "gmail"
    OUTLOOK = "outlook"
    DOCCLE = "doccle"


class InvoiceStatus(StrEnum):
    NEW = "new"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    STORED = "stored"
    UPLOAD_APPROVED = "upload_approved"
    UPLOADED = "uploaded"
    REJECTED = "rejected"


class AgentEventType(StrEnum):
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    DECISION = "decision"
    LLM_MESSAGE = "llm_message"
    ERROR = "error"


class AgentEventLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Run(Base):
    """One agent run: start/end and per-run item counts (dashboard history)."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    items_fetched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_extracted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_stored: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_flagged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class Invoice(Base):
    """One invoice. ``content_hash`` is unique — a PDF is never stored twice."""

    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("content_hash", name="uq_invoices_content_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    source_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    vendor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    fy_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    quarter: Mapped[str | None] = mapped_column(String(2), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=InvoiceStatus.NEW.value, nullable=False)
    drive_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    drive_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    # Set when the PDF is emailed to Billtobox (task 20); NULL guards against a double-send.
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceStatus(Base):
    """Per-source fetch watermark and last success/error (reruns + health)."""

    __tablename__ = "source_status"

    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    # Last-seen message timestamp; reruns fetch only items newer than this.
    watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class AgentEvent(Base):
    """A single redacted step in the agent's audit trail (decisions.md §2)."""

    __tablename__ = "agent_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True, index=True)
    invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id"), nullable=True, index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    step: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    level: Mapped[str] = mapped_column(
        String(8), default=AgentEventLevel.INFO.value, nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Redacted via utils.redact before insert — never raw secrets or PDF bytes.
    inputs_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    outputs_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
