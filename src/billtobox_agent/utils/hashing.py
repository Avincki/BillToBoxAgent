"""Content hashing helpers."""

from __future__ import annotations

import hashlib


def sha256_hex(data: bytes) -> str:
    """Hex SHA-256 digest of raw bytes — the content hash used for dedup."""
    return hashlib.sha256(data).hexdigest()
