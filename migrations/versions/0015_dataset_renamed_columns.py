"""datasets.renamed_columns (Item 1, upload rename step): records which
columns were renamed at upload confirmation time and what their original
names were — {new_name: original_name}, only for columns that actually
changed (not every column). NULL for datasets with no renames (the common
case) and for source='sql'/'demo' (renaming is upload-only, item 1.4 — SQL
column names come from the query's own aliases).

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-22
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("renamed_columns", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("datasets", "renamed_columns")
