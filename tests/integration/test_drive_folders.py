from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from billtobox_agent.config.models import AccountingConfig
from billtobox_agent.data import (
    AgentEventLevel,
    AgentEventType,
    UnitOfWork,
    create_engine,
    create_session_factory,
    init_schema,
)
from billtobox_agent.drive import DriveConnector, ensure_quarter_folder

# ----- a minimal fake of the googleapiclient Drive v3 resource ----------------
# camelCase params mirror the real API kwargs. The fake resolves the connector's
# ``q`` string (name + parent) so find-before-create behaves like real Drive:
# a previously-created folder is returned instead of duplicated.

_NAME_RE = re.compile(r"name = '([^']*)'")
_PARENT_RE = re.compile(r"'([^']*)' in parents")


def _name_and_parent(query: str) -> tuple[str, str]:
    name = _NAME_RE.search(query)
    parent = _PARENT_RE.search(query)
    assert name is not None and parent is not None, query
    return name.group(1), parent.group(1)


class _Resp:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def execute(self) -> dict[str, Any]:
        return self._result


class _Files:
    def __init__(self, store: FakeDriveService) -> None:
        self._store = store

    def list(
        self,
        *,
        q: str,
        spaces: str = "drive",
        fields: str | None = None,
        pageSize: int | None = None,
    ) -> _Resp:
        self._store.list_calls.append(q)
        name, parent = _name_and_parent(q)
        matches = [
            {"id": f["id"], "name": f["name"]}
            for f in self._store.folders
            if f["name"] == name and f["parent"] == parent
        ]
        return _Resp({"files": matches})

    def create(self, *, body: dict[str, Any], fields: str | None = None) -> _Resp:
        if self._store.fail_create:
            raise RuntimeError("drive down")
        name = body["name"]
        parent = body["parents"][0]
        self._store.counter += 1
        folder_id = f"fld-{self._store.counter}"
        self._store.folders.append({"id": folder_id, "name": name, "parent": parent})
        self._store.create_calls.append((name, parent))
        return _Resp({"id": folder_id})


class FakeDriveService:
    def __init__(self, *, fail_create: bool = False) -> None:
        self.folders: list[dict[str, str]] = []
        self.list_calls: list[str] = []
        self.create_calls: list[tuple[str, str]] = []
        self.counter = 0
        self.fail_create = fail_create

    def files(self) -> _Files:
        return _Files(self)


# ----- connector tests --------------------------------------------------------


def test_ensure_quarter_path_creates_full_tree() -> None:
    service = FakeDriveService()
    connector = DriveConnector(service)

    leaf = connector.ensure_quarter_path("2026", "Q1")

    # One find (a miss) preceded each create — find-before-create at every level.
    assert len(service.list_calls) == 3
    assert service.create_calls == [("Invoices", "root"), ("2026", "fld-1"), ("Q1", "fld-2")]
    assert leaf == "fld-3"


def test_second_call_same_quarter_creates_nothing() -> None:
    service = FakeDriveService()
    connector = DriveConnector(service)

    first = connector.ensure_quarter_path("2026", "Q1")
    creates_after_first = len(service.create_calls)
    second = connector.ensure_quarter_path("2026", "Q1")

    assert second == first
    assert len(service.create_calls) == creates_after_first  # nothing new created


def test_fresh_connector_reuses_existing_tree() -> None:
    # A new process (new connector) sharing the same Drive must reuse, not recreate.
    service = FakeDriveService()
    first = DriveConnector(service).ensure_quarter_path("2026", "Q1")
    creates_after_first = len(service.create_calls)

    second = DriveConnector(service).ensure_quarter_path("2026", "Q1")

    assert second == first
    assert len(service.create_calls) == creates_after_first


def test_new_quarter_reuses_root_and_year() -> None:
    service = FakeDriveService()
    connector = DriveConnector(service)

    connector.ensure_quarter_path("2026", "Q1")
    connector.ensure_quarter_path("2026", "Q2")

    # Q2 reuses Invoices + 2026; only the Q2 folder is created the second time.
    assert service.create_calls == [
        ("Invoices", "root"),
        ("2026", "fld-1"),
        ("Q1", "fld-2"),
        ("Q2", "fld-2"),
    ]


