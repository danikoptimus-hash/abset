"""Backfill experiment_datasets (kind='post_analysis') from existing
analysis_results rows (item 1 bug package). Root cause of the "analysis
datasets show no experiment in the Datasets list" report: the list column
was reading datasets.experiment_id (the single legacy PRIMARY/first-use
field, CLAUDE.md) instead of the experiment_datasets many-to-many table —
that read-side bug is fixed in the same package (backend/routers/datasets.py).
The link-writing code (abkit/jobs.py) was already correct going forward, but
any analysis run BEFORE that write existed (or under code paths that predate
it) left analysis_results.dataset_id as the only record of the association,
with no matching experiment_datasets row — this migration reconstructs those.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-16
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO experiment_datasets (experiment_id, dataset_id, kind)
        SELECT DISTINCT experiment_id, dataset_id, 'post_analysis'
        FROM analysis_results
        WHERE dataset_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM experiment_datasets ed
        USING analysis_results ar
        WHERE ed.kind = 'post_analysis'
          AND ed.experiment_id = ar.experiment_id
          AND ed.dataset_id = ar.dataset_id
        """
    )
