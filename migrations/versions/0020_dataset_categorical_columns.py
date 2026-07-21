"""datasets.categorical_columns: per-column categorical flag

Part 2: a column's NATURE (categorical vs continuous) used to be inferred from
dtype alone, which silently binned integer categories (months_ago ∈ {1,2,3,5})
into interval strata like "(0.999, 2.0]". The flag makes it an explicit,
user-editable dataset property.

Additive, nullable JSONB list of the columns marked categorical. NULL = "never
resolved" — datasets created before this feature are backfilled LAZILY by the
heuristic (string/bool → categorical; numeric with <= 20 distinct → categorical)
on first design/analyze read (abkit/dataset_categorical.py::resolve_categorical_columns),
so no expensive per-parquet backfill runs in this migration. New datasets store
the resolved list at creation; Edit persists user overrides.

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column("categorical_columns", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("datasets", "categorical_columns")
