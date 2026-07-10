"""tags + experiment_tags (Superset-style A/B test tags, CLAUDE.md) —
additive: two new tables, nothing existing changes. `tags.name` is CITEXT
(case-insensitive unique, same pattern as users.email) so "Checkout" and
"checkout" collide into one tag. `experiment_tags` is a bare composite-PK
link table (no surrogate id — unlike experiment_datasets, there's no extra
column to justify one), ON DELETE CASCADE both directions.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-11
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", postgresql.CITEXT(), nullable=False, unique=True),
        sa.Column("color", sa.Text(), nullable=True),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "experiment_tags",
        sa.Column(
            "experiment_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "tag_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True,
        ),
    )
    op.create_index("ix_experiment_tags_tag", "experiment_tags", ["tag_id"])


def downgrade() -> None:
    op.drop_index("ix_experiment_tags_tag", table_name="experiment_tags")
    op.drop_table("experiment_tags")
    op.drop_table("tags")
