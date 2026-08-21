"""SQL Lab: интерактивный прогон запроса + личная история (Editor+).

Синхронный эндпоинт, а не job: у интерактивного запроса короткий бюджет
(ABKIT_SQL_LAB_TIMEOUT_SEC, по умолчанию 60с) и ограниченный результат
(1000 строк) — заводить ради этого фоновую джобу с поллингом значило бы
усложнить и клиент, и сервер ради операции, которая должна ощущаться как
мгновенная. Длинные операции (материализация датасета) как были джобами, так
и остаются.

Права — Editor+, ровно как у уже существующего POST /db-connections/{id}/
preview: SQL Lab не дает никаких НОВЫХ прав, он лишь удобнее показывает то,
что и так было доступно (тот же SELECT-only гард, те же подключения).
"""

from __future__ import annotations

import uuid as uuid_mod

from fastapi import APIRouter, Depends

from abkit.auth.guards import CurrentUser
from abkit.db.repositories import DatabaseConnectionRepo, SqlLabQueryRepo
from abkit.db_connections.sql_dataset import SqlExecutionError
from abkit.db_connections.sql_guard import SqlValidationError
from abkit.db_connections.sql_lab import INTERACTIVE_ROW_LIMIT, execute_interactive
from backend.deps import require_min_role
from backend.errors import APIError
from backend.schemas.sql_lab import (
    RunQueryRequest,
    RunQueryResponse,
    SqlLabHistoryResponse,
    SqlLabQueryOut,
)

router = APIRouter(prefix="/sql-lab", tags=["sql-lab"])


def _to_out(q) -> SqlLabQueryOut:
    return SqlLabQueryOut(
        id=str(q.id),
        connection_id=str(q.connection_id) if q.connection_id else None,
        connection_name=q.connection_name,
        sql_text=q.sql_text,
        started_at=q.started_at,
        duration_ms=q.duration_ms,
        n_rows=q.n_rows,
        error=q.error,
    )


@router.post("/run", response_model=RunQueryResponse)
def run_query(
    body: RunQueryRequest, user: CurrentUser = Depends(require_min_role("editor")),
) -> RunQueryResponse:
    from abkit.jobs import _connection_spec

    try:
        conn_uuid = uuid_mod.UUID(body.connection_id)
    except ValueError as e:
        raise APIError(422, "validation_error", "Invalid connection id") from e

    conn_row = DatabaseConnectionRepo().get_by_id(conn_uuid)
    if conn_row is None:
        raise APIError(404, "not_found", f"Database connection '{body.connection_id}' not found")

    repo = SqlLabQueryRepo()
    user_uuid = uuid_mod.UUID(user.id)

    def _record(duration_ms: int | None, n_rows: int | None, error: str | None) -> None:
        # История пишется и для УПАВШИХ запросов — к ним возвращаются чаще
        # всего («что я там написал, что оно ругнулось?»). Ошибка записи в
        # историю не должна ронять сам ответ: результат запроса пользователю
        # важнее, чем строчка в журнале.
        try:
            repo.record(
                user_id=user_uuid,
                connection_id=conn_uuid,
                connection_name=conn_row.display_name,
                sql_text=body.sql,
                duration_ms=duration_ms,
                n_rows=n_rows,
                error=error,
            )
        except Exception:
            pass

    try:
        result = execute_interactive(_connection_spec(conn_row), body.sql)
    except SqlValidationError as e:
        _record(None, None, str(e))
        raise APIError(422, "sql_validation_error", str(e)) from e
    except SqlExecutionError as e:
        _record(None, None, str(e))
        raise APIError(422, "sql_execution_error", str(e)) from e

    _record(result.elapsed_ms, result.n_rows, None)
    return RunQueryResponse(
        columns=result.columns,
        rows=result.rows,
        n_rows=result.n_rows,
        elapsed_ms=result.elapsed_ms,
        truncated=result.truncated,
        row_limit=INTERACTIVE_ROW_LIMIT,
        warnings=result.warnings,
    )


@router.get("/history", response_model=SqlLabHistoryResponse)
def history(user: CurrentUser = Depends(require_min_role("editor"))) -> SqlLabHistoryResponse:
    """Только СВОЯ история — user_id берется из сессии и не принимается
    параметром: чужой SQL (имена таблиц, условия) читать нельзя никому,
    включая админа."""
    rows = SqlLabQueryRepo().list_for_user(uuid_mod.UUID(user.id))
    return SqlLabHistoryResponse(items=[_to_out(q) for q in rows])


@router.delete("/history", status_code=204)
def clear_history(user: CurrentUser = Depends(require_min_role("editor"))) -> None:
    SqlLabQueryRepo().clear_for_user(uuid_mod.UUID(user.id))
