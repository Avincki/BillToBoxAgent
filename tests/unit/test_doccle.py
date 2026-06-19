from __future__ import annotations

import importlib
from datetime import UTC, datetime

import pytest

from billtobox_agent.mail import DoccleConnector, MailMessageRef


def test_module_importable_at_expected_path() -> None:
    module = importlib.import_module("billtobox_agent.mail.doccle")
    assert hasattr(module, "DoccleConnector")
    assert DoccleConnector.source == "doccle"


def test_search_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        DoccleConnector().search()


def test_download_pdfs_raises_not_implemented() -> None:
    ref = MailMessageRef(
        source="doccle",
        message_id="x",
        subject="",
        sender="",
        received_at=datetime.now(UTC),
    )
    with pytest.raises(NotImplementedError):
        DoccleConnector().download_pdfs(ref)


def test_from_config_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        DoccleConnector.from_config(None)
