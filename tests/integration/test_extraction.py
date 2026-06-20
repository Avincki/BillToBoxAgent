from __future__ import annotations

import base64
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

from billtobox_agent.config.models import AnthropicConfig
from billtobox_agent.data import (
    AgentEventLevel,
    AgentEventType,
    UnitOfWork,
    create_engine,
    create_session_factory,
    init_schema,
)
from billtobox_agent.extraction import ExtractionError, extract_fields, extract_invoice

_PDF = b"%PDF-1.7 fake invoice bytes"
_GOOD = json.dumps(
    {
        "is_invoice": True,
        "confidence": 0.95,
        "vendor": "KPN",
        "invoice_date": "2026-05-31",
        "amount": 149.95,
        "currency": "EUR",
    }
)


def _config(**overrides: Any) -> AnthropicConfig:
    return AnthropicConfig(api_key="sk-ant-secret", **overrides)


# ----- a minimal fake of the Anthropic client --------------------------------


def _response(text: str) -> Any:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


class _FakeMessages:
    def __init__(self, behavior: Any) -> None:
        self._behavior = behavior
        self.calls = 0
        self.last_kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        self.last_kwargs = kwargs
        return self._behavior(self.calls)


class FakeAnthropic:
    def __init__(self, behavior: Any) -> None:
        self.messages = _FakeMessages(behavior)


def _always(text: str) -> Any:
    return lambda _call: _response(text)


def _raise_then(exc: BaseException, text: str) -> Any:
    def behavior(call: int) -> Any:
        if call == 1:
            raise exc
        return _response(text)

    return behavior


def _always_raise(exc: BaseException) -> Any:
    def behavior(_call: int) -> Any:
        raise exc

    return behavior


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _conn_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(request=_request())


def _bad_request() -> anthropic.BadRequestError:
    return anthropic.BadRequestError(
        "bad", response=httpx.Response(400, request=_request()), body=None
    )


# ----- extract_fields: parsing, validation, gating ---------------------------


def test_parses_valid_invoice_and_sends_base64_document() -> None:
    client = FakeAnthropic(_always(_GOOD))

    result = extract_fields(client, _PDF, config=_config(), confidence_threshold=0.85)

    assert result.is_invoice is True
    assert result.vendor == "KPN"
    assert result.invoice_date == date(2026, 5, 31)
    assert result.amount == 149.95
    assert result.currency == "EUR"
    assert result.auto_approve is True

    # the PDF went out as a base64 document block, ahead of the instruction
    content = client.messages.last_kwargs["messages"][0]["content"]
    assert content[0]["type"] == "document"
    assert content[0]["source"]["media_type"] == "application/pdf"
    assert base64.standard_b64decode(content[0]["source"]["data"]) == _PDF
    assert content[1]["type"] == "text"
    assert client.messages.last_kwargs["model"] == "claude-opus-4-8"


def test_low_confidence_is_not_auto_approved() -> None:
    payload = json.dumps({"is_invoice": True, "confidence": 0.50, "currency": "EUR"})
    result = extract_fields(
        FakeAnthropic(_always(payload)), _PDF, config=_config(), confidence_threshold=0.85
    )
    assert result.confidence == 0.50
    assert result.auto_approve is False


def test_non_invoice_is_not_auto_approved_even_when_confident() -> None:
    payload = json.dumps({"is_invoice": False, "confidence": 0.99})
    result = extract_fields(
        FakeAnthropic(_always(payload)), _PDF, config=_config(), confidence_threshold=0.85
    )
    assert result.is_invoice is False
    assert result.auto_approve is False


@pytest.mark.parametrize(
    "payload",
    [
        '{"is_invoice": true, "confidence": 1.5, "currency": "EUR"}',  # confidence out of range
        '{"confidence": 0.9, "vendor": "X"}',  # missing is_invoice
        "this is not json at all",  # not JSON
        '{"is_invoice": true, "confidence": 0.9, "currency": "EURO"}',  # bad currency code
        '{"is_invoice": true, "confidence": 0.9, "invoice_date": "2026-13-40"}',  # impossible date
        "[1, 2, 3]",  # JSON, but not an object
    ],
)
def test_malformed_response_is_rejected(payload: str) -> None:
    client = FakeAnthropic(_always(payload))
    with pytest.raises(ExtractionError):
        extract_fields(client, _PDF, config=_config(), confidence_threshold=0.85)


