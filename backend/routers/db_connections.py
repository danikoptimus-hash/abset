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
    TestConnectionResult,
    TestDraftConnectionRequest,
)

router = APIRouter(prefix="/admin/db-connections", tags=["db-connections"])


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
