"""DB1 (CLAUDE.md, Database Connections feature): CRUD + test-connection
against testcontainers-postgres (real reachable DB) and an unreachable host."""

from __future__ import annotations

import uuid as uuid_mod

from abkit.auth.passwords import hash_password
from abkit.db.repositories import UserRepo


def _login(app_client, email="editor@co.com", role="admin"):
    UserRepo().create(email=email, first_name="E", password_hash=hash_password("pw12345"), role=role)
    app_client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345"})


def _pg_body(display_name="My Postgres", **overrides):
    body = {
        "display_name": display_name, "engine": "postgresql", "host": "localhost", "port": 5432,
        "database": "abkit", "username": "abkit", "password": "s3cr3t-pw", "ssl": False,
    }
    body.update(overrides)
    return body


def test_create_requires_admin(app_client, db_url):
    _login(app_client, role="editor")
    resp = app_client.post("/api/v1/admin/db-connections", json=_pg_body())
    assert resp.status_code == 403


def test_editor_can_list_but_not_mutate(app_client, db_url):
    _login(app_client, role="admin")
    created = app_client.post("/api/v1/admin/db-connections", json=_pg_body()).json()

    app_client.post("/api/v1/auth/logout")
    _login(app_client, email="editor2@co.com", role="editor")

    list_resp = app_client.get("/api/v1/admin/db-connections")
    assert list_resp.status_code == 200
    assert any(c["id"] == created["id"] for c in list_resp.json())

    patch_resp = app_client.patch(
        f"/api/v1/admin/db-connections/{created['id']}", json={"display_name": "hacked"}
    )
    assert patch_resp.status_code == 403
    delete_resp = app_client.delete(f"/api/v1/admin/db-connections/{created['id']}")
    assert delete_resp.status_code == 403


def test_create_never_returns_password_and_never_stores_plaintext(app_client, db_url):
    _login(app_client)
    resp = app_client.post("/api/v1/admin/db-connections", json=_pg_body(password="super-secret-pw"))
    assert resp.status_code == 201
    body = resp.json()
    assert "password" not in body
    assert "password_encrypted" not in body

    from sqlalchemy import text as sa_text

    from abkit.db.engine import session_scope

    with session_scope() as s:
        row = s.execute(
            sa_text("SELECT password_encrypted FROM database_connections WHERE id = :id"),
            {"id": body["id"]},
        ).one()
    assert "super-secret-pw" not in row[0]

    from abkit.db.repositories import DatabaseConnectionRepo
    from abkit.db_connections.crypto import decrypt_password

    conn = DatabaseConnectionRepo().get_by_id(uuid_mod.UUID(body["id"]))
    assert decrypt_password(conn.password_encrypted) == "super-secret-pw"


def test_crud_roundtrip_and_audit_log(app_client, db_url):
    _login(app_client)
    created = app_client.post("/api/v1/admin/db-connections", json=_pg_body()).json()
    conn_id = created["id"]

    patched = app_client.patch(
        f"/api/v1/admin/db-connections/{conn_id}", json={"display_name": "Renamed"}
    ).json()
    assert patched["display_name"] == "Renamed"
    assert patched["host"] == "localhost"  # untouched fields survive a partial PATCH

    delete_resp = app_client.delete(f"/api/v1/admin/db-connections/{conn_id}")
    assert delete_resp.status_code == 204
    assert app_client.get("/api/v1/admin/db-connections").json() == []

    from abkit.db.repositories import AuditRepo

    actions = {a.action for a in AuditRepo().list_recent(limit=50)}
    assert {"db_connection.create", "db_connection.update", "db_connection.delete"} <= actions


def test_patch_password_updates_encrypted_value_when_provided(app_client, db_url):
    _login(app_client)
    created = app_client.post("/api/v1/admin/db-connections", json=_pg_body(password="first-pw")).json()
    app_client.patch(f"/api/v1/admin/db-connections/{created['id']}", json={"password": "second-pw"})

    from abkit.db.repositories import DatabaseConnectionRepo
    from abkit.db_connections.crypto import decrypt_password

    conn = DatabaseConnectionRepo().get_by_id(uuid_mod.UUID(created["id"]))
    assert decrypt_password(conn.password_encrypted) == "second-pw"


def test_patch_without_password_keeps_existing_password(app_client, db_url):
    _login(app_client)
    created = app_client.post("/api/v1/admin/db-connections", json=_pg_body(password="keep-me")).json()
    app_client.patch(f"/api/v1/admin/db-connections/{created['id']}", json={"display_name": "renamed only"})

    from abkit.db.repositories import DatabaseConnectionRepo
    from abkit.db_connections.crypto import decrypt_password

    conn = DatabaseConnectionRepo().get_by_id(uuid_mod.UUID(created["id"]))
    assert decrypt_password(conn.password_encrypted) == "keep-me"


def test_test_connection_against_unreachable_host(app_client, db_url):
    _login(app_client)
    created = app_client.post(
        "/api/v1/admin/db-connections",
        json=_pg_body(host="does-not-exist.invalid", port=5432),
    ).json()
    resp = app_client.post(f"/api/v1/admin/db-connections/{created['id']}/test")
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "host_unreachable"


def test_test_draft_connection_against_real_testcontainers_postgres(app_client, db_url):
    """Real end-to-end SELECT 1 against the same testcontainers-postgres
    instance this test session is already using — proves the whole chain
    (URL building, driver, timeout, SELECT 1) actually works, not just that
    errors are classified correctly."""
    from sqlalchemy.engine import make_url

    url = make_url(db_url)
    _login(app_client)
    resp = app_client.post(
        "/api/v1/admin/db-connections/test-draft",
        json={
            "engine": "postgresql", "host": url.host, "port": url.port,
            "database": url.database, "username": url.username, "password": url.password,
            "ssl": False,
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"outcome": "ok", "message": "Connection successful"}


def test_test_draft_connection_wrong_password_is_auth_failed(app_client, db_url):
    from sqlalchemy.engine import make_url

    url = make_url(db_url)
    _login(app_client)
    resp = app_client.post(
        "/api/v1/admin/db-connections/test-draft",
        json={
            "engine": "postgresql", "host": url.host, "port": url.port,
            "database": url.database, "username": url.username, "password": "definitely-wrong",
            "ssl": False,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "auth_failed"
