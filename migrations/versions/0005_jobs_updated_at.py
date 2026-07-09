"""jobs.updated_at (job-heartbeat timeout, CLAUDE.md job-reliability
package): touched on every status/progress change (onupdate=func.now() at
the ORM level, abkit/db/models.py::Job.updated_at) so a periodic sweeper
can detect a job stuck in 'running' whose worker died without raising a
catchable exception (e.g. OOM-killed process) — see
backend/jobs/runner.py::JobRunner._sweep_stale_jobs.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-10
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_column("jobs", "updated_at")
