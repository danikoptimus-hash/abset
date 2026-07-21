"""users.strata_power_expanded: per-user "strata power check table expanded" flag

Visibility package §1: the strata power check on the Design tab collapses by
default when it has many strata (> 12); the user's choice to expand it persists,
exactly like strata_balance_expanded (0019) and folders_panel_collapsed (0018).
Per the rule documented in 0018, another flag = another additive typed BOOLEAN
column, not a switch to JSONB.

server_default=false — collapsed by default; only matters when the table is
long enough to collapse (<= 12 strata always renders expanded).

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "strata_power_expanded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "strata_power_expanded")
