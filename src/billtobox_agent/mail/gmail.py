"""Gmail read-only connector.

Searches for candidate invoice messages, then walks ``payload.parts`` to download
PDF attachments. The Google API client is injected (``GmailConnector(service)``),
so tests pass a fake; production builds it via :meth:`GmailConnector.from_config`.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from googleapiclient.discovery import build

from billtobox_agent.config.models import GoogleConfig
from billtobox_agent.mail.base import FetchedPdf, MailMessageRef
from billtobox_agent.mail.google_auth import load_credentials
from billtobox_agent.mail.render import render_message_pdf

_KEYWORDS = "(invoice OR factuur OR rekening OR BTW)"
# Strict: only mail that already carries a PDF attachment.
GMAIL_QUERY = f"has:attachment filename:pdf {_KEYWORDS}"
# Broadened: invoice-keyword mail regardless of attachment, so body-only invoices
# (no PDF attached) surface too. They are rendered to a PDF in ``download_pdfs``.
GMAIL_QUERY_INCLUDING_BODYLESS = _KEYWORDS


class GmailConnector:
    source = "gmail"

    def __init__(self, service: Any, *, render_bodyless: bool = True) -> None:
        self._service = service
        self._render_bodyless = render_bodyless

    @classmethod
    def from_config(cls, config: GoogleConfig, *, render_bodyless: bool = True) -> GmailConnector:
        credentials = load_credentials(config)
        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        return cls(service, render_bodyless=render_bodyless)

    def search(self, since: datetime | None = None) -> list[MailMessageRef]:
        query = GMAIL_QUERY_INCLUDING_BODYLESS if self._render_bodyless else GMAIL_QUERY
        if since is not None:
            # Gmail's after: accepts an epoch-second timestamp (second-granular).
            query = f"{query} after:{int(since.timestamp())}"
        refs = [self._message_ref(message_id) for message_id in self._list_message_ids(query)]
        refs.sort(key=lambda ref: ref.received_at)
        return refs

    def download_pdfs(self, ref: MailMessageRef) -> list[FetchedPdf]:
        message = (
            self._service.users()
            .messages()
            .get(userId="me", id=ref.message_id, format="full")
            .execute()
        )
        payload = message.get("payload", {})
        results: list[FetchedPdf] = []
        for filename, attachment_id in _iter_pdf_parts(payload):
            attachment = (
                self._service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=ref.message_id, id=attachment_id)
                .execute()
            )
            pdf_bytes = base64.urlsafe_b64decode(attachment["data"])
            results.append(FetchedPdf(message=ref, filename=filename, pdf_bytes=pdf_bytes))

        # No attached PDF — fall back to rendering the message body (body-only invoice).
        if not results and self._render_bodyless:
            body, is_html = _extract_body(payload)
            if body:
                results.append(render_message_pdf(ref, body=body, is_html=is_html))
        return results

    def _list_message_ids(self, query: str) -> list[str]:
        ids: list[str] = []
        page_token: str | None = None
        messages = self._service.users().messages()
        while True:
            kwargs: dict[str, Any] = {"userId": "me", "q": query}
            if page_token:
                kwargs["pageToken"] = page_token
            response = messages.list(**kwargs).execute()
            ids.extend(item["id"] for item in response.get("messages", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return ids

    def _message_ref(self, message_id: str) -> MailMessageRef:
        message = (
            self._service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=["Subject", "From"],
            )
            .execute()
        )
        headers = {
            header["name"].lower(): header["value"]
            for header in message.get("payload", {}).get("headers", [])
        }
        received_at = datetime.fromtimestamp(int(message["internalDate"]) / 1000, tz=UTC)
        return MailMessageRef(
            source=self.source,
            message_id=message_id,
            subject=headers.get("subject", ""),
            sender=headers.get("from", ""),
            received_at=received_at,
        )


def _iter_pdf_parts(part: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """Yield ``(filename, attachment_id)`` for every PDF part, recursively."""
    filename = part.get("filename", "") or ""
    mime_type = part.get("mimeType", "")
    attachment_id = part.get("body", {}).get("attachmentId")
    if attachment_id and (mime_type == "application/pdf" or filename.lower().endswith(".pdf")):
        yield filename, attachment_id
    for sub_part in part.get("parts", []):
        yield from _iter_pdf_parts(sub_part)


def _extract_body(payload: dict[str, Any]) -> tuple[str, bool]:
    """Return ``(text, is_html)`` for the message body, preferring HTML over plain.

    Walks the MIME tree for inline ``text/html`` (richest) then ``text/plain``;
    returns ``("", False)`` when neither carries inline data.
    """
    bodies: dict[str, str] = {}
    for mime_type, data in _iter_text_parts(payload):
        bodies.setdefault(mime_type, data)  # keep the first part of each type
    if "text/html" in bodies:
        return bodies["text/html"], True
    if "text/plain" in bodies:
        return bodies["text/plain"], False
    return "", False


def _iter_text_parts(part: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """Yield ``(mime_type, decoded_text)`` for inline text parts, recursively."""
    mime_type = part.get("mimeType", "")
    body = part.get("body", {})
    data = body.get("data")
    # Inline text only: a part with an attachmentId is a download, not the body.
    if data and not body.get("attachmentId") and mime_type in ("text/plain", "text/html"):
        yield mime_type, base64.urlsafe_b64decode(data).decode("utf-8", "replace")
    for sub_part in part.get("parts", []):
        yield from _iter_text_parts(sub_part)
