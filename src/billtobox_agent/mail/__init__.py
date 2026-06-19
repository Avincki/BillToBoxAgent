"""Mail source connectors: Gmail, Outlook (Graph), and the Doccle stub (tasks 8-11)."""

from billtobox_agent.mail.base import FetchedPdf, MailConnector, MailMessageRef
from billtobox_agent.mail.doccle import DoccleConnector
from billtobox_agent.mail.fetch import fetch_new_pdfs
from billtobox_agent.mail.gmail import GMAIL_QUERY, GmailConnector
from billtobox_agent.mail.google_auth import (
    GOOGLE_SCOPES,
    GoogleAuthError,
    load_credentials,
    run_consent_flow,
    save_credentials,
)
from billtobox_agent.mail.graph import GraphClient, GraphError, GraphHttp
from billtobox_agent.mail.ms_auth import (
    MS_SCOPES,
    MicrosoftAuthError,
    acquire_token,
    run_device_flow,
)
from billtobox_agent.mail.outlook import OutlookConnector
from billtobox_agent.mail.prefilter import PDF_MAGIC, prefilter

__all__ = [
    "GMAIL_QUERY",
    "GOOGLE_SCOPES",
    "MS_SCOPES",
    "PDF_MAGIC",
    "DoccleConnector",
    "FetchedPdf",
    "GmailConnector",
    "GoogleAuthError",
    "GraphClient",
    "GraphError",
    "GraphHttp",
    "MailConnector",
    "MailMessageRef",
    "MicrosoftAuthError",
    "OutlookConnector",
    "acquire_token",
    "fetch_new_pdfs",
    "load_credentials",
    "prefilter",
    "run_consent_flow",
    "run_device_flow",
    "save_credentials",
]
