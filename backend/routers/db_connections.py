"""DB1 (CLAUDE.md, Database Connections feature): admin CRUD + test for
external database connections. GET is allowed for editor+ (needed to pick a
connection when creating a dataset from SQL, DB2) — every mutation and the
test-connection action are admin-only; both levels are enforced twice (the
router Depends AND abkit.db_connections.service's own require_role), same
defense-in-depth pattern as backend/routers/admin.py + abkit/auth/service.py."""

from __future__ import annotations

import uuid as uuid_mod

from fastapi import APIRouter, Depends

from abkit.auth.guards import CurrentUser
from abkit.db_connections import service
from backend.deps import require_min_role
from backend.errors import APIError
from backend.schemas.db_connections import (
    CreateDatabaseConnectionRequest,
    DatabaseConnectionOut,
    PatchDatabaseConnectionRequest,
    SchemasResponse,
    SqlPreviewRequest,
    SqlPreviewResponse,
    TablesResponse,
    TestConnectionResult,
    TestDraftConnectionRequest,
)

router = APIRouter(prefix="/admin/db-connections", tags=["db-connections"])
# Not admin-gated: used from the Datasets page's "From SQL" flow (DB2), by
# any editor+ building a dataset — separate from connection management.
public_router = APIRouter(prefix="/db-connections", tags=["db-connections"])


def _to_out(c) -> DatabaseConnectionOut:
    return DatabaseConnectionOut(
        id=str(c.id), display_name=c.display_name, engine=c.engine, host=c.host, port=c.port,
        database=c.database, username=c.username, extra_params=c.extra_params, ssl=c.ssl,
        created_at=c.created_at, updated_at=c.updated_at,
    )


def _parse_id(conn_id: str) -> uuid_mod.UUID:
    try:
        return uuid_mod.UUID(conn_id)
    except ValueError as e:
        raise APIError(422, "validation_error", "Invalid connection id") from e


@router.get("", response_model=list[DatabaseConnectionOut])
def list_db_connections(
    user: CurrentUser = Depends(require_min_role("editor")),
) -> list[DatabaseConnectionOut]:
    return [_to_out(c) for c in service.list_connections(user)]


@router.post("", response_model=DatabaseConnectionOut, status_code=201)
def create_db_connection(
    body: CreateDatabaseConnectionRequest, user: CurrentUser = Depends(require_min_role("admin")),
) -> DatabaseConnectionOut:
    conn = service.create_connection(
        user, display_name=body.display_name, engine=body.engine, host=body.host, port=body.port,
        database=body.database, username=body.username, password=body.password,
        extra_params=body.extra_params, ssl=body.ssl,
    )
    return _to_out(conn)


@router.patch("/{conn_id}", response_model=DatabaseConnectionOut)
def patch_db_connection(
    conn_id: str, body: PatchDatabaseConnectionRequest,
    user: CurrentUser = Depends(require_min_role("admin")),
) -> DatabaseConnectionOut:
    conn = service.update_connection(
        user, _parse_id(conn_id), display_name=body.display_name, engine=body.engine,
        host=body.host, port=body.port, database=body.database, username=body.username,
        password=body.password, extra_params=body.extra_params, ssl=body.ssl,
    )
    return _to_out(conn)


@router.delete("/{conn_id}", status_code=204)
def delete_db_connection(
    conn_id: str, user: CurrentUser = Depends(require_min_role("admin")),
) -> None:
    service.delete_connection(user, _parse_id(conn_id))


@router.post("/{conn_id}/test", response_model=TestConnectionResult)
def test_db_connection(
    conn_id: str, user: CurrentUser = Depends(require_min_role("admin")),
) -> TestConnectionResult:
    result = service.test_saved_connection(user, _parse_id(conn_id))
    return TestConnectionResult(outcome=result.outcome, message=result.message)


@router.post("/test-draft", response_model=TestConnectionResult)
def test_draft_db_connection(
    body: TestDraftConnectionRequest, user: CurrentUser = Depends(require_min_role("admin")),
) -> TestConnectionResult:
    """"Test connection" inline in the "+ Database" modal, before Save —
    tests the form's current values without persisting anything."""
    result = service.test_draft_connection(
        user, engine=body.engine, host=body.host, port=body.port, database=body.database,
        username=body.username, password=body.password, ssl=body.ssl, extra_params=body.extra_params,
    )
    return TestConnectionResult(outcome=result.outcome, message=result.message)


@public_router.get("/{conn_id}/schemas", response_model=SchemasResponse)
def list_connection_schemas(
    conn_id: str, refresh: bool = False, user: CurrentUser = Depends(require_min_role("editor")),
) -> SchemasResponse:
    from abkit.db_connections.sql_dataset import SqlExecutionError

    try:
        schemas, default = service.list_connection_schemas(user, _parse_id(conn_id), force_refresh=refresh)
    except SqlExecutionError as e:
        raise APIError(422, "sql_execution_error", str(e)) from e
    return SchemasResponse(schemas=schemas, default_schema=default)


@public_router.get("/{conn_id}/schemas/{schema}/tables", response_model=TablesResponse)
def list_connection_tables(
    conn_id: str, schema: str, refresh: bool = False, user: CurrentUser = Depends(require_min_role("editor")),
) -> TablesResponse:
    from abkit.db_connections.sql_dataset import SqlExecutionError

    try:
        tables = service.list_connection_tables(user, _parse_id(conn_id), schema, force_refresh=refresh)
    except SqlExecutionError as e:
        raise APIError(422, "sql_execution_error", str(e)) from e
    return TablesResponse(tables=tables)


@public_router.post("/{conn_id}/preview", response_model=SqlPreviewResponse)
def preview_connection_sql(
    conn_id: str, body: SqlPreviewRequest, user: CurrentUser = Depends(require_min_role("editor")),
) -> SqlPreviewResponse:
    import pandas as pd

    from abkit.db_connections.sql_dataset import SqlExecutionError
    from abkit.db_connections.sql_guard import SqlValidationError

    try:
        df = service.preview_connection_sql(user, _parse_id(conn_id), body.sql)
    except SqlValidationError as e:
        raise APIError(422, "sql_validation_error", str(e)) from e
    except SqlExecutionError as e:
        raise APIError(422, "sql_execution_error", str(e)) from e
    df = df.where(pd.notnull(df), None)
    return SqlPreviewResponse(
        columns=list(df.columns), dtypes={c: str(t) for c, t in df.dtypes.items()},
        rows=df.to_dict(orient="records"),
    )
