"""Подписанные токены сессии (JWT, HS256) — DOCKER.md §4.2. Секрет —
ABKIT_SECRET_KEY из окружения; обязателен и провалидирован в серверном режиме
(DOCKER.md §3: приложение не должно стартовать без него или с дефолтным
значением "change-me...")."""

from __future__ import annotations

import os
import time
from typing import Any

import jwt

_ALGORITHM = "HS256"
_DEFAULT_SECRET_PREFIX = "change-me"


class TokenError(Exception):
    """Токен сессии отсутствует, невалиден или истек."""


def get_secret_key() -> str:
    secret = os.environ.get("ABKIT_SECRET_KEY")
    if not secret or secret.startswith(_DEFAULT_SECRET_PREFIX):
        raise TokenError(
            "ABKIT_SECRET_KEY is not set or uses the default "
            "'change-me...' value — required for server mode (see .env.example, "
            "generate with: openssl rand -hex 32)"
        )
    return secret


def create_session_token(
    *,
    user_id: str,
    email: str,
    role: str,
    lifetime_hours: float = 72,
    secret_key: str | None = None,
) -> str:
    secret = secret_key or get_secret_key()
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "iat": now,
        "exp": now + int(lifetime_hours * 3600),
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def verify_session_token(token: str, secret_key: str | None = None) -> dict[str, Any]:
    secret = secret_key or get_secret_key()
    try:
        return jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError as e:
        raise TokenError("Session expired, please log in again") from e
    except jwt.InvalidTokenError as e:
        raise TokenError("Invalid session token") from e
