"""UX package (CLAUDE.md 0.4): default experiment_blocks titles are now
created in English (Hypothesis/Conclusions/Decision) going forward — this
data migration only relabels EXISTING blocks that still have the old
Russian default title AND are still empty (untouched by the user). Blocks
the user has actually written content into are left alone: the title is UI
chrome, not user data, but we only rewrite it where we're certain it's still
the untouched default.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-09
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RENAMES = {
    "Гипотеза": "Hypothesis",
    "Выводы": "Conclusions",
    "Решение": "Decision",
}


def upgrade() -> None:
    for old_title, new_title in _RENAMES.items():
        op.execute(
            f"""
            UPDATE experiment_blocks
            SET title = '{new_title}'
            WHERE title = '{old_title}' AND trim(content_md) = ''
            """
        )


def downgrade() -> None:
    for old_title, new_title in _RENAMES.items():
        op.execute(
            f"""
            UPDATE experiment_blocks
            SET title = '{old_title}'
            WHERE title = '{new_title}' AND trim(content_md) = ''
            """
        )
