from __future__ import annotations

from datetime import UTC, datetime

from billtobox_agent.config.models import PrefilterConfig
from billtobox_agent.mail import MailMessageRef, prefilter

_PDF = b"%PDF-1.7 ...bytes..."
_NOT_PDF = b"PK\x03\x04 this is a zip"


def _ref(subject: str = "Invoice 42", sender: str = "billing@kpn.com") -> MailMessageRef:
    return MailMessageRef(
        source="gmail",
        message_id="m1",
        subject=subject,
        sender=sender,
        received_at=datetime(2026, 5, 1, tzinfo=UTC),
    )


# ----- PDF magic-byte validity ------------------------------------------------


def test_accepts_valid_pdf_with_keyword_subject() -> None:
    assert prefilter(_ref(), _PDF, PrefilterConfig()) is True


def test_rejects_non_pdf_bytes() -> None:
    assert prefilter(_ref(), _NOT_PDF, PrefilterConfig()) is False


def test_rejects_empty_bytes() -> None:
    assert prefilter(_ref(), b"", PrefilterConfig()) is False


# ----- subject keyword gating (default keywords) ------------------------------


def test_rejects_when_subject_has_no_keyword() -> None:
    assert prefilter(_ref(subject="Your weekly newsletter"), _PDF, PrefilterConfig()) is False


def test_subject_keyword_is_case_insensitive() -> None:
    assert prefilter(_ref(subject="FACTUUR mei 2026"), _PDF, PrefilterConfig()) is True


def test_keyword_matches_as_substring() -> None:
    # "btw" appears inside the Dutch subject word; still a valid match.
    assert prefilter(_ref(subject="BTW-aangifte Q2"), _PDF, PrefilterConfig()) is True


def test_subject_keywords_are_config_driven() -> None:
    config = PrefilterConfig(subject_keywords=("bill",))
    assert prefilter(_ref(subject="Monthly bill"), _PDF, config) is True
    assert prefilter(_ref(subject="Invoice 42"), _PDF, config) is False  # default kw no longer set


# ----- sender allowlist (trust/bypass) ----------------------------------------


def test_allowlisted_sender_passes_without_keyword() -> None:
    config = PrefilterConfig(sender_allowlist=("kpn.com",))
    assert prefilter(_ref(subject="no keyword here"), _PDF, config) is True


def test_allowlist_matches_subdomain() -> None:
    config = PrefilterConfig(sender_allowlist=("kpn.com",))
    ref = _ref(subject="no keyword", sender="billing@mail.kpn.com")
    assert prefilter(ref, _PDF, config) is True


def test_allowlist_handles_display_name_sender() -> None:
    config = PrefilterConfig(sender_allowlist=("kpn.com",))
    ref = _ref(subject="no keyword", sender="KPN Billing <billing@kpn.com>")
    assert prefilter(ref, _PDF, config) is True


def test_allowlist_does_not_match_unrelated_domain() -> None:
    config = PrefilterConfig(sender_allowlist=("kpn.com",))
    # "evil-kpn.com" must NOT match "kpn.com" (suffix match is dot-anchored).
    ref = _ref(subject="no keyword", sender="billing@evil-kpn.com")
    assert prefilter(ref, _PDF, config) is False


# ----- sender blocklist (hard reject, wins over allowlist) --------------------


def test_blocklisted_sender_rejected_despite_keyword() -> None:
    config = PrefilterConfig(sender_blocklist=("spam.example",))
    ref = _ref(subject="Invoice 42", sender="noreply@spam.example")
    assert prefilter(ref, _PDF, config) is False


def test_blocklist_wins_over_allowlist() -> None:
    config = PrefilterConfig(
        sender_allowlist=("kpn.com",),
        sender_blocklist=("kpn.com",),
    )
    assert prefilter(_ref(), _PDF, config) is False


def test_blocklist_matches_subdomain() -> None:
    config = PrefilterConfig(sender_blocklist=("example.com",))
    ref = _ref(subject="Invoice 42", sender="noreply@news.example.com")
    assert prefilter(ref, _PDF, config) is False


# ----- edge cases -------------------------------------------------------------


def test_empty_lists_fall_through_to_keyword_check() -> None:
    config = PrefilterConfig(sender_allowlist=(), sender_blocklist=())
    assert prefilter(_ref(subject="Invoice 42"), _PDF, config) is True
    assert prefilter(_ref(subject="hello"), _PDF, config) is False


def test_unparseable_sender_relies_on_keyword() -> None:
    # No domain to match against either list; the keyword carries it.
    ref = _ref(subject="Invoice 42", sender="not-an-address")
    assert prefilter(ref, _PDF, PrefilterConfig(sender_allowlist=("kpn.com",))) is True
    ref = _ref(subject="hello", sender="not-an-address")
    assert prefilter(ref, _PDF, PrefilterConfig(sender_allowlist=("kpn.com",))) is False
