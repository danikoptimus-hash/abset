"""DI: текущий пользователь из cookie-сессии + guard-зависимости по ролям.

Cookie и верификация токена — те же abkit.auth.tokens/service, что и CLI
(cli_admin.py) — единая модель сессий (см. main.py)."""

from __future__ import annotations

from typing import Callable

from fastapi import Cookie, Depends, Request

from abkit.auth.guards import AuthError, CurrentUser, require_role
from backend.errors import APIError
from backend.jobs import JobRunner

COOKIE_NAME = "abkit_session"


def get_optional_user(abkit_session: str | None = Cookie(default=None)) -> CurrentUser | None:
    from abkit.auth.service import current_user_from_token

    return current_user_from_token(abkit_session)


def get_current_user(user: CurrentUser | None = Depends(get_optional_user)) -> CurrentUser:
    if user is None:
        raise APIError(401, "unauthorized", "Login required")
    return user


def require_min_role(min_role: str) -> Callable[[CurrentUser], CurrentUser]:
    """Фабрика зависимостей: Depends(require_min_role("editor")) — 403, если
    роли не хватает. Использует ТЕ ЖЕ abkit.auth.guards.require_role, что и
    jobs.py — права проверяются идентично независимо от транспорта."""

    def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        try:
            return require_role(user, min_role)
        except AuthError as e:
            raise APIError(403, "forbidden", str(e)) from e

    return _dep


def get_job_runner(request: Request) -> JobRunner:
    """JobRunner создается один раз в lifespan (main.py) и живет в app.state —
    не пересоздается на каждый запрос."""
    return request.app.state.job_runner