def test_strips_code_fences_around_json() -> None:
    fenced = f"```json\n{_GOOD}\n```"
    result = extract_fields(
        FakeAnthropic(_always(fenced)), _PDF, config=_config(), confidence_threshold=0.85
    )
    assert result.vendor == "KPN"


# ----- retry behaviour -------------------------------------------------------


def test_retry_fires_once_on_transient_error() -> None:
    client = FakeAnthropic(_raise_then(_conn_error(), _GOOD))
    result = extract_fields(client, _PDF, config=_config(max_attempts=3), confidence_threshold=0.85)
    assert result.is_invoice is True
    assert client.messages.calls == 2  # one transient failure, then success


def test_non_transient_error_is_not_retried() -> None:
    client = FakeAnthropic(_always_raise(_bad_request()))
    with pytest.raises(anthropic.BadRequestError):
        extract_fields(client, _PDF, config=_config(max_attempts=3), confidence_threshold=0.85)
    assert client.messages.calls == 1


def test_transient_error_reraises_after_attempts_exhausted() -> None:
    client = FakeAnthropic(_always_raise(_conn_error()))
    with pytest.raises(anthropic.APIConnectionError):
        extract_fields(client, _PDF, config=_config(max_attempts=2), confidence_threshold=0.85)
    assert client.messages.calls == 2


# ----- extract_invoice: redacted agent_events --------------------------------


async def _factory(tmp_path: Path) -> tuple[Any, Any]:
    engine = create_engine(tmp_path / "extract.db")
    await init_schema(engine)
    return engine, create_session_factory(engine)


async def test_extract_invoice_emits_redacted_events(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    client = FakeAnthropic(_always(_GOOD))

    async with UnitOfWork(factory) as uow:
        run = await uow.runs.start()
        result = await extract_invoice(
            client,
            _PDF,
            config=_config(),
            confidence_threshold=0.85,
            uow=uow,
            run_id=run.id,
            step=1,
        )
        await uow.commit()
        run_id = run.id

    async with UnitOfWork(factory) as uow:
        events = await uow.agent_events.list(run_id=run_id)
        call_ev = next(e for e in events if e.event_type == AgentEventType.TOOL_CALL)
        result_ev = next(e for e in events if e.event_type == AgentEventType.TOOL_RESULT)
        call_inputs = call_ev.inputs_json
        result_outputs = result_ev.outputs_json

    await engine.dispose()

    assert result.auto_approve is True
    assert call_inputs is not None and result_outputs is not None
    # PDF bytes were redacted to a hash + length; the api key never appears anywhere
    assert call_inputs["model"] == "claude-opus-4-8"
    assert call_inputs["pdf_bytes"]["__bytes__"]["len"] == len(_PDF)
    assert "%PDF" not in json.dumps(call_inputs)
    assert "sk-ant-secret" not in json.dumps(call_inputs)
    # the result event carries the extracted fields, no secrets
    assert result_outputs["vendor"] == "KPN"
    assert result_outputs["currency"] == "EUR"
    assert result_outputs["invoice_date"] == "2026-05-31"
    assert result_outputs["auto_approve"] is True
    assert "sk-ant-secret" not in json.dumps(result_outputs)


async def test_extract_invoice_records_error_event_on_failure(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    client = FakeAnthropic(_always("not json"))

    async with UnitOfWork(factory) as uow:
        run = await uow.runs.start()
        with pytest.raises(ExtractionError):
            await extract_invoice(
                client,
                _PDF,
                config=_config(max_attempts=1),
                confidence_threshold=0.85,
                uow=uow,
                run_id=run.id,
            )
        await uow.commit()
        run_id = run.id

    async with UnitOfWork(factory) as uow:
        events = await uow.agent_events.list(run_id=run_id)
        # Capture plain values while the session is open (instances detach on close).
        errors = [
            {"level": e.level, "tool": e.tool, "outputs": e.outputs_json}
            for e in events
            if e.event_type == AgentEventType.ERROR
        ]

    await engine.dispose()

    assert len(errors) == 1
    assert errors[0]["level"] == AgentEventLevel.ERROR
    assert errors[0]["tool"] == "extract_invoice"
    assert "sk-ant-secret" not in json.dumps(errors[0]["outputs"])
