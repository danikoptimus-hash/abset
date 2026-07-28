"""datasets.excluded_columns: per-column exclusion list (removable columns)

Part 2 (removable columns): a dataset can carry a persisted list of columns to
exclude. The physical file/SQL snapshot is never rewritten — the exclusion is
applied lazily wherever the data is read/materialized (abkit/dataset_exclusions.
py::apply_column_exclusions), so it works identically for upload/SQL/demo and
survives an SQL Refresh (the re-fetch brings the column back into the raw
snapshot, the stored exclusion simply re-applies).

Additive, nullable JSONB list of excluded column names. NULL/[] = nothing
excluded — the default for every existing dataset, so no backfill runs here.

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column("excluded_columns", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("datasets", "excluded_columns")
