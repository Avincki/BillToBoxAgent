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

GMAIL_QUERY = "has:attachment filename:pdf (invoice OR factuur OR rekening OR BTW)"


class GmailConnector:
    source = "gmail"

    def __init__(self, service: Any) -> None:
        self._service = service

    @classmethod
    def from_config(cls, config: GoogleConfig) -> GmailConnector:
        credentials = load_credentials(config)
        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        return cls(service)

    def search(self, since: datetime | None = None) -> list[MailMessageRef]:
        query = GMAIL_QUERY
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
        results: list[FetchedPdf] = []
        for filename, attachment_id in _iter_pdf_parts(message.get("payload", {})):
            attachment = (
                self._service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=ref.message_id, id=attachment_id)
                .execute()
            )
            pdf_bytes = base64.urlsafe_b64decode(attachment["data"])
            results.append(FetchedPdf(message=ref, filename=filename, pdf_bytes=pdf_bytes))
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
