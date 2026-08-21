from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class RunQueryRequest(BaseModel):
    connection_id: str
    sql: str


class RunQueryResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    n_rows: int
    elapsed_ms: int
    truncated: bool
    """Результат обрезан до интерактивного лимита. Фронт обязан показать это
    явно — иначе 1000 строк примут за весь результат."""
    row_limit: int
    warnings: list[str] = []


class SqlLabQueryOut(BaseModel):
    id: str
    connection_id: str | None
    connection_name: str | None
    sql_text: str
    started_at: datetime
    duration_ms: int | None
    n_rows: int | None
    error: str | None


class SqlLabHistoryResponse(BaseModel):
    items: list[SqlLabQueryOut]
