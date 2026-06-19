"""Cheap, model-free pre-filter applied before any Claude extraction (task 11).

Each candidate PDF is gated on three signals that cost nothing but a few string
operations, so junk never reaches the (paid, slower) model call:

1. **PDF validity** — the bytes must start with the ``%PDF-`` magic marker.
2. **Sender domain** — a configurable allow/blocklist. The *blocklist* always
   rejects (it wins over the allowlist); the *allowlist* always accepts. Domains
   match themselves and any subdomain (``kpn.com`` matches ``mail.kpn.com``).
3. **Subject keywords** — senders that are neither allow- nor blocklisted must
   carry one of the configured keywords (``invoice``, ``factuur``, …) in the
   subject. This keeps unknown vendors flowing through while still gating noise.

The allowlist is deliberately a *trust/bypass* (not a hard whitelist): the owner
receives invoices from vendors they cannot enumerate in advance, so an unknown
sender with an invoice-like subject must still pass.
"""

from __future__ import annotations

from email.utils import parseaddr

from billtobox_agent.config.models import PrefilterConfig
from billtobox_agent.mail.base import MailMessageRef

PDF_MAGIC = b"%PDF-"


def prefilter(ref: MailMessageRef, pdf_bytes: bytes, config: PrefilterConfig) -> bool:
    """Return ``True`` if the candidate is worth extracting, ``False`` to drop it."""
    if not pdf_bytes.startswith(PDF_MAGIC):
        return False

    domain = _sender_domain(ref.sender)
    if _domain_matches(domain, config.sender_blocklist):
        return False  # blocklist wins over everything else
    if _domain_matches(domain, config.sender_allowlist):
        return True  # trusted sender — accept regardless of subject

    return _subject_has_keyword(ref.subject, config.subject_keywords)


def _sender_domain(sender: str) -> str:
    """Extract the lowercased domain from a ``From`` header value.

    Handles both bare addresses (``billing@kpn.com``) and display-name forms
    (``KPN Billing <billing@kpn.com>``); returns ``""`` when no domain is present.
    """
    _name, address = parseaddr(sender)
    _, _, domain = address.rpartition("@")
    return domain.strip().lower()


def _domain_matches(domain: str, patterns: tuple[str, ...]) -> bool:
    """True if ``domain`` equals or is a subdomain of any configured pattern."""
    if not domain:
        return False
    for pattern in patterns:
        normalized = pattern.strip().lower().lstrip("@").lstrip(".")
        if normalized and (domain == normalized or domain.endswith(f".{normalized}")):
            return True
    return False


def _subject_has_keyword(subject: str, keywords: tuple[str, ...]) -> bool:
    """True if any keyword appears in the subject (case-insensitive substring)."""
    haystack = subject.lower()
    return any(keyword.lower() in haystack for keyword in keywords if keyword)
