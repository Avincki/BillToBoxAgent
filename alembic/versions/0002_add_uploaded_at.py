"""add invoices.uploaded_at

Records when an invoice's PDF was emailed to Billtobox (task 20). The original
schema (0001) omitted it; the send guard relies on it being NULL until sent.

Revision ID: 0002_add_uploaded_at
Revises: 0001_initial
Create Date: 2026-06-20 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_add_uploaded_at"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # render_as_batch (alembic/env.py) wraps this in a batch op on SQLite.
    op.add_column(
        "invoices",
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("invoices", "uploaded_at")
