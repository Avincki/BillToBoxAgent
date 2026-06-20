"""Claude invoice extraction (task 13).

Sends a PDF to Claude as a base64 ``document`` block with a JSON-only instruction,
parses and validates the response against a strict schema, and applies the
confidence-threshold gate that decides whether an item may be auto-approved.

Two layers:

* :func:`extract_fields` — the synchronous core (model call + retry + parse +
  validate + gate). Pure of the database; unit-tested with a fake client.
* :func:`extract_invoice` — the async pipeline tool: runs ``extract_fields`` in a
  worker thread (the Anthropic client is sync) and writes redacted ``agent_events``
  so each call/result is auditable. Never logs the PDF bytes or the API key.

Model id defaults to ``claude-opus-4-8`` per the claude-api reference; override it
in ``config.yaml`` to use a cheaper tier.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from datetime import date
from typing import Any

import anthropic
from anthropic.types import Message
from pydantic import BaseModel, ConfigDict, Field, field_validator
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from billtobox_agent.config.models import AnthropicConfig
from billtobox_agent.data import UnitOfWork
from billtobox_agent.data.models import AgentEventLevel, AgentEventType

# Transient failures worth retrying: connection drops, rate limits, and 5xx
# (InternalServerError covers 500/529). Client errors (400/401/403/404) are not
# retried — they will not succeed on a second identical request.
_TRANSIENT_ERRORS = (
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)

INVOICE_INSTRUCTION = (
    "You are extracting structured data from a PDF that may or may not be an invoice.\n"
    "Respond with ONLY a single JSON object — no prose, no markdown, no code fences.\n"
    "Fields:\n"
    "  is_invoice (boolean): true if this document is an invoice/bill (factuur/rekening)\n"
    "  confidence (number 0.0-1.0): your confidence in is_invoice and the extracted fields\n"
    "  vendor (string|null): the issuing company/supplier name\n"
    "  invoice_date (string|null): the invoice date as YYYY-MM-DD\n"
    "  amount (number|null): the total amount due, as a number with no currency symbol\n"
    "  currency (string|null): ISO 4217 code, e.g. EUR, USD\n"
    "Use null for any field you cannot determine. If is_invoice is false, the rest may be null."
)


class ExtractionError(Exception):
    """The model response could not be parsed or failed schema validation."""


class ExtractedInvoice(BaseModel):
    """Validated shape of the model's JSON response (CLAUDE.md extraction schema)."""

    model_config = ConfigDict(extra="ignore")

    is_invoice: bool
    confidence: float = Field(ge=0.0, le=1.0)
    vendor: str | None = None
    invoice_date: date | None = None
    amount: float | None = None
    currency: str | None = None

    @field_validator("currency")
    @classmethod
    def _currency_iso4217(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("currency must be a 3-letter ISO 4217 code")
        return normalized


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Extracted fields plus the auto-approval decision (confidence gate applied)."""

    is_invoice: bool
    confidence: float
    vendor: str | None
    invoice_date: date | None
    amount: float | None
    currency: str | None
    auto_approve: bool


def build_anthropic_client(config: AnthropicConfig) -> anthropic.Anthropic:
    """Construct the SDK client with its own retries disabled.

    Our :func:`extract_fields` tenacity loop owns transient-error retries so the
    ``agent_events`` audit trail reflects each attempt; double-retrying would hide
    attempts and slow failures.
    """
    return anthropic.Anthropic(api_key=config.api_key.get_secret_value(), max_retries=0)


def extract_fields(
    client: anthropic.Anthropic,
    pdf_bytes: bytes,
    *,
    config: AnthropicConfig,
    confidence_threshold: float,
) -> ExtractionResult:
    """Call Claude, validate the response, and apply the confidence gate.

    Raises :class:`ExtractionError` on a malformed/invalid response. Transient API
    errors are retried up to ``config.max_attempts`` times; a non-transient API
    error (e.g. a 400) propagates immediately.
    """
    doc_b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
    response = _create_with_retry(client, doc_b64=doc_b64, config=config)
    invoice = _validate(_parse_response(response))
    auto_approve = invoice.is_invoice and invoice.confidence >= confidence_threshold
    return ExtractionResult(
        is_invoice=invoice.is_invoice,
        confidence=invoice.confidence,
        vendor=invoice.vendor,
        invoice_date=invoice.invoice_date,
        amount=invoice.amount,
        currency=invoice.currency,
        auto_approve=auto_approve,
    )


async def extract_invoice(
    client: anthropic.Anthropic,
    pdf_bytes: bytes,
    *,
    config: AnthropicConfig,
    confidence_threshold: float,
    uow: UnitOfWork,
    run_id: int | None = None,
    invoice_id: int | None = None,
    step: int = 0,
) -> ExtractionResult:
    """Pipeline tool: extract fields off-thread and record redacted audit events."""
    await uow.agent_events.add(
        event_type=AgentEventType.TOOL_CALL,
        summary="extract_invoice",
        run_id=run_id,
        invoice_id=invoice_id,
        step=step,
        tool="extract_invoice",
        inputs={"model": config.model, "pdf_bytes": pdf_bytes},
    )
    try:
        result = await asyncio.to_thread(
            extract_fields,
            client,
            pdf_bytes,
            config=config,
            confidence_threshold=confidence_threshold,
        )
    except Exception as exc:
        await uow.agent_events.add(
            event_type=AgentEventType.ERROR,
            summary=f"extract_invoice failed: {type(exc).__name__}",
            run_id=run_id,
            invoice_id=invoice_id,
            step=step,
            tool="extract_invoice",
            level=AgentEventLevel.ERROR,
            outputs={"error": str(exc)},
        )
        raise

    await uow.agent_events.add(
        event_type=AgentEventType.TOOL_RESULT,
        summary=(
            f"extract_invoice: is_invoice={result.is_invoice} "
            f"confidence={result.confidence:.2f} auto_approve={result.auto_approve}"
        ),
        run_id=run_id,
        invoice_id=invoice_id,
        step=step,
        tool="extract_invoice",
        outputs=_result_outputs(result),
    )
    return result


def _create_with_retry(
    client: anthropic.Anthropic, *, doc_b64: str, config: AnthropicConfig
) -> Message:
    for attempt in Retrying(
        retry=retry_if_exception_type(_TRANSIENT_ERRORS),
        stop=stop_after_attempt(config.max_attempts),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=20),
        reraise=True,
    ):
        with attempt:
            return _create(client, doc_b64=doc_b64, config=config)
    raise AssertionError("retry loop exited without returning")  # pragma: no cover


def _create(client: anthropic.Anthropic, *, doc_b64: str, config: AnthropicConfig) -> Message:
    return client.messages.create(
        model=config.model,
        max_tokens=config.max_tokens,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": doc_b64,
                        },
                    },
                    {"type": "text", "text": INVOICE_INSTRUCTION},
                ],
            }
        ],
    )


def _parse_response(response: Message) -> dict[str, Any]:
    text: str | None = None
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", None)
            break
    if text is None:
        raise ExtractionError("model response contained no text block")

    payload = _strip_fences(text)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"model response was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ExtractionError("model response JSON was not an object")
    return data


def _validate(data: dict[str, Any]) -> ExtractedInvoice:
    try:
        return ExtractedInvoice.model_validate(data)
    except ValueError as exc:  # pydantic ValidationError is a ValueError subclass
        raise ExtractionError(f"model response failed schema validation: {exc}") from exc


def _strip_fences(text: str) -> str:
    """Tolerate a stray ```json … ``` code fence around the JSON object."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped[3:]
        if stripped[:4].lower() == "json":
            stripped = stripped[4:]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    return stripped.strip()


def _result_outputs(result: ExtractionResult) -> dict[str, Any]:
    return {
        "is_invoice": result.is_invoice,
        "confidence": result.confidence,
        "vendor": result.vendor,
        "invoice_date": result.invoice_date.isoformat() if result.invoice_date else None,
        "amount": result.amount,
        "currency": result.currency,
        "auto_approve": result.auto_approve,
    }
