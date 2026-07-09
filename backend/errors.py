"""Единый формат ошибок API (FRONTEND.md §3.1): {"error": {"code","message","details"}}.

AuthError из abkit.auth.guards переиспользуется как есть (те же guard-функции,
что и в jobs.py/app.py) — этот модуль только знает, как превратить ее (и
APIError) в HTTP-ответ нужной формы, без дублирования самой логики прав.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from abkit import storage
from abkit.auth.guards import AuthError


class APIError(Exception):
    """Ошибка уровня API с явным HTTP-статусом и машиночитаемым кодом."""

    def __init__(
        self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def _error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _auth_error_status(exc: AuthError) -> int:
    """require_login поднимает AuthError с этим ТОЧНЫМ текстом при отсутствии
    сессии — 401 (не аутентифицирован). Любая другая AuthError (недостаточно
    прав / не владелец) — 403 (аутентифицирован, но запрещено)."""
    return 401 if str(exc) == "Login required" else 403


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def _handle_api_error(request: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(AuthError)
    async def _handle_auth_error(request: Request, exc: AuthError) -> JSONResponse:
        status_code = _auth_error_status(exc)
        code = "unauthorized" if status_code == 401 else "forbidden"
        return JSONResponse(status_code=status_code, content=_error_body(code, str(exc)))

    @app.exception_handler(storage.StorageError)
    async def _handle_storage_error(request: Request, exc: storage.StorageError) -> JSONResponse:
        # StorageError/RepoError/DbStoreError (abkit/db/repositories.py,
        # abkit/db/store.py) — почти всегда "эксперимент/датасет не найден" в
        # синхронных мутациях (status/rename/delete/blocks); сообщение уже
        # человекочитаемое на русском, просто оборачиваем в конверт ошибок.
        # POST /design — асинхронная job, там такие ошибки идут в job.error,
        # а не сюда (см. backend/jobs/runner.py).
        return JSONResponse(status_code=404, content=_error_body("not_found", str(exc)))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_body(
                "validation_error", "Invalid request data", {"errors": exc.errors()}
            ),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Не протекает деталями внутренних исключений наружу — только generic
        # сообщение; структурированный traceback уходит в лог (см. main.py).
        from abkit.logging_config import get_logger

        get_logger("backend.errors").error("unhandled_exception", exc_info=True, path=str(request.url))
        return JSONResponse(
            status_code=500,
            content=_error_body("internal_error", "Internal server error"),
        )
