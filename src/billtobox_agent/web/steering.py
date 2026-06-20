"""Dashboard steering actions — state-changing POST routes (task 19).

Every route is guarded by :func:`require_same_origin` and delegates to the
worker's tool functions (which emit ``agent_events``); the web layer never writes
the database or Drive directly. Each returns a 303 redirect (POST/redirect/GET) so
a refresh doesn't re-submit.

Actions: approve / reject / edit fields (with a Drive move when the quarter
changes) / re-extract / approve the Billtobox send / trigger a manual run.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.datastructures import FormData

from billtobox_agent.pipeline import (
    WorkerContext,
    approve_invoice,
    edit_invoice,
    queue_billtobox_upload,
    reextract_invoice,
    reject_invoice,
    run_once,
)
from billtobox_agent.web.dependencies import (
    AnthropicDep,
    ConfigDep,
    DriveDep,
    MailConnectorsDep,
    UowDep,
    get_session_factory,
    require_same_origin,
)

steering_router = APIRouter(dependencies=[Depends(require_same_origin)])


def _str_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _float_or_none(value: object, field: str) -> float | None:
    text = _str_or_none(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid {field}: {text!r}") from exc


def _is_checked(value: object) -> bool:
    """An HTML checkbox submits its value only when ticked (absent otherwise)."""
    return isinstance(value, str) and value.strip().lower() in {"on", "true", "1", "yes"}


def _date_or_none(value: object, field: str) -> date | None:
    text = _str_or_none(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid {field}: {text!r}") from exc


def _back(invoice_id: int) -> RedirectResponse:
    return RedirectResponse(f"/invoices/{invoice_id}", status_code=303)


@steering_router.post("/invoices/{invoice_id}/approve")
async def approve(invoice_id: int, uow: UowDep) -> RedirectResponse:
    async with uow:
        await approve_invoice(uow, invoice_id)
        await uow.commit()
    return _back(invoice_id)


@steering_router.post("/invoices/{invoice_id}/reject")
async def reject(invoice_id: int, request: Request, uow: UowDep) -> RedirectResponse:
    form: FormData = await request.form()
    reason = _str_or_none(form.get("reason"))
    async with uow:
        await reject_invoice(uow, invoice_id, reason=reason)
        await uow.commit()
    return _back(invoice_id)


@steering_router.post("/invoices/{invoice_id}/queue-send")
async def queue_send(invoice_id: int, uow: UowDep) -> RedirectResponse:
    async with uow:
        await queue_billtobox_upload(uow, invoice_id)
        await uow.commit()
    return _back(invoice_id)


@steering_router.post("/invoices/{invoice_id}/edit")
async def edit(
    invoice_id: int,
    request: Request,
    uow: UowDep,
    config: ConfigDep,
    drive: DriveDep,
) -> RedirectResponse:
    form: FormData = await request.form()
    async with uow:
        await edit_invoice(
            uow,
            drive,
            invoice_id,
            vendor=_str_or_none(form.get("vendor")),
            invoice_date=_date_or_none(form.get("invoice_date"), "invoice_date"),
            amount=_float_or_none(form.get("amount"), "amount"),
            currency=_str_or_none(form.get("currency")),
            accounting=config.accounting,
        )
        await uow.commit()
    return _back(invoice_id)


@steering_router.post("/invoices/{invoice_id}/reextract")
async def reextract(
    invoice_id: int,
    uow: UowDep,
    config: ConfigDep,
    drive: DriveDep,
    anthropic_client: AnthropicDep,
) -> RedirectResponse:
    if drive is None or anthropic_client is None:
        raise HTTPException(status_code=503, detail="Drive/Anthropic unavailable for re-extraction")
    async with uow:
        await reextract_invoice(
            uow,
            drive,
            anthropic_client,
            invoice_id,
            anthropic_config=config.anthropic,
            extraction_config=config.extraction,
            accounting=config.accounting,
        )
        await uow.commit()
    return _back(invoice_id)


@steering_router.post("/run")
async def manual_run(
    request: Request,
    config: ConfigDep,
    drive: DriveDep,
    anthropic_client: AnthropicDep,
    mail_connectors: MailConnectorsDep,
) -> RedirectResponse:
    if drive is None or anthropic_client is None:
        raise HTTPException(
            status_code=503, detail="worker components unavailable (check config/tokens)"
        )
    form: FormData = await request.form()
    since = _date_or_none(form.get("since"), "since")
    dry_run = _is_checked(form.get("dry_run"))
    no_send = _is_checked(form.get("no_send"))
    ctx = WorkerContext(
        config=config,
        session_factory=get_session_factory(request),
        mail_connectors=mail_connectors,
        drive=drive,
        anthropic_client=anthropic_client,
        dry_run=dry_run,
        billtobox_send_enabled=not no_send,
    )
    await run_once(ctx, since_override=since)
    return RedirectResponse("/runs", status_code=303)
