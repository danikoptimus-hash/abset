"""Оркестрация логина/пользователей: связывает UserRepo (D1) + passwords +
tokens + guards. Единая точка входа для app.py и cli_admin.py."""

from __future__ import annotations

import os
import secrets
import uuid as uuid_mod
from datetime import datetime, timezone

from abkit.auth.guards import AuthError, CurrentUser, require_admin
from abkit.auth.passwords import hash_password, verify_password
from abkit.auth.tokens import TokenError, create_session_token, verify_session_token
from abkit.db.repositories import UserRepo

_MAX_LOGIN_ATTEMPTS = 5
_LOCKOUT_MINUTES = 15
_CLI_ACTOR_EMAIL = "cli:abkit-admin"


def _session_lifetime_hours() -> float:
    return float(os.environ.get("ABKIT_SESSION_LIFETIME_HOURS", "72"))


def _audit(
    *,
    action: str,
    user_id: uuid_mod.UUID | None = None,
    user_email: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    object_name: str | None = None,
    details: dict | None = None,
) -> None:
    """Пишется на уровне сервисной функции (не UI), чтобы CLI-действия тоже
    попадали в аудит (DOCKER.md §6.2) — в т.ч. когда acting_user=None
    (доверенный abkit-admin), тогда user_id=None, user_email='cli:abkit-admin'."""
    from abkit.db.repositories import AuditRepo

    AuditRepo().log(
        action=action,
        user_id=user_id,
        user_email=user_email,
        object_type=object_type,
        object_id=object_id,
        object_name=object_name,
        details=details,
    )


def login(email: str, password: str) -> str:
    """Возвращает токен сессии при успехе; бросает AuthError иначе.

    Блокировка перебора (DOCKER.md §4.2): 5 неудачных попыток подряд -> 15 минут
    блокировки для этого email, хранится в БД (UserRepo.record_login_failure),
    не в памяти процесса — переживает рестарт/несколько воркеров.
    """
    repo = UserRepo()
    user = repo.get_by_email(email)
    if user is None:
        _audit(action="auth.login_failed", user_email=email, details={"reason": "unknown_email"})
        raise AuthError("Invalid email or password")

    if user.locked_until is not None and user.locked_until > datetime.now(timezone.utc):
        _audit(
            action="auth.login_failed", user_id=user.id, user_email=email,
            details={"reason": "locked_out"},
        )
        raise AuthError(
            f"Too many failed login attempts. Try again after "
            f"{user.locked_until.strftime('%H:%M UTC')}"
        )

    if not user.is_active:
        _audit(
            action="auth.login_failed", user_id=user.id, user_email=email,
            details={"reason": "inactive"},
        )
        raise AuthError("This account has been deactivated by an administrator")

    if not verify_password(password, user.password_hash):
        repo.record_login_failure(email, max_attempts=_MAX_LOGIN_ATTEMPTS, lockout_minutes=_LOCKOUT_MINUTES)
        _audit(
            action="auth.login_failed", user_id=user.id, user_email=email,
            details={"reason": "wrong_password"},
        )
        raise AuthError("Invalid email or password")

    repo.record_login_success(user.id)
    _audit(action="auth.login", user_id=user.id, user_email=user.email)
    return create_session_token(
        user_id=str(user.id),
        email=user.email,
        role=user.role,
        lifetime_hours=_session_lifetime_hours(),
    )


def current_user_from_token(token: str | None) -> CurrentUser | None:
    """None, если токена нет/невалиден/юзер деактивирован — вызывающая сторона
    (app.py) в этом случае должна показать экран логина."""
    if not token:
        return None
    try:
        payload = verify_session_token(token)
    except TokenError:
        return None
    try:
        user_id = uuid_mod.UUID(payload["sub"])
    except (KeyError, ValueError):
        return None
    user = UserRepo().get_by_id(user_id)
    if user is None or not user.is_active:
        return None
    return CurrentUser(
        id=str(user.id),
        email=user.email,
        name=user.full_name,
        role=user.role,
        must_change_password=user.must_change_password,
    )


def change_own_password(current_user: CurrentUser, old_password: str, new_password: str) -> None:
    repo = UserRepo()
    user = repo.get_by_id(uuid_mod.UUID(current_user.id))
    if user is None or not verify_password(old_password, user.password_hash):
        raise AuthError("Current password is incorrect")
    repo.set_password_hash(user.id, hash_password(new_password), must_change_password=False)
    _audit(
        action="auth.password_changed", user_id=user.id, user_email=user.email,
        object_type="user", object_id=str(user.id), object_name=user.email,
    )


def _generate_temp_password() -> str:
    return secrets.token_urlsafe(12)