def test_pinned_root_folder_id_skips_root_lookup() -> None:
    service = FakeDriveService()
    connector = DriveConnector(service, root_folder_id="ROOT123")

    leaf = connector.ensure_quarter_path("2026", "Q1")

    # The root is never searched or created — only the year and quarter under it.
    assert ("Invoices", "root") not in service.create_calls
    assert service.create_calls == [("2026", "ROOT123"), ("Q1", "fld-1")]
    assert leaf == "fld-2"


def test_query_escapes_single_quotes() -> None:
    service = FakeDriveService()
    connector = DriveConnector(service, root_folder_name="O'Brien")

    connector.ensure_root_folder()

    # The apostrophe is backslash-escaped inside the Drive q string.
    assert r"name = 'O\'Brien'" in service.list_calls[0]


# ----- async pipeline-tool tests ----------------------------------------------


async def _factory(tmp_path: Path) -> Any:
    engine = create_engine(tmp_path / "drive.db")
    await init_schema(engine)
    return engine, create_session_factory(engine)


async def test_tool_maps_calendar_quarter_and_audits(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    service = FakeDriveService()
    connector = DriveConnector(service)

    async with UnitOfWork(factory) as uow:
        folder_id = await ensure_quarter_folder(
            connector,
            date(2026, 5, 17),  # calendar Q2 of 2026
            accounting=AccountingConfig(),
            uow=uow,
            run_id=None,
            invoice_id=None,
        )
        await uow.commit()
        # Read attributes into plain locals while the session is still open.
        events = await uow.agent_events.list()
        result_tool = next(e.tool for e in events if e.event_type == AgentEventType.TOOL_RESULT)
        result_outputs = next(
            e.outputs_json for e in events if e.event_type == AgentEventType.TOOL_RESULT
        )
        call_inputs = next(
            e.inputs_json for e in events if e.event_type == AgentEventType.TOOL_CALL
        )

    await engine.dispose()

    assert folder_id == "fld-3"  # Invoices -> 2026 -> Q2
    assert service.create_calls == [("Invoices", "root"), ("2026", "fld-1"), ("Q2", "fld-2")]
    assert result_tool == "ensure_quarter_folder"
    assert result_outputs == {"folder_id": "fld-3", "path": "Invoices/2026/Q2"}
    assert call_inputs == {
        "invoice_date": "2026-05-17",
        "fy_label": "2026",
        "quarter": "Q2",
    }


async def test_tool_honours_fiscal_year_offset(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    connector = DriveConnector(FakeDriveService())

    async with UnitOfWork(factory) as uow:
        await ensure_quarter_folder(
            connector,
            date(2026, 2, 10),  # fy_start=4: Feb 2026 is Q4 of label 2025
            accounting=AccountingConfig(fiscal_year_start_month=4),
            uow=uow,
        )
        await uow.commit()
        events = await uow.agent_events.list()
        result_outputs = next(
            e.outputs_json for e in events if e.event_type == AgentEventType.TOOL_RESULT
        )

    await engine.dispose()

    assert result_outputs is not None
    assert result_outputs["path"] == "Invoices/2025/Q4"


async def test_tool_records_error_and_reraises(tmp_path: Path) -> None:
    engine, factory = await _factory(tmp_path)
    connector = DriveConnector(FakeDriveService(fail_create=True))

    async with UnitOfWork(factory) as uow:
        with pytest.raises(RuntimeError):
            await ensure_quarter_folder(
                connector,
                date(2026, 1, 1),
                accounting=AccountingConfig(),
                uow=uow,
            )
        await uow.commit()
        events = await uow.agent_events.list()
        error_events = [(e.event_type, e.tool) for e in events if e.level == AgentEventLevel.ERROR]

    await engine.dispose()

    assert len(error_events) == 1
    assert error_events[0] == (AgentEventType.ERROR, "ensure_quarter_folder")
