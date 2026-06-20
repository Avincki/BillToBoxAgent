"""Fetch new invoice PDFs from a mail source, honouring watermark + dedup.

Reads the per-source watermark, lists candidates newer than it, skips any message
already turned into an invoice (dedup by ``source_message_id``), downloads the rest,
and advances the watermark. Connector calls are sync (googleapiclient / requests),
so they run in a thread to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from billtobox_agent.data import UnitOfWork
from billtobox_agent.mail.base import FetchedPdf, MailConnector


async def fetch_new_pdfs(
    connector: MailConnector,
    uow: UnitOfWork,
    *,
    since_override: datetime | None = None,
) -> list[FetchedPdf]:
    if since_override is not None:
        # An explicit starting date (e.g. the first web-launched run) overrides the
        # stored watermark: fetch from exactly this point. Already-processed messages
        # are still skipped below via the source_message_id dedup.
        since: datetime | None = since_override
    else:
        stored = await uow.source_status.get_watermark(connector.source)
        # SQLite drops tzinfo on round-trip; treat the stored watermark as UTC so the
        # connector's ``after:`` epoch is computed correctly.
        since = (
            stored.replace(tzinfo=UTC) if (stored is not None and stored.tzinfo is None) else stored
        )

    refs = await asyncio.to_thread(connector.search, since)

    new_pdfs: list[FetchedPdf] = []
    newest: datetime | None = since
    for ref in refs:
        newest = ref.received_at if newest is None else max(newest, ref.received_at)
        if await uow.invoices.exists_source_message_id(connector.source, ref.message_id):
            continue  # already processed in a previous run
        new_pdfs.extend(await asyncio.to_thread(connector.download_pdfs, ref))

    if newest is not None and newest != since:
        await uow.source_status.set_watermark(connector.source, newest)

    return new_pdfs
