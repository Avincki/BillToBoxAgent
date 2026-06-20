"""Billtobox email send over SMTP (task 20).

The final, money-adjacent step: email a stored invoice PDF to the Billtobox
mailbox. It is gated hard so it can never send without human approval and never
send twice:

* the invoice **must** be ``upload_approved`` (the human-approval flag set by
  ``queue_billtobox_upload`` / the dashboard), and
* ``uploaded_at`` **must** be NULL.

On a confirmed send it sets ``status="uploaded"`` + ``uploaded_at`` and writes an
``agent_events`` row (the PDF bytes are redacted, never logged). The SMTP
transport is injected (``SmtpTransport`` in production; a fake in tests).
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from typing import Protocol

from billtobox_agent.config.models import BilltoboxConfig, SmtpConfig
from billtobox_agent.data import UnitOfWork
from billtobox_agent.data.models import AgentEventType, InvoiceStatus
from billtobox_agent.drive import DriveConnector


class BilltoboxSendError(Exception):
    """Raised when a Billtobox send is not allowed (guard violated) or fails."""


class MailTransport(Protocol):
    """The send surface email_to_billtobox needs (so tests can inject a fake)."""

    def send(self, message: EmailMessage) -> None: ...


class SmtpTransport:
    """Production transport: STARTTLS + app-password SMTP from :class:`SmtpConfig`."""

    def __init__(self, config: SmtpConfig) -> None:
        self._config = config

    def send(self, message: EmailMessage) -> None:
        config = self._config
        with smtplib.SMTP(config.host, config.port) as smtp:
            if config.use_tls:
                smtp.starttls()
            smtp.login(config.username, config.password.get_secret_value())
            smtp.send_message(message)


def _build_message(billtobox: BilltoboxConfig, filename: str, pdf_bytes: bytes) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = filename
    message["From"] = str(billtobox.sender_address)
    message["To"] = str(billtobox.mailbox_address)
    message.set_content(f"Invoice attached: {filename}")
    message.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=filename)
    return message


async def email_to_billtobox(
    uow: UnitOfWork,
    drive: DriveConnector,
    invoice_id: int,
    *,
    billtobox: BilltoboxConfig,
    transport: MailTransport,
    run_id: int | None = None,
    step: int = 0,
) -> str:
    """Email the invoice's stored PDF to Billtobox. Returns the attachment filename.

    Raises :class:`BilltoboxSendError` if the invoice is not ``upload_approved``,
    has already been uploaded, or has no stored PDF — so a re-invocation sends
    nothing.
    """
    invoice = await uow.invoices.get(invoice_id)
    if invoice is None:
        raise BilltoboxSendError(f"invoice {invoice_id} not found")
    if invoice.status != InvoiceStatus.UPLOAD_APPROVED.value:
        raise BilltoboxSendError(
            f"invoice {invoice_id} is not upload_approved (status={invoice.status!r})"
        )
    if invoice.uploaded_at is not None:
        raise BilltoboxSendError(f"invoice {invoice_id} was already uploaded")
    if not invoice.drive_file_id:
        raise BilltoboxSendError(f"invoice {invoice_id} has no stored PDF to attach")

    filename = (
        invoice.drive_path.rsplit("/", 1)[-1] if invoice.drive_path else f"invoice_{invoice_id}.pdf"
    )
    drive_file_id = invoice.drive_file_id

    await uow.agent_events.add(
        event_type=AgentEventType.TOOL_CALL,
        summary=f"email_to_billtobox {filename}",
        run_id=run_id,
        invoice_id=invoice_id,
        step=step,
        tool="email_to_billtobox",
        inputs={"filename": filename, "to": str(billtobox.mailbox_address)},
    )

    pdf_bytes = await asyncio.to_thread(drive.download_pdf, drive_file_id)
    message = _build_message(billtobox, filename, pdf_bytes)
    await asyncio.to_thread(transport.send, message)

    await uow.invoices.mark_uploaded(invoice_id)
    await uow.agent_events.add(
        event_type=AgentEventType.TOOL_RESULT,
        summary=f"email_to_billtobox sent {filename}",
        run_id=run_id,
        invoice_id=invoice_id,
        step=step,
        tool="email_to_billtobox",
        outputs={"filename": filename, "status": InvoiceStatus.UPLOADED.value},
    )
    return filename