def admin_create_user(
    acting_user: CurrentUser | None,
    *,
    email: str,
    first_name: str,
    last_name: str = "",
    role: str,
    password: str | None = None,
) -> tuple[str, str]:
    """acting_user=None допустим только для доверенного CLI (abkit-admin,
    запущенного внутри контейнера) — bootstrap первого админа именно так и
    происходит (аналог `superset fab create-admin`). Из UI acting_user всегда
    передается и должен быть Admin."""
    if acting_user is not None:
        require_admin(acting_user)
    generated = password or _generate_temp_password()
    user_id = UserRepo().create(
        email=email,
        first_name=first_name,
        last_name=last_name,
        password_hash=hash_password(generated),
        role=role,
        must_change_password=password is None,
    )
    _audit(
        action="user.create",
        user_id=uuid_mod.UUID(acting_user.id) if acting_user is not None else None,
        user_email=acting_user.email if acting_user is not None else _CLI_ACTOR_EMAIL,
        object_type="user", object_id=str(user_id), object_name=email,
        details={"role": role},
    )
    return str(user_id), generated


def admin_reset_password(
    acting_user: CurrentUser | None, *, target_email: str, new_password: str | None = None
) -> str:
    if acting_user is not None:
        require_admin(acting_user)
    user = UserRepo().get_by_email(target_email)
    if user is None:
        raise AuthError(f"User '{target_email}' not found")
    generated = new_password or _generate_temp_password()
    UserRepo().set_password_hash(user.id, hash_password(generated), must_change_password=True)
    _audit(
        action="user.password_reset",
        user_id=uuid_mod.UUID(acting_user.id) if acting_user is not None else None,
        user_email=acting_user.email if acting_user is not None else _CLI_ACTOR_EMAIL,
        object_type="user", object_id=str(user.id), object_name=target_email,
    )
    return generated


def admin_set_role(acting_user: CurrentUser, *, target_email: str, role: str) -> None:
    require_admin(acting_user)
    user = UserRepo().get_by_email(target_email)
    if user is None:
        raise AuthError(f"User '{target_email}' not found")
    old_role = user.role
    UserRepo().update_role(user.id, role)
    _audit(
        action="user.role_change", user_id=uuid_mod.UUID(acting_user.id), user_email=acting_user.email,
        object_type="user", object_id=str(user.id), object_name=target_email,
        details={"from": old_role, "to": role},
    )


def admin_set_active(acting_user: CurrentUser, *, target_email: str, is_active: bool) -> None:
    require_admin(acting_user)
    user = UserRepo().get_by_email(target_email)
    if user is None:
        raise AuthError(f"User '{target_email}' not found")
    UserRepo().set_active(user.id, is_active)
    _audit(
        action="user.active_change", user_id=uuid_mod.UUID(acting_user.id), user_email=acting_user.email,
        object_type="user", object_id=str(user.id), object_name=target_email,
        details={"is_active": is_active},
    )


def admin_update_name(
    acting_user: CurrentUser, *, target_email: str, first_name: str, last_name: str
) -> None:
    """Email намеренно не редактируется здесь — это логин пользователя, смена
    требует отдельной проверки уникальности и переизобретения токена сессии,
    что выходит за рамки формы редактирования (UI показывает email как
    read-only)."""
    require_admin(acting_user)
    user = UserRepo().get_by_email(target_email)
    if user is None:
        raise AuthError(f"User '{target_email}' not found")
    old_first_name, old_last_name = user.first_name, user.last_name
    UserRepo().update_name(user.id, first_name, last_name)
    _audit(
        action="user.name_change", user_id=uuid_mod.UUID(acting_user.id), user_email=acting_user.email,
        object_type="user", object_id=str(user.id), object_name=target_email,
        details={
            "from": f"{old_first_name} {old_last_name}".strip(),
            "to": f"{first_name} {last_name}".strip(),
        },
    )


def self_register(*, email: str, first_name: str, last_name: str = "", password: str) -> str:
    """DOCKER.md §4.2: ABKIT_ALLOW_SELF_REGISTRATION=true включает страницу
    самостоятельной регистрации, новый пользователь получает роль Viewer."""
    if os.environ.get("ABKIT_ALLOW_SELF_REGISTRATION", "false").lower() != "true":
        raise AuthError("Self-registration is disabled")
    user_id = UserRepo().create(
        email=email, first_name=first_name, last_name=last_name,
        password_hash=hash_password(password), role="viewer",
    )
    _audit(
        action="user.create", user_id=user_id, user_email=email,
        object_type="user", object_id=str(user_id), object_name=email,
        details={"role": "viewer", "self_registered": True},
    )
    return str(user_id)
