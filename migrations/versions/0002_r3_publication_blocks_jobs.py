"""R3 (FRONTEND.md §3.3): experiments.publication_status, experiment_blocks,
jobs; datasets.experiment_id становится nullable (пред-дизайн датасет
загружается ДО того, как эксперимент создан — POST /datasets {experiment_id?}).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-08
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column(
            "publication_status", sa.Text(), nullable=False, server_default="draft"
        ),
    )
    op.create_check_constraint(
        "ck_experiments_publication_status",
        "experiments",
        "publication_status IN ('draft','published')",
    )

    op.alter_column("datasets", "experiment_id", nullable=True)

    op.create_table(
        "experiment_blocks",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "experiment_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_md", sa.Text(), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "kind IN ('hypothesis','conclusion','decision','custom')",
            name="ck_experiment_blocks_kind",
        ),
    )
    op.create_index(
        "ix_experiment_blocks_experiment", "experiment_blocks", ["experiment_id", "position"]
    )

    op.create_table(
        "jobs",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("progress", postgresql.JSONB(), nullable=True),
        sa.Column("result_ref", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','running','requires_confirmation','completed','failed')",
            name="ck_jobs_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_index("ix_experiment_blocks_experiment", table_name="experiment_blocks")
    op.drop_table("experiment_blocks")
    op.alter_column("datasets", "experiment_id", nullable=False)
    op.drop_constraint("ck_experiments_publication_status", "experiments", type_="check")
    op.drop_column("experiments", "publication_status")
