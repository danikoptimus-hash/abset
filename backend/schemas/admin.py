from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class UserAdminOut(BaseModel):
    id: str
    email: str
    first_name: str
    last_name: str
    role: str
    is_active: bool
    must_change_password: bool
    created_at: datetime
    last_login_at: datetime | None


class CreateUserRequest(BaseModel):
    email: str
    first_name: str
    last_name: str = ""
    role: str
    password: str | None = None


class CreateUserResponse(BaseModel):
    user: UserAdminOut
    generated_password: str


class PatchUserRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    role: str | None = None
    is_active: bool | None = None


class ResetPasswordResponse(BaseModel):
    new_password: str


class MonitoringTableSize(BaseModel):
    table_name: str
    size_bytes: int


class MonitoringCurrentOut(BaseModel):
    """Admin monitoring panel — latest stored snapshot (up to
    SNAPSHOT_INTERVAL_SECONDS=60s stale, same cadence the history chart
    uses, so "current" always agrees with the most recent history point)
    plus two things queried fresh on every call rather than stored:
    disk_total_mb (static infra info, not worth a history column) and
    top_tables (a "right now" breakdown, not a time series)."""

    ts: datetime | None
    backend_rss_mb: float | None
    db_total_mb: float | None
    data_volume_mb: float | None
    disk_free_mb: float | None
    disk_total_mb: float | None
    active_jobs: int | None
    top_tables: list[MonitoringTableSize]


class MonitoringHistoryPoint(BaseModel):
    ts: datetime
    backend_rss_mb: float | None
    db_total_mb: float | None
    data_volume_mb: float | None
    disk_free_mb: float | None
    active_jobs: int | None
    # Populated only for resolution=hourly points — null on 'raw' ones
    # (nothing to aggregate over a single 60s sample).
    backend_rss_mb_min: float | None = None
    backend_rss_mb_max: float | None = None
    db_total_mb_min: float | None = None
    db_total_mb_max: float | None = None
    data_volume_mb_min: float | None = None
    data_volume_mb_max: float | None = None
    disk_free_mb_min: float | None = None
    disk_free_mb_max: float | None = None


class MonitoringHistoryOut(BaseModel):
    resolution: Literal["raw", "hourly"]
    points: list[MonitoringHistoryPoint]
