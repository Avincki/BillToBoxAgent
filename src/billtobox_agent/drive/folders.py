"""Quarter-folder pipeline tool (task 14).

Maps an invoice date to its accounting ``(fy_label, quarter)`` via
:func:`period_for`, then ensures ``<root>/<fy_label>/<quarter>/`` exists on Drive
(find-or-create at every level) and returns the leaf folder id. Drive calls are
synchronous (googleapiclient), so they run in a worker thread to avoid blocking
the event loop; each call and result is recorded as a redacted ``agent_events``
row so the folder placement is auditable per invoice (decisions.md §2).
"""

from __future__ import annotations

import asyncio
from datetime import date

from billtobox_agent.config.models import AccountingConfig
from billtobox_agent.data import UnitOfWork
from billtobox_agent.data.models import AgentEventLevel, AgentEventType
from billtobox_agent.drive.connector import DriveConnector
from billtobox_agent.extraction.period import period_for


async def ensure_quarter_folder(
    connector: DriveConnector,
    invoice_date: date,
    *,
    accounting: AccountingConfig,
    uow: UnitOfWork,
    run_id: int | None = None,
    invoice_id: int | None = None,
    step: int = 0,
) -> str:
    """Return the Drive folder id for ``invoice_date``'s accounting quarter.

    Idempotent: a second call for a date in the same quarter returns the same id
    and creates nothing (the connector finds existing folders before creating).
    """
    fy_label, quarter = period_for(
        invoice_date,
        accounting.fiscal_year_start_month,
        accounting.fy_label_prefix,
    )
    logical_path = f"{connector.root_folder_name}/{fy_label}/{quarter}"

    await uow.agent_events.add(
        event_type=AgentEventType.TOOL_CALL,
        summary=f"ensure_quarter_folder {logical_path}",
        run_id=run_id,
        invoice_id=invoice_id,
        step=step,
        tool="ensure_quarter_folder",
        inputs={
            "invoice_date": invoice_date.isoformat(),
            "fy_label": fy_label,
            "quarter": quarter,
        },
    )
    try:
        folder_id = await asyncio.to_thread(connector.ensure_quarter_path, fy_label, quarter)
    except Exception as exc:
        await uow.agent_events.add(
            event_type=AgentEventType.ERROR,
            summary=f"ensure_quarter_folder failed: {type(exc).__name__}",
            run_id=run_id,
            invoice_id=invoice_id,
            step=step,
            tool="ensure_quarter_folder",
            level=AgentEventLevel.ERROR,
            outputs={"error": str(exc)},
        )
        raise

    await uow.agent_events.add(
        event_type=AgentEventType.TOOL_RESULT,
        summary=f"ensure_quarter_folder -> {logical_path}",
        run_id=run_id,
        invoice_id=invoice_id,
        step=step,
        tool="ensure_quarter_folder",
        outputs={"folder_id": folder_id, "path": logical_path},
    )
    return folder_id
