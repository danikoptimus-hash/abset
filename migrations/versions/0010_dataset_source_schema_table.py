"""datasets.source_schema/source_table (Datasets follow-up: persist source
schema/table instead of re-parsing SQL in the UI) — the Edit-modal cascade
selects used to be prefilled by parsing sql_text on the fly, which failed
silently for any dataset whose query didn't match the narrow "simple FROM
schema.table" shape (and, it turned out, even for some that did — see
abkit/db_connections/sql_parsing.py's trailing-\\b fix). Recording the
selection explicitly at creation/edit time is authoritative and doesn't
depend on being able to reverse-engineer it from SQL text later.

Backfills existing source='sql' rows by running the same simple parse
against their stored sql_text — best-effort, leaves NULL where the query
isn't a plain "FROM schema.table" (JOIN/CTE/subquery/unqualified), same as
the live parse fallback the frontend still uses for whatever this backfill
can't confidently resolve.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-12
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from abkit.db_connections.sql_parsing import parse_schema_table_from_sql

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("source_schema", sa.Text(), nullable=True))
    op.add_column("datasets", sa.Column("source_table", sa.Text(), nullable=True))

    datasets = sa.table(
        "datasets",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("sql_text", sa.Text),
        sa.column("source", sa.Text),
        sa.column("source_schema", sa.Text),
        sa.column("source_table", sa.Text),
    )
    conn = op.get_bind()
    rows = conn.execute(
        sa.select(datasets.c.id, datasets.c.sql_text).where(datasets.c.source == "sql")
    ).fetchall()
    for row in rows:
        schema, table = parse_schema_table_from_sql(row.sql_text or "")
        if schema and table:
            conn.execute(
                datasets.update()
                .where(datasets.c.id == row.id)
                .values(source_schema=schema, source_table=table)
            )


def downgrade() -> None:
    op.drop_column("datasets", "source_table")
    op.drop_column("datasets", "source_schema")
