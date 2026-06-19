"""Doccle connector — STUB.

The Doccle API is unconfirmed (CLAUDE.md "Doccle"): partner REST/GraphQL with OAuth 2.0
and a developer account, but the docs render client-side and couldn't be read. Every
method raises :class:`NotImplementedError` until the user provides the OpenAPI spec and
credentials. The worker only builds connectors for sources in ``sources.polling`` (default
gmail, outlook), so this stub is never invoked by default.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from billtobox_agent.mail.base import FetchedPdf, MailMessageRef

_NOT_IMPLEMENTED = "Doccle connector is not implemented yet (awaiting API spec + credentials)"


class DoccleConnector:
    source = "doccle"

    @classmethod
    def from_config(cls, config: Any) -> DoccleConnector:
        # TODO: confirm Doccle API
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def search(self, since: datetime | None = None) -> list[MailMessageRef]:
        # TODO: confirm Doccle API
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def download_pdfs(self, ref: MailMessageRef) -> list[FetchedPdf]:
        # TODO: confirm Doccle API
        raise NotImplementedError(_NOT_IMPLEMENTED)
