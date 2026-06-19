from __future__ import annotations

from billtobox_agent.utils import redact, sha256_hex


def test_secret_keys_masked() -> None:
    out = redact({"api_key": "sk-secret", "client_secret": "x", "vendor": "KPN"})
    assert out["api_key"] == "***"
    assert out["client_secret"] == "***"
    assert out["vendor"] == "KPN"


def test_bytes_replaced_with_hash_and_length() -> None:
    payload = b"%PDF-1.7 hello"
    out = redact({"pdf_bytes": payload})
    assert out["pdf_bytes"]["__bytes__"]["len"] == len(payload)
    assert out["pdf_bytes"]["__bytes__"]["sha256"] == sha256_hex(payload)
    assert "%PDF" not in str(out)


def test_nested_and_list_redaction() -> None:
    out = redact({"items": [{"token": "abc", "ok": 1}], "nested": {"password": "p"}})
    assert out["items"][0]["token"] == "***"
    assert out["items"][0]["ok"] == 1
    assert out["nested"]["password"] == "***"


def test_long_strings_truncated() -> None:
    out = redact({"body": "x" * 5000})
    assert "truncated" in out["body"]
    assert len(out["body"]) < 5000


def test_scalars_pass_through() -> None:
    assert redact(42) == 42
    assert redact(None) is None
    assert redact("short") == "short"
