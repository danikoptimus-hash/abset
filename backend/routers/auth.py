"""POST /auth/login, POST /auth/logout, GET /auth/me, POST /auth/change-password,
GET /auth/config, POST /auth/register (FRONTEND.md §3.2) — тонкая обертка
HTTP+cookie над abkit.auth.service."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Response

from abkit.auth.guards import AuthError, CurrentUser
from abkit.db.repositories import RepoError
from backend.deps import COOKIE_NAME, get_current_user
from backend.errors import APIError
from backend.schemas.auth import ChangePasswordRequest, LoginRequest, RegisterRequest, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/config")
def config() -> dict[str, bool]:
    """Публичный (без авторизации) — фронту нужно знать до логина, показывать
    ли ссылку/форму 'Регистрация' на странице логина (DOCKER.md §4.2)."""
    return {
        "self_registration_enabled": os.environ.get(
            "ABKIT_ALLOW_SELF_REGISTRATION", "false"
        ).lower() == "true",
    }


def _cookie_max_age() -> int:
    hours = float(os.environ.get("ABKIT_SESSION_LIFETIME_HOURS", "72"))
    return int(hours * 3600)


def _cookie_secure() -> bool:
    # По умолчанию False — большинство локальных/докер-деплоев без TLS (см.
    # docker/README.md, раздел TLS — опционален). Включать через
    # ABKIT_COOKIE_SECURE=true, когда nginx терминирует TLS перед backend.
    return os.environ.get("ABKIT_COOKIE_SECURE", "false").lower() == "true"


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=_cookie_max_age(),
        httponly=True,
        secure=_cookie_secure(),
        # Strict, не Lax/double-submit-token: frontend и /api/* всегда на одном
        # origin через nginx (FRONTEND.md §2) — кросс-сайтовых запросов с этим
        # cookie в легитимных сценариях не бывает, значит Strict ничего не
        # ломает и не требует отдельного CSRF-токена на клиенте (выбор из
        # FRONTEND.md §3.1 "выбери и зафиксируй решение в комментарии").
        samesite="strict",
        path="/",
    )


@router.post("/login", response_model=UserOut)
def login(body: LoginRequest, response: Response) -> UserOut:
    from abkit.auth.service import current_user_from_token, login as auth_login

    try:
        token = auth_login(body.email, body.password)
    except AuthError as e:
        raise APIError(401, "invalid_credentials", str(e)) from e

    _set_session_cookie(response, token)
    user = current_user_from_token(token)
    assert user is not None  # только что сами создали валидный токен
    return UserOut.from_current_user(user)


@router.post("/register", status_code=201)
def register(body: RegisterRequest) -> dict[str, bool]:
    """Самостоятельная регистрация (DOCKER.md §4.2) — только когда
    ABKIT_ALLOW_SELF_REGISTRATION=true, иначе 403 (self_register сама это
    проверяет и бросает AuthError, тут просто транспорт). Как и в legacy
    (app.py _render_self_registration_form) — БЕЗ автологина, пользователь
    входит отдельным шагом на той же странице логина."""
    from abkit.auth.service import self_register

    try:
        self_register(
            email=body.email, first_name=body.first_name, last_name=body.last_name,
            password=body.password,
        )
    except AuthError as e:
        raise APIError(403, "registration_disabled", str(e)) from e
    except RepoError as e:
        raise APIError(409, "already_exists", str(e)) from e

    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser = Depends(get_current_user)) -> UserOut:
    return UserOut.from_current_user(user)


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest, user: CurrentUser = Depends(get_current_user)
) -> dict[str, bool]:
    from abkit.auth.service import change_own_password

    try:
        change_own_password(user, body.old_password, body.new_password)
    except AuthError as e:
        raise APIError(400, "invalid_password", str(e)) from e
    return {"ok": True}
