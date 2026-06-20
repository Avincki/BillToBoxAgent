from __future__ import annotations

from datetime import UTC, datetime

from billtobox_agent.mail.base import MailMessageRef
from billtobox_agent.mail.prefilter import PDF_MAGIC
from billtobox_agent.mail.render import (
    html_to_text,
    render_email_to_pdf,
    render_message_pdf,
    rendered_filename,
)


def _ref() -> MailMessageRef:
    return MailMessageRef(
        source="gmail",
        message_id="m1",
        subject="Uw factuur",
        sender="KPN <billing@kpn.be>",
        received_at=datetime(2026, 5, 1, 12, 30, tzinfo=UTC),
    )


def test_html_to_text_strips_tags_and_skips_script_style() -> None:
    html = (
        "<html><head><style>td{color:red}</style></head>"
        "<body><p>Factuur 123</p><table><tr><td>Bedrag</td><td>149,95</td></tr></table>"
        "<script>track()</script></body></html>"
    )
    text = html_to_text(html)

    assert "Factuur 123" in text
    assert "Bedrag" in text and "149,95" in text
    assert "track()" not in text  # script body dropped
    assert "color:red" not in text  # style body dropped
    assert "<" not in text  # no raw tags


def test_render_email_to_pdf_returns_valid_pdf_bytes() -> None:
    pdf = render_email_to_pdf(
        subject="Uw factuur - mei",
        sender="billing@kpn.be",
        received_at=datetime(2026, 5, 1, tzinfo=UTC),
        body="<p>Bedrag: 149,95 €</p>",  # euro sign exercises the substitution table
        is_html=True,
    )

    assert pdf.startswith(PDF_MAGIC)
    assert len(pdf) > 0


def test_render_email_to_pdf_handles_empty_body() -> None:
    pdf = render_email_to_pdf(
        subject="x",
        sender="a@b.c",
        received_at=datetime(2026, 5, 1, tzinfo=UTC),
        body="",
        is_html=False,
    )

    assert pdf.startswith(PDF_MAGIC)


def test_render_message_pdf_preserves_provenance_and_names_file() -> None:
    fetched = render_message_pdf(_ref(), body="Bedrag 10 EUR", is_html=False)

    assert fetched.message.message_id == "m1"
    assert fetched.filename == rendered_filename(_ref()) == "gmail-email-20260501.pdf"
    assert fetched.pdf_bytes.startswith(PDF_MAGIC)
