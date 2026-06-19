"""Mail source connectors: Gmail, Outlook (Graph), and the Doccle stub (tasks 8-11)."""

from billtobox_agent.mail.base import FetchedPdf, MailConnector, MailMessageRef
from billtobox_agent.mail.fetch import fetch_new_pdfs
from billtobox_agent.mail.gmail import GMAIL_QUERY, GmailConnector
from billtobox_agent.mail.google_auth import (
    GOOGLE_SCOPES,
    GoogleAuthError,
    load_credentials,
    run_consent_flow,
    save_credentials,
)

__all__ = [
    "GMAIL_QUERY",
    "GOOGLE_SCOPES",
    "FetchedPdf",
    "GmailConnector",
    "GoogleAuthError",
    "MailConnector",
    "MailMessageRef",
    "fetch_new_pdfs",
    "load_credentials",
    "run_consent_flow",
    "save_credentials",
]
