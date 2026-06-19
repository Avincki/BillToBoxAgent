"""Unit of work — bundles a session with the repositories.

Usage::

    async with UnitOfWork(session_factory) as uow:
        run = await uow.runs.start()
        await uow.invoices.add(invoice)
        await uow.commit()

On exit any uncommitted work is rolled back and the session closed.
"""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from billtobox_agent.data.repositories import (
    AgentEventsRepository,
    InvoicesRepository,
    RunsRepository,
    SourceStatusRepository,
)


class UnitOfWork:
    _session: AsyncSession
    invoices: InvoicesRepository
    runs: RunsRepository
    source_status: SourceStatusRepository
    agent_events: AgentEventsRepository

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __aenter__(self) -> UnitOfWork:
        self._session = self._session_factory()
        self.invoices = InvoicesRepository(self._session)
        self.runs = RunsRepository(self._session)
        self.source_status = SourceStatusRepository(self._session)
        self.agent_events = AgentEventsRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            await self._session.rollback()
        finally:
            await self._session.close()

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
