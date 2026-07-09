from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

Engine = Literal["postgresql", "clickhouse", "mssql"]


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
    display_name: str
    engine: Engine
    host: str
    port: int
    database: str
    username: str
    password: str
    extra_params: dict[str, Any] | None = None
    ssl: bool = False


class PatchDatabaseConnectionRequest(BaseModel):
    display_name: str | None = None
    engine: Engine | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    # None == "unchanged" (UI placeholder) — omit or send null to keep the
    # existing encrypted password; send a non-empty string to replace it.
    password: str | None = None
    extra_params: dict[str, Any] | None = None
    ssl: bool | None = None


class TestConnectionResult(BaseModel):
    outcome: Literal["ok", "host_unreachable", "auth_failed", "db_not_found", "error"]
    message: str


class TestDraftConnectionRequest(BaseModel):
    engine: Engine
    host: str
    port: int
    database: str
    username: str
    password: str
    extra_params: dict[str, Any] | None = None
    ssl: bool = False
