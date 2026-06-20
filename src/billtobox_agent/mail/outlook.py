"""Outlook / Microsoft 365 read-only connector (Microsoft Graph).

Lists messages with attachments since the watermark and downloads PDF
``fileAttachment`` bytes. Returns the same shapes as the Gmail connector so the
worker treats both uniformly. Keyword narrowing is left to the shared pre-filter
(task 11), keeping one keyword policy across sources.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

from billtobox_agent.config.models import MicrosoftConfig
from billtobox_agent.mail.base import FetchedPdf, MailMessageRef
from billtobox_agent.mail.graph import GraphClient, GraphHttp
from billtobox_agent.mail.ms_auth import acquire_token
from billtobox_agent.mail.render import render_message_pdf

_FILE_ATTACHMENT = "#microsoft.graph.fileAttachment"


def _graph_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class OutlookConnector:
    source = "outlook"

    def __init__(self, client: GraphHttp, *, render_bodyless: bool = True) -> None:
        self._client = client
        self._render_bodyless = render_bodyless

    @classmethod
    def from_config(
        cls, config: MicrosoftConfig, *, render_bodyless: bool = True
    ) -> OutlookConnector:
        return cls(
            GraphClient(token_provider=lambda: acquire_token(config)),
            render_bodyless=render_bodyless,
        )

    def search(self, since: datetime | None = None) -> list[MailMessageRef]:
        clauses: list[str] = []
        # Strict mode requires an attachment; bodyless mode drops it so body-only
        # invoices surface (they are rendered to a PDF in ``download_pdfs``).
        if not self._render_bodyless:
            clauses.append("hasAttachments eq true")
        if since is not None:
            clauses.append(f"receivedDateTime gt {_graph_iso(since)}")
        query: dict[str, Any] = {
            "$select": "id,subject,from,receivedDateTime",
            "$orderby": "receivedDateTime asc",
            "$top": "50",
        }
        if clauses:
            query["$filter"] = " and ".join(clauses)
        params: dict[str, Any] | None = query
        path = "/me/messages"
        refs: list[MailMessageRef] = []
        while path:
            response = self._client.get(path, params=params)
            refs.extend(self._to_ref(item) for item in response.get("value", []))
            path = response.get("@odata.nextLink", "")
            params = None
        refs.sort(key=lambda ref: ref.received_at)
        return refs

    def download_pdfs(self, ref: MailMessageRef) -> list[FetchedPdf]:
        response = self._client.get(f"/me/messages/{ref.message_id}/attachments")
        results: list[FetchedPdf] = []
        for attachment in response.get("value", []):
            if attachment.get("@odata.type") != _FILE_ATTACHMENT:
                continue
            name = attachment.get("name", "") or ""
            content_type = attachment.get("contentType", "")
            content_b64 = attachment.get("contentBytes")
            is_pdf = content_type == "application/pdf" or name.lower().endswith(".pdf")
            if not content_b64 or not is_pdf:
                continue
            results.append(
                FetchedPdf(message=ref, filename=name, pdf_bytes=base64.b64decode(content_b64))
            )

        # No attached PDF — fall back to rendering the message body (body-only invoice).
        if not results and self._render_bodyless:
            body, is_html = self._fetch_body(ref.message_id)
            if body:
                results.append(render_message_pdf(ref, body=body, is_html=is_html))
        return results

    def _fetch_body(self, message_id: str) -> tuple[str, bool]:
        """Return ``(content, is_html)`` for the message body, or ``("", False)``."""
        message = self._client.get(f"/me/messages/{message_id}", params={"$select": "body"})
        body = message.get("body", {})
        content = body.get("content", "") or ""
        return content, body.get("contentType", "").lower() == "html"

    def _to_ref(self, item: dict[str, Any]) -> MailMessageRef:
        received_at = datetime.fromisoformat(item["receivedDateTime"].replace("Z", "+00:00"))
        sender = item.get("from", {}).get("emailAddress", {}).get("address", "")
        return MailMessageRef(
            source=self.source,
            message_id=item["id"],
            subject=item.get("subject", "") or "",
            sender=sender,
            received_at=received_at,
        )
