"""Guard-функции (DOCKER.md §4.1/§8.2): require_login/require_role/
require_owner_or_admin/require_admin.

Критично: вызываются не только из app.py (перед рендером таба/кнопки), но и из
abkit/jobs.py — перед каждой мутацией. Это гарантирует, что Viewer не сможет
вызвать мутацию даже прямым вызовом сервисной функции в обход UI (критерий
готовности этапа D2, DOCKER.md §12)."""

from __future__ import annotations

from dataclasses import dataclass

_ROLE_ORDER = {"viewer": 0, "editor": 1, "admin": 2}


class AuthError(PermissionError):
    """Нет активной сессии или недостаточно прав."""


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    name: str
    role: str
    must_change_password: bool = False


def require_login(current_user: CurrentUser | None) -> CurrentUser:
    if current_user is None:
        raise AuthError("Login required")
    return current_user


def require_role(current_user: CurrentUser | None, min_role: str) -> CurrentUser:
    """min_role: 'viewer' | 'editor' | 'admin' — current_user.role должна быть
    этой роли или выше по DOCKER.md §4.1 (viewer < editor < admin)."""
    require_login(current_user)
    if _ROLE_ORDER[current_user.role] < _ROLE_ORDER[min_role]:
        raise AuthError(
            f"Insufficient permissions: role '{min_role}' or higher required, you have '{current_user.role}'"
        )
    return current_user


def require_admin(current_user: CurrentUser | None) -> CurrentUser:
    return require_role(current_user, "admin")
