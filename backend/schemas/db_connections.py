from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, StringConstraints

Engine = Literal["postgresql", "clickhouse", "mssql"]

# Root cause of a real "Test connection fails on host=postgres" bug: a
# trailing space typed/pasted into the Host field (or database/username)
# turns a perfectly valid value into an unresolvable one ("postgres " is not
# "postgres") — DNS resolution correctly fails on the space-padded string,
# but the resulting "dns_error" reads as a false positive since the host
# LOOKS right. Strip whitespace at the schema boundary so it can never reach
# the connection layer at all, for any of the three write paths (create,
# patch, test-draft). Never applied to `password` — a password's leading/
# trailing whitespace can be intentional and must round-trip byte-for-byte.
TrimmedStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DatabaseConnectionOut(BaseModel):
    """Never includes the password — write-only field (DB1)."""

    id: str
    display_name: str
    engine: Engine
    host: str
    port: int
    database: str
    username: str
    extra_params: dict[str, Any] | None
    ssl: bool
    created_at: datetime
    updated_at: datetime


class CreateDatabaseConnectionRequest(BaseModel):
    display_name: TrimmedStr
    engine: Engine
    host: TrimmedStr
    port: int
    database: TrimmedStr
    username: TrimmedStr
    password: str
    extra_params: dict[str, Any] | None = None
    ssl: bool = False


class PatchDatabaseConnectionRequest(BaseModel):
    display_name: TrimmedStr | None = None
    engine: Engine | None = None
    host: TrimmedStr | None = None
    port: int | None = None
    database: TrimmedStr | None = None
    username: TrimmedStr | None = None
    # None == "unchanged" (UI placeholder) — omit or send null to keep the
    # existing encrypted password; send a non-empty string to replace it.
    password: str | None = None
    extra_params: dict[str, Any] | None = None
    ssl: bool | None = None


class TestConnectionResult(BaseModel):
    outcome: Literal["ok", "dns_error", "tcp_timeout", "auth_failed", "db_not_found", "error"]
    message: str


class TestDraftConnectionRequest(BaseModel):
    engine: Engine
    host: TrimmedStr
    port: int
    database: TrimmedStr
    username: TrimmedStr
    password: str
    extra_params: dict[str, Any] | None = None
    ssl: bool = False


class SchemasResponse(BaseModel):
    schemas: list[str]
    # public/dbo/default, если такая схема есть у этого подключения. Форма
    # выбирает ее сама при первом заходе — правило зависит от движка, поэтому
    # считается на сервере (abkit/db_connections/introspection.py), а не
    # угадывается тремя формами по отдельности.
    default_schema: str | None = None


class TablesResponse(BaseModel):
    tables: list[str]


class SqlPreviewRequest(BaseModel):
    sql: str


class SqlPreviewResponse(BaseModel):
    columns: list[str]
    dtypes: dict[str, str]
    rows: list[dict[str, Any]]
