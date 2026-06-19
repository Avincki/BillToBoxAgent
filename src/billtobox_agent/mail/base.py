"""Shared types for mail connectors (Gmail, Outlook, Doccle).

Every connector returns the same shapes so the worker treats sources uniformly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MailMessageRef:
    """A candidate message: provenance + the fields the pre-filter needs."""

    source: str
    message_id: str
    subject: str
    sender: str
    received_at: datetime


@dataclass(frozen=True, slots=True)
class FetchedPdf:
    """A single PDF attachment plus the message it came from (provenance)."""

    message: MailMessageRef
    filename: str
    pdf_bytes: bytes


class MailConnector(Protocol):
    """Structural interface implemented by every source connector."""

    source: str

    def search(self, since: datetime | None) -> list[MailMessageRef]:
        """List candidate messages, optionally only those newer than ``since``."""
        ...

    def download_pdfs(self, ref: MailMessageRef) -> list[FetchedPdf]:
        """Download every PDF attachment of ``ref``."""
        ...
