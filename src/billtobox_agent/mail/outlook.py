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

_FILE_ATTACHMENT = "#microsoft.graph.fileAttachment"


def _graph_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class OutlookConnector:
    source = "outlook"

    def __init__(self, client: GraphHttp) -> None:
        self._client = client

    @classmethod
    def from_config(cls, config: MicrosoftConfig) -> OutlookConnector:
        return cls(GraphClient(token_provider=lambda: acquire_token(config)))

    def search(self, since: datetime | None = None) -> list[MailMessageRef]:
        filter_clause = "hasAttachments eq true"
        if since is not None:
            filter_clause = f"{filter_clause} and receivedDateTime gt {_graph_iso(since)}"
        params: dict[str, Any] | None = {
            "$filter": filter_clause,
            "$select": "id,subject,from,receivedDateTime",
            "$orderby": "receivedDateTime asc",
            "$top": "50",
        }
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
        return results

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
