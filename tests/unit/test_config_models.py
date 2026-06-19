from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from billtobox_agent.config.models import AppConfig, QuarterDateField, Source


def _minimal_dict() -> dict[str, Any]:
    return {
        "anthropic": {"api_key": "test-anthropic-key"},
        "google": {"client_id": "g-id", "client_secret": "g-secret"},
        "microsoft": {"client_id": "m-id", "client_secret": "m-secret"},
        "billtobox": {
            "mailbox_address": "upload@mailbox.billtobox.com",
            "sender_address": "invoices@example.com",
        },
        "smtp": {
            "host": "smtp.example.com",
            "username": "invoices@example.com",
            "password": "smtp-secret",
        },
    }


def test_defaults_applied() -> None:
    cfg = AppConfig.model_validate(_minimal_dict())
    assert cfg.extraction.confidence_threshold == 0.85
    assert cfg.accounting.fiscal_year_start_month == 1
    assert cfg.accounting.quarter_date_field is QuarterDateField.INVOICE_DATE
    assert cfg.accounting.fy_label_prefix == ""
    assert cfg.sources.polling == (Source.GMAIL, Source.OUTLOOK)
    assert cfg.web.host == "127.0.0.1"
    assert cfg.web.port == 9003
    assert cfg.microsoft.tenant_id == "common"
    assert cfg.storage.sqlite_path == Path("data/billtobox.db")
    assert cfg.google.token_path == Path("data/google_token.json")
    assert cfg.drive.root_folder_id is None
    assert cfg.drive.root_folder_name == "Invoices"


def test_secrets_are_redacted() -> None:
    cfg = AppConfig.model_validate(_minimal_dict())
    assert cfg.anthropic.api_key.get_secret_value() == "test-anthropic-key"
    assert str(cfg.anthropic.api_key) == "**********"
    assert "test-anthropic-key" not in repr(cfg)
    assert "smtp-secret" not in repr(cfg)


def test_missing_required_section_rejected() -> None:
    data = _minimal_dict()
    del data["anthropic"]
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_extra_key_forbidden() -> None:
    data = _minimal_dict()
    data["unexpected"] = 1
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_confidence_out_of_range_rejected() -> None:
    data = _minimal_dict()
    data["extraction"] = {"confidence_threshold": 1.5}
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_fiscal_month_out_of_range_rejected() -> None:
    data = _minimal_dict()
    data["accounting"] = {"fiscal_year_start_month": 13}
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_invalid_email_rejected() -> None:
    data = _minimal_dict()
    data["billtobox"]["mailbox_address"] = "not-an-email"
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_empty_polling_rejected() -> None:
    data = _minimal_dict()
    data["sources"] = {"polling": []}
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_unknown_source_rejected() -> None:
    data = _minimal_dict()
    data["sources"] = {"polling": ["gmail", "fax"]}
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_frozen_config_is_immutable() -> None:
    cfg = AppConfig.model_validate(_minimal_dict())
    with pytest.raises(ValidationError):
        cfg.web.port = 1234
