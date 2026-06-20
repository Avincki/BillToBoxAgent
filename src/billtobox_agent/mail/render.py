"""Render an invoice email that has *no PDF attachment* into a PDF.

Some vendors (and most Doccle-style notifications) put the invoice in the message
body itself — an HTML table or plain text — with no attached PDF. To keep one
currency flowing through the pipeline (everything downstream consumes
:class:`~billtobox_agent.mail.base.FetchedPdf`), the connectors fall back to this
module: it lays out the message metadata (From / Subject / Date) plus the body
text onto an A4 PDF and returns the bytes, which then go through the same
pre-filter → hash → Claude extraction → Drive → Billtobox path as a real
attachment.

The renderer is pure-Python (``fpdf2``) on purpose: no Cairo/Pango/wkhtmltopdf
system libraries to install on the Raspberry Pi (or in CI). HTML bodies are
flattened to text rather than faithfully laid out — the goal is a readable,
extractable archive of the invoice, not a pixel-perfect reproduction.
"""

from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
from io import StringIO

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from billtobox_agent.mail.base import FetchedPdf, MailMessageRef

# Core PDF fonts are Latin-1 only; map the symbols that actually show up in
# (mostly Dutch/French) invoice mail so amounts stay legible, then drop the rest.
_LATIN1_SUBSTITUTIONS = {
    "€": "EUR ",  # euro sign is not in Latin-1
    "‘": "'",  # noqa: RUF001
    "’": "'",  # noqa: RUF001
    "“": '"',
    "”": '"',
    "–": "-",  # noqa: RUF001
    "—": "-",
    "…": "...",
    " ": " ",  # non-breaking space  # noqa: RUF001
    "•": "-",
}

# Block-level tags after which we force a line break when flattening HTML.
_HTML_BREAK_TAGS = frozenset(
    {"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"}
)
_HTML_SKIP_TAGS = frozenset({"script", "style", "head", "title"})


def render_email_to_pdf(
    *,
    subject: str,
    sender: str,
    received_at: datetime,
    body: str,
    is_html: bool,
) -> bytes:
    """Render an email's metadata + body to PDF bytes (always starts with ``%PDF-``)."""
    text = html_to_text(body) if is_html else body

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    for label, value in (
        ("From", sender),
        ("Subject", subject),
        ("Date", received_at.strftime("%Y-%m-%d %H:%M %Z").strip()),
    ):
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", style="B", size=10)
        pdf.cell(18, 6, f"{label}:")
        pdf.set_font("Helvetica", style="", size=10)
        pdf.multi_cell(0, 6, _to_latin1(value), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(3)
    pdf.set_draw_color(180, 180, 180)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)

    pdf.set_font("Helvetica", style="", size=11)
    pdf.multi_cell(0, 6, _to_latin1(text) or "(empty message body)")

    return bytes(pdf.output())


def render_message_pdf(ref: MailMessageRef, *, body: str, is_html: bool) -> FetchedPdf:
    """Render ``ref``'s body to a :class:`FetchedPdf` (provenance preserved)."""
    pdf_bytes = render_email_to_pdf(
        subject=ref.subject,
        sender=ref.sender,
        received_at=ref.received_at,
        body=body,
        is_html=is_html,
    )
    return FetchedPdf(message=ref, filename=rendered_filename(ref), pdf_bytes=pdf_bytes)


def rendered_filename(ref: MailMessageRef) -> str:
    """A stable, filesystem-safe name for a body-rendered invoice PDF."""
    return f"{ref.source}-email-{ref.received_at:%Y%m%d}.pdf"


class _TextExtractor(HTMLParser):
    """Collect visible text from HTML, inserting newlines on block boundaries."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out = StringIO()
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in _HTML_SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _HTML_BREAK_TAGS:
            self._out.write("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _HTML_SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _HTML_BREAK_TAGS:
            self._out.write("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._out.write(data)

    def text(self) -> str:
        return self._out.getvalue()


def html_to_text(html: str) -> str:
    """Flatten an HTML body to readable plain text (tags stripped, blocks newlined)."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    lines = [line.strip() for line in parser.text().splitlines()]
    # Collapse runs of blank lines that the block-break logic produces.
    cleaned: list[str] = []
    for line in lines:
        if line or (cleaned and cleaned[-1]):
            cleaned.append(line)
    return "\n".join(cleaned).strip()


def _to_latin1(text: str) -> str:
    """Make ``text`` safe for the Latin-1 core PDF fonts (substitute, then drop)."""
    for needle, replacement in _LATIN1_SUBSTITUTIONS.items():
        text = text.replace(needle, replacement)
    return text.encode("latin-1", "replace").decode("latin-1")
