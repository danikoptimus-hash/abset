"""Оркестрация Database Connections (DB1) — тот же паттерн, что abkit/jobs.py
и abkit/auth/service.py: guard-проверки + audit_log на уровне сервисной
функции, не в роутере, чтобы будущие вызовы (CLI и т.п.) тоже проходили
через одни и те же проверки."""

from __future__ import annotations

import uuid as uuid_mod
from typing import Any

from abkit.auth.guards import CurrentUser, require_role
from abkit.db.repositories import DatabaseConnectionRepo
from abkit.db_connections.crypto import decrypt_password, encrypt_password
from abkit.db_connections.engines import ConnectionSpec
from abkit.db_connections.testing import ConnectionTestResult, test_connection

_DEFAULT_TEST_TIMEOUT_SEC = 10


def _audit(
    current_user: CurrentUser,
    action: str,
    *,
    object_id: str | None = None,
    object_name: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    from abkit.db.repositories import AuditRepo

    AuditRepo().log(
        action=action, user_id=uuid_mod.UUID(current_user.id), user_email=current_user.email,
        object_type="database_connection", object_id=object_id, object_name=object_name,
        details=details,
    )


def list_connections(current_user: CurrentUser):
    """Editor+ (без чувствительных полей — контролируется схемой ответа в
    роутере, не здесь: пароль в принципе не выходит за пределы password_
    encrypted на этом объекте, decrypt_password не вызывается)."""
    require_role(current_user, "editor")
    return DatabaseConnectionRepo().list_all()


def create_connection(
    current_user: CurrentUser,
    *,
    display_name: str,
    engine: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    extra_params: dict[str, Any] | None,
    ssl: bool,
):
    require_role(current_user, "admin")
    conn = DatabaseConnectionRepo().create(
        display_name=display_name, engine=engine, host=host, port=port, database=database,
        username=username, password_encrypted=encrypt_password(password),
        extra_params=extra_params, ssl=ssl, created_by=uuid_mod.UUID(current_user.id),
    )
    _audit(
        current_user, "db_connection.create",
        object_id=str(conn.id), object_name=display_name,
        details={"engine": engine, "host": host, "port": port, "database": database},
    )
    return conn


def update_connection(
    current_user: CurrentUser,
    conn_id: uuid_mod.UUID,
    *,
    display_name: str | None = None,
    engine: str | None = None,
    host: str | None = None,
    port: int | None = None,
    database: str | None = None,
    username: str | None = None,
    password: str | None = None,
    extra_params: dict[str, Any] | None = None,
    ssl: bool | None = None,
):
    require_role(current_user, "admin")
    password_encrypted = encrypt_password(password) if password else None
    conn = DatabaseConnectionRepo().update(
        conn_id, display_name=display_name, engine=engine, host=host, port=port,
        database=database, username=username, password_encrypted=password_encrypted,
        extra_params=extra_params, ssl=ssl,
    )
    _audit(
        current_user, "db_connection.update", object_id=str(conn.id), object_name=conn.display_name,
        details={"password_changed": password is not None},
    )
    return conn


def delete_connection(current_user: CurrentUser, conn_id: uuid_mod.UUID) -> None:
    require_role(current_user, "admin")
    conn = DatabaseConnectionRepo().get_by_id(conn_id)
    name = conn.display_name if conn is not None else str(conn_id)
    DatabaseConnectionRepo().delete(conn_id)
    _audit(current_user, "db_connection.delete", object_id=str(conn_id), object_name=name)


def _spec_from_row(conn, password_override: str | None = None) -> ConnectionSpec:
    password = password_override if password_override is not None else decrypt_password(conn.password_encrypted)
    return ConnectionSpec(
        engine=conn.engine, host=conn.host, port=conn.port, database=conn.database,
        username=conn.username, password=password, ssl=conn.ssl, extra_params=conn.extra_params,
    )


def test_saved_connection(
    current_user: CurrentUser, conn_id: uuid_mod.UUID, timeout_sec: int = _DEFAULT_TEST_TIMEOUT_SEC
) -> ConnectionTestResult:
    """Test connection (Edit page/list Actions) — decrypts the stored password."""
    require_role(current_user, "admin")
    from abkit import storage

    conn = DatabaseConnectionRepo().get_by_id(conn_id)
    if conn is None:
        raise storage.StorageError(f"Database connection '{conn_id}' not found")
    result = test_connection(_spec_from_row(conn), timeout_sec=timeout_sec)
    _audit(
        current_user, "db_connection.test", object_id=str(conn_id), object_name=conn.display_name,
        details={"outcome": result.outcome},
    )
    return result


def test_draft_connection(
    current_user: CurrentUser,
    *,
    engine: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    ssl: bool,
    extra_params: dict[str, Any] | None,
    timeout_sec: int = _DEFAULT_TEST_TIMEOUT_SEC,
) -> ConnectionTestResult:
    """Test connection button in the "+ Database" modal, before Save exists
    yet — no stored row, no decryption, plaintext password from the form."""
    require_role(current_user, "admin")
    spec = ConnectionSpec(
        engine=engine, host=host, port=port, database=database, username=username,
        password=password, ssl=ssl, extra_params=extra_params,
    )
    result = test_connection(spec, timeout_sec=timeout_sec)
    _audit(
        current_user, "db_connection.test_draft", object_name=f"{host}/{database}",
        details={"engine": engine, "outcome": result.outcome},
    )
    return result
