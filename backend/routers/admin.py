"""FRONTEND.md §3.2: admin-only управление пользователями — чтение (R2) +
мутации (R3), тонкая обертка над abkit.auth.service (те же функции, что и
Admin-таб в app.py, включая генерацию временного пароля и запись в audit_log).

Also: admin monitoring panel (resource usage + persistent history) — same
admin-only gate, thin wrapper over abkit.monitoring/abkit.db.repositories.MonitoringRepo."""

from __future__ import annotations

import shutil
import uuid as uuid_mod
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from abkit.auth.guards import AuthError, CurrentUser
from abkit.db.repositories import MonitoringRepo, RepoError, UserRepo
from abkit.db.store import get_data_dir
from abkit.monitoring import MonitoringCollector
from backend.deps import get_monitoring_collector, require_min_role
from backend.errors import APIError
from backend.schemas.admin import (
    CreateUserRequest,
    CreateUserResponse,
    MonitoringCurrentOut,
    MonitoringHistoryOut,
    MonitoringHistoryPoint,
    MonitoringTableSize,
    PatchUserRequest,
    ResetPasswordResponse,
    UserAdminOut,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def _to_out(u) -> UserAdminOut:
    return UserAdminOut(
        id=str(u.id), email=u.email, first_name=u.first_name, last_name=u.last_name,
        role=u.role, is_active=u.is_active,
        must_change_password=u.must_change_password, created_at=u.created_at,
        last_login_at=u.last_login_at,
    )


def _get_user_or_404(user_id: str):
    try:
        parsed_id = uuid_mod.UUID(user_id)
    except ValueError as e:
        raise APIError(422, "validation_error", "Invalid user id") from e
    user = UserRepo().get_by_id(parsed_id)
    if user is None:
        raise APIError(404, "not_found", f"User '{user_id}' not found")
    return user


@router.get("/users", response_model=list[UserAdminOut])
def list_users(user: CurrentUser = Depends(require_min_role("admin"))) -> list[UserAdminOut]:
    return [_to_out(u) for u in UserRepo().list_all()]


@router.post("/users", response_model=CreateUserResponse, status_code=201)
def create_user(
    body: CreateUserRequest, user: CurrentUser = Depends(require_min_role("admin")),
) -> CreateUserResponse:
    from abkit.auth.service import admin_create_user

    try:
        user_id, generated_password = admin_create_user(
            user, email=body.email, first_name=body.first_name, last_name=body.last_name,
            role=body.role, password=body.password,
        )
    except AuthError as e:
        raise APIError(403, "forbidden", str(e)) from e
    except RepoError as e:
        raise APIError(409, "already_exists", str(e)) from e

    created = UserRepo().get_by_id(uuid_mod.UUID(user_id))
    return CreateUserResponse(user=_to_out(created), generated_password=generated_password)


@router.patch("/users/{user_id}", response_model=UserAdminOut)
def patch_user(
    user_id: str, body: PatchUserRequest, user: CurrentUser = Depends(require_min_role("admin")),
) -> UserAdminOut:
    from abkit.auth.service import admin_set_active, admin_set_role, admin_update_name

    target = _get_user_or_404(user_id)
    if body.first_name is not None or body.last_name is not None:
        admin_update_name(
            user, target_email=target.email,
            first_name=body.first_name if body.first_name is not None else target.first_name,
            last_name=body.last_name if body.last_name is not None else target.last_name,
        )
    if body.role is not None:
        admin_set_role(user, target_email=target.email, role=body.role)
    if body.is_active is not None:
        admin_set_active(user, target_email=target.email, is_active=body.is_active)
    return _to_out(UserRepo().get_by_id(target.id))


@router.post("/users/{user_id}/reset-password", response_model=ResetPasswordResponse)
def reset_password(
    user_id: str, user: CurrentUser = Depends(require_min_role("admin")),
) -> ResetPasswordResponse:
    from abkit.auth.service import admin_reset_password

    target = _get_user_or_404(user_id)
    new_password = admin_reset_password(user, target_email=target.email)
    return ResetPasswordResponse(new_password=new_password)


def _current_payload() -> MonitoringCurrentOut:
    repo = MonitoringRepo()
    latest = repo.latest()

    # Static infra info (doesn't meaningfully change while the process
    # runs) — queried fresh here rather than stored in every history point.
    disk_total_mb: float | None = None
    try:
        disk_total_mb = shutil.disk_usage(get_data_dir()).total / (1024 * 1024)
    except OSError:
        pass

    try:
        top_tables = repo.top_tables(limit=10)
    except Exception:
        top_tables = []

    return MonitoringCurrentOut(
        ts=latest.ts if latest else None,
        backend_rss_mb=latest.backend_rss_mb if latest else None,
        db_total_mb=latest.db_total_mb if latest else None,
        data_volume_mb=latest.data_volume_mb if latest else None,
        disk_free_mb=latest.disk_free_mb if latest else None,
        disk_total_mb=disk_total_mb,
        active_jobs=latest.active_jobs if latest else None,
        top_tables=[MonitoringTableSize(**t) for t in top_tables],
    )


@router.get("/monitoring/current", response_model=MonitoringCurrentOut)
def get_monitoring_current(user: CurrentUser = Depends(require_min_role("admin"))) -> MonitoringCurrentOut:
    return _current_payload()


@router.get("/monitoring/history", response_model=MonitoringHistoryOut)
def get_monitoring_history(
    ts_from: datetime = Query(..., alias="from"),
    ts_to: datetime = Query(..., alias="to"),
    resolution: str = Query("raw", pattern="^(raw|hourly)$"),
    user: CurrentUser = Depends(require_min_role("admin")),
) -> MonitoringHistoryOut:
    rows = MonitoringRepo().list_range(resolution=resolution, ts_from=ts_from, ts_to=ts_to)
    return MonitoringHistoryOut(
        resolution=resolution,  # type: ignore[arg-type]
        points=[
            MonitoringHistoryPoint(
                ts=r.ts,
                backend_rss_mb=r.backend_rss_mb,
                db_total_mb=r.db_total_mb,
                data_volume_mb=r.data_volume_mb,
                disk_free_mb=r.disk_free_mb,
                active_jobs=r.active_jobs,
                backend_rss_mb_min=r.backend_rss_mb_min,
                backend_rss_mb_max=r.backend_rss_mb_max,
                db_total_mb_min=r.db_total_mb_min,
                db_total_mb_max=r.db_total_mb_max,
                data_volume_mb_min=r.data_volume_mb_min,
                data_volume_mb_max=r.data_volume_mb_max,
                disk_free_mb_min=r.disk_free_mb_min,
                disk_free_mb_max=r.disk_free_mb_max,
            )
            for r in rows
        ],
    )


@router.post("/monitoring/snapshot-now", response_model=MonitoringCurrentOut)
def force_monitoring_snapshot(
    collector: MonitoringCollector = Depends(get_monitoring_collector),
    user: CurrentUser = Depends(require_min_role("admin")),
) -> MonitoringCurrentOut:
    """Manual refresh — what lets e2e/tests see data without waiting up to
    60s for the timer thread's first regular tick, and generally useful
    whenever "right now, for real" matters more than the up-to-60s-stale
    stored latest."""
    collector.snapshot_now()
    return _current_payload()
