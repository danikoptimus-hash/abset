"""UX package: users.name -> first_name/last_name split, experiment_access
table (additional owners/editors), experiments.visible_roles.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-09
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("first_name", sa.Text(), nullable=False, server_default=""))
    op.add_column("users", sa.Column("last_name", sa.Text(), nullable=False, server_default=""))
    # Data backfill: first word -> first_name, remainder (trimmed) -> last_name.
    op.execute(
        """
        UPDATE users
        SET first_name = CASE WHEN position(' ' in name) > 0
                               THEN split_part(name, ' ', 1)
                               ELSE name END,
            last_name = CASE WHEN position(' ' in name) > 0
                              THEN trim(substring(name from position(' ' in name) + 1))
                              ELSE '' END
        """
    )
    op.drop_column("users", "name")

    op.add_column("experiments", sa.Column("visible_roles", postgresql.JSONB(), nullable=True))

    op.create_table(
        "experiment_access",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "experiment_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("access", sa.Text(), nullable=False),
        sa.CheckConstraint("access IN ('owner','editor')", name="ck_experiment_access_access"),
    )
    op.create_index("ix_experiment_access_experiment", "experiment_access", ["experiment_id"])
    op.create_index("ix_experiment_access_user", "experiment_access", ["user_id"])
    op.create_index(
        "ux_experiment_access_experiment_user", "experiment_access",
        ["experiment_id", "user_id"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_experiment_access_experiment_user", table_name="experiment_access")
    op.drop_index("ix_experiment_access_user", table_name="experiment_access")
    op.drop_index("ix_experiment_access_experiment", table_name="experiment_access")
    op.drop_table("experiment_access")
    op.drop_column("experiments", "visible_roles")

    op.add_column("users", sa.Column("name", sa.Text(), nullable=False, server_default=""))
    op.execute("UPDATE users SET name = trim(first_name || ' ' || last_name)")
    op.drop_column("users", "last_name")
    op.drop_column("users", "first_name")
