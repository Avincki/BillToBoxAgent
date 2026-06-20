"""Mail source connectors: Gmail, Outlook (Graph), and the Doccle stub (tasks 8-11)."""

from billtobox_agent.mail.base import FetchedPdf, MailConnector, MailMessageRef
from billtobox_agent.mail.doccle import DoccleConnector
from billtobox_agent.mail.fetch import fetch_new_pdfs
from billtobox_agent.mail.gmail import (
    GMAIL_QUERY,
    GMAIL_QUERY_INCLUDING_BODYLESS,
    GmailConnector,
)
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
from billtobox_agent.mail.render import (
    html_to_text,
    render_email_to_pdf,
    render_message_pdf,
    rendered_filename,
)

__all__ = [
    "GMAIL_QUERY",
    "GMAIL_QUERY_INCLUDING_BODYLESS",
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
    "html_to_text",
    "load_credentials",
    "prefilter",
    "render_email_to_pdf",
    "render_message_pdf",
    "rendered_filename",
    "run_consent_flow",
    "run_device_flow",
    "save_credentials",
]
