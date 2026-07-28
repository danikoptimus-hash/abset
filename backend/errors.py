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

    from abkit.jobs import DatasetInUseError

    @app.exception_handler(DatasetInUseError)
    async def _handle_dataset_in_use_error(request: Request, exc: DatasetInUseError) -> JSONResponse:
        # DELETE /datasets/{id} without confirm="DELETE" when experiments
        # still use it (UX package, Datasets §2.2) — frontend shows the
        # experiment list + requires typed DELETE, same discipline as
        # DELETE /experiments/{name}.
        return JSONResponse(
            status_code=400,
            content=_error_body(
                "confirmation_required", str(exc), {"experiments": exc.experiment_names}
            ),
        )

    from abkit.jobs import ProtectedColumnError

    @app.exception_handler(ProtectedColumnError)
    async def _handle_protected_column_error(request: Request, exc: ProtectedColumnError) -> JSONResponse:
        # PATCH /datasets/{id} trying to exclude a column that is an
        # experiment's unit/ID column (Part 2, removable columns) — the join
        # key, hard-blocked from removal. The frontend already disables the
        # remove control for such columns; this is the server-side backstop.
        return JSONResponse(
            status_code=400,
            content=_error_body(
                "protected_column", str(exc),
                {"column": exc.column, "experiments": exc.experiment_names},
            ),
        )

    from abkit.jobs import TagNameConflictError

    @app.exception_handler(TagNameConflictError)
    async def _handle_tag_name_conflict_error(request: Request, exc: TagNameConflictError) -> JSONResponse:
        # PATCH /tags/{id} renaming into a name that collides
        # case-insensitively with a DIFFERENT existing tag (tag management
        # page §2.1) — the frontend uses existing_tag_id/name to offer
        # Merge instead of just showing a generic error.
        return JSONResponse(
            status_code=409,
            content=_error_body(
                "tag_name_conflict", str(exc),
                {"existing_tag_id": exc.existing_tag_id, "existing_tag_name": exc.existing_tag_name},
            ),
        )

    from abkit.jobs import FolderNameConflictError

    @app.exception_handler(FolderNameConflictError)
    async def _handle_folder_name_conflict_error(request: Request, exc: FolderNameConflictError) -> JSONResponse:
        # POST /folders or PATCH /folders/{id} colliding with an existing
        # folder's exact name (item 5, folders package) — no merge
        # follow-up like tags, just a plain rejection.
        return JSONResponse(status_code=400, content=_error_body("folder_name_conflict", str(exc)))

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
        # error_id (короткий uuid) — единственная связь между тем, что видит
        # пользователь, и строкой в логе с полным traceback: голое
        # "Internal processing error" без него было бесполезно для
        # диагностики (найдено на баге с кириллицей в Content-Disposition —
        # без error_id пришлось вручную grep'ать логи по времени запроса).
        # В message, а не только в details, чтобы код был виден прямо в
        # тосте UI, не требуя от фронтенда отдельно читать details.
        import uuid

        from abkit.logging_config import get_logger

        error_id = uuid.uuid4().hex[:8]
        get_logger("backend.errors").error(
            "unhandled_exception", exc_info=True, path=str(request.url), error_id=error_id
        )
        return JSONResponse(
            status_code=500,
            content=_error_body(
                "internal_error", f"Internal processing error (ref: {error_id})", {"error_id": error_id}
            ),
        )
