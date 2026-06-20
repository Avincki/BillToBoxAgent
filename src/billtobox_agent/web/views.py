"""HTML routes for the read-only dashboard (task 18).

Five surfaces: the invoice list (home), an invoice detail page with its per-invoice
``agent_events`` audit trail, the exceptions queue, run history, the agent-activity
timeline, plus ``/logs`` (live SSE viewer) and ``/debug`` (per-source health). All
read-only; steering actions land in task 19. ``agent_events`` are stored already
redacted, so nothing rendered here can leak a secret or PDF bytes.

The ``TemplateResponse`` is built *inside* each ``async with uow`` block: Starlette
renders the template eagerly, so it must run while the session is still open (the
UnitOfWork expires ORM instances when it rolls back on exit).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from billtobox_agent.data.models import InvoiceStatus
from billtobox_agent.utils.clock import to_local
from billtobox_agent.web.dependencies import UowDep

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_templates = Jinja2Templates(directory=_TEMPLATES_DIR)

# Statuses that mean "needs a human": flagged for review, or stuck before storage.
_EXCEPTION_STATUSES = (InvoiceStatus.NEW.value, InvoiceStatus.REVIEWED.value)


def _localtime(value: datetime | None) -> str:
    return to_local(value).strftime("%Y-%m-%d %H:%M") if value is not None else ""


_templates.env.filters["localtime"] = _localtime

views_router = APIRouter()


@views_router.get("/", response_class=HTMLResponse)
async def invoice_list(request: Request, uow: UowDep) -> HTMLResponse:
    async with uow:
        invoices = await uow.invoices.list()
        return _templates.TemplateResponse(
            request=request, name="invoices.html", context={"invoices": invoices}
        )


@views_router.get("/invoices/{invoice_id}", response_class=HTMLResponse)
async def invoice_detail(request: Request, invoice_id: int, uow: UowDep) -> HTMLResponse:
    async with uow:
        invoice = await uow.invoices.get(invoice_id)
        if invoice is None:
            raise HTTPException(status_code=404, detail="invoice not found")
        events = await uow.agent_events.list(invoice_id=invoice_id, limit=500)
        return _templates.TemplateResponse(
            request=request,
            name="invoice_detail.html",
            context={"invoice": invoice, "events": events},
        )


@views_router.get("/exceptions", response_class=HTMLResponse)
async def exceptions(request: Request, uow: UowDep) -> HTMLResponse:
    async with uow:
        invoices = await uow.invoices.list_by_statuses(_EXCEPTION_STATUSES)
        return _templates.TemplateResponse(
            request=request, name="exceptions.html", context={"invoices": invoices}
        )


@views_router.get("/runs", response_class=HTMLResponse)
async def runs(request: Request, uow: UowDep) -> HTMLResponse:
    async with uow:
        run_rows = await uow.runs.list()
        return _templates.TemplateResponse(
            request=request, name="runs.html", context={"runs": run_rows}
        )


@views_router.get("/activity", response_class=HTMLResponse)
async def activity(
    request: Request,
    uow: UowDep,
    run_id: Annotated[int | None, Query()] = None,
    invoice_id: Annotated[int | None, Query()] = None,
    level: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    async with uow:
        events = await uow.agent_events.list(
            run_id=run_id, invoice_id=invoice_id, level=level or None, limit=300
        )
        return _templates.TemplateResponse(
            request=request,
            name="activity.html",
            context={
                "events": events,
                "filters": {"run_id": run_id, "invoice_id": invoice_id, "level": level},
            },
        )


@views_router.get("/debug", response_class=HTMLResponse)
async def debug(request: Request, uow: UowDep) -> HTMLResponse:
    async with uow:
        sources = await uow.source_status.list()
        return _templates.TemplateResponse(
            request=request, name="debug.html", context={"sources": sources}
        )


@views_router.get("/logs", response_class=HTMLResponse)
async def logs(request: Request) -> HTMLResponse:
    return _templates.TemplateResponse(request=request, name="logs.html", context={})
