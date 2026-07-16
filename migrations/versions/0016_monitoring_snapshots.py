"""Admin monitoring panel: monitoring_snapshots table (collector daemon
thread, abkit/monitoring.py — no Docker socket, no new services) +
jobs.peak_memory_mb (per-job peak backend RSS, sampled every 2s while a job
runs, backend/jobs/runner.py).

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-16
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("peak_memory_mb", sa.Float(), nullable=True))

    op.create_table(
        "monitoring_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=False, server_default="raw"),
        sa.Column("backend_rss_mb", sa.Float(), nullable=True),
        sa.Column("backend_rss_mb_min", sa.Float(), nullable=True),
        sa.Column("backend_rss_mb_max", sa.Float(), nullable=True),
        sa.Column("db_total_mb", sa.Float(), nullable=True),
        sa.Column("db_total_mb_min", sa.Float(), nullable=True),
        sa.Column("db_total_mb_max", sa.Float(), nullable=True),
        sa.Column("data_volume_mb", sa.Float(), nullable=True),
        sa.Column("data_volume_mb_min", sa.Float(), nullable=True),
        sa.Column("data_volume_mb_max", sa.Float(), nullable=True),
        sa.Column("disk_free_mb", sa.Float(), nullable=True),
        sa.Column("disk_free_mb_min", sa.Float(), nullable=True),
        sa.Column("disk_free_mb_max", sa.Float(), nullable=True),
        sa.Column("active_jobs", sa.Integer(), nullable=True),
        sa.CheckConstraint("resolution IN ('raw','hourly')", name="ck_monitoring_snapshots_resolution"),
    )
    op.create_index(
        "ix_monitoring_snapshots_resolution_ts", "monitoring_snapshots", ["resolution", "ts"]
    )


def downgrade() -> None:
    op.drop_index("ix_monitoring_snapshots_resolution_ts", table_name="monitoring_snapshots")
    op.drop_table("monitoring_snapshots")
    op.drop_column("jobs", "peak_memory_mb")
