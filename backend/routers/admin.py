"""FRONTEND.md §3.2: admin-only управление пользователями — чтение (R2) +
мутации (R3), тонкая обертка над abkit.auth.service (те же функции, что и
Admin-таб в app.py, включая генерацию временного пароля и запись в audit_log)."""

from __future__ import annotations

import uuid as uuid_mod

from fastapi import APIRouter, Depends

from abkit.auth.guards import AuthError, CurrentUser
from abkit.db.repositories import RepoError, UserRepo
from backend.deps import require_min_role
from backend.errors import APIError
from backend.schemas.admin import (
    CreateUserRequest,
    CreateUserResponse,
    PatchUserRequest,
    ResetPasswordResponse,
    UserAdminOut,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def _to_out(u) -> UserAdminOut:
    return UserAdminOut(
        id=str(u.id), email=u.email, name=u.name, role=u.role, is_active=u.is_active,
        must_change_password=u.must_change_password, created_at=u.created_at,
        last_login_at=u.last_login_at,
    )


def _get_user_or_404(user_id: str):
    try:
        parsed_id = uuid_mod.UUID(user_id)
    except ValueError as e:
        raise APIError(422, "validation_error", "Некорректный идентификатор пользователя") from e
    user = UserRepo().get_by_id(parsed_id)
    if user is None:
        raise APIError(404, "not_found", f"Пользователь '{user_id}' не найден")
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
            user, email=body.email, name=body.name, role=body.role, password=body.password,
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
    if body.name is not None:
        admin_update_name(user, target_email=target.email, name=body.name)
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
