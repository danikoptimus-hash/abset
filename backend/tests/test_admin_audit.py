"""FRONTEND.md §3.2: admin-only GET /admin/users и GET /audit (глобальный
журнал; фильтр по пользователю — query-параметр `user`) — R2 (чтение) + R3
(POST/PATCH users, reset-password)."""

from __future__ import annotations

from abkit.auth.passwords import hash_password
from abkit.db.repositories import AuditRepo, UserRepo


def _login(app_client, email, role):
    UserRepo().create(email=email, first_name="U", password_hash=hash_password("pw12345"), role=role)
    app_client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345"})


def test_list_users_requires_admin(app_client):
    _login(app_client, "viewer@co.com", "viewer")
    resp = app_client.get("/api/v1/admin/users")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


def test_list_users_as_admin(app_client):
    _login(app_client, "admin@co.com", "admin")
    UserRepo().create(email="viewer2@co.com", first_name="V2", password_hash=hash_password("pw12345"), role="viewer")
    resp = app_client.get("/api/v1/admin/users")
    assert resp.status_code == 200
    emails = {u["email"] for u in resp.json()}
    assert {"admin@co.com", "viewer2@co.com"} <= emails


def test_global_audit_requires_admin(app_client):
    _login(app_client, "editor@co.com", "editor")
    resp = app_client.get("/api/v1/audit")
    assert resp.status_code == 403


def test_global_audit_filters_by_user(app_client):
    # _login сама пишет запись auth.login (abkit.auth.service.login) — не
    # только явный AuditRepo().log() ниже, поэтому total сравнивается с
    # запасом (>=), а не точным числом.
    _login(app_client, "admin2@co.com", "admin")
    AuditRepo().log(action="delete_experiment", user_email="other@co.com")

    resp_all = app_client.get("/api/v1/audit")
    assert resp_all.json()["total"] >= 2

    resp_filtered = app_client.get("/api/v1/audit", params={"user": "other@co.com"})
    body = resp_filtered.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "delete_experiment"

    resp_unknown = app_client.get("/api/v1/audit", params={"user": "nobody@co.com"})
    assert resp_unknown.json()["total"] == 0


def test_global_audit_filters_by_action(app_client):
    _login(app_client, "admin3@co.com", "admin")
    AuditRepo().log(action="login", user_email="admin3@co.com")
    AuditRepo().log(action="delete_experiment", user_email="admin3@co.com")

    resp = app_client.get("/api/v1/audit", params={"action": "delete_experiment"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "delete_experiment"


def test_create_user_requires_admin(app_client):
    _login(app_client, "editor4@co.com", "editor")
    resp = app_client.post(
        "/api/v1/admin/users",
        json={"email": "new@co.com", "first_name": "New", "role": "viewer"},
    )
    assert resp.status_code == 403


def test_create_user_as_admin_generates_password(app_client):
    _login(app_client, "admin4@co.com", "admin")
    resp = app_client.post(
        "/api/v1/admin/users",
        json={"email": "created@co.com", "first_name": "Created", "role": "editor"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["user"]["email"] == "created@co.com"
    assert body["user"]["must_change_password"] is True
    assert len(body["generated_password"]) > 0


def test_create_user_duplicate_email_409(app_client):
    _login(app_client, "admin5@co.com", "admin")
    UserRepo().create(email="dup5@co.com", first_name="D", password_hash=hash_password("pw12345"), role="viewer")
    resp = app_client.post(
        "/api/v1/admin/users", json={"email": "dup5@co.com", "first_name": "Dup", "role": "viewer"},
    )
    assert resp.status_code == 409


def test_patch_user_updates_role_and_active(app_client):
    _login(app_client, "admin6@co.com", "admin")
    target_id = UserRepo().create(
        email="target6@co.com", first_name="Target", password_hash=hash_password("pw12345"), role="viewer"
    )
    resp = app_client.patch(
        f"/api/v1/admin/users/{target_id}",
        json={"role": "editor", "is_active": False, "first_name": "Renamed"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "editor"
    assert body["is_active"] is False
    assert body["first_name"] == "Renamed"


def test_patch_user_404_for_unknown_id(app_client):
    _login(app_client, "admin7@co.com", "admin")
    resp = app_client.patch(
        "/api/v1/admin/users/11111111-1111-1111-1111-111111111111", json={"role": "editor"},
    )
    assert resp.status_code == 404


def test_reset_password_returns_new_password(app_client):
    _login(app_client, "admin8@co.com", "admin")
    target_id = UserRepo().create(
        email="target8@co.com", first_name="Target", password_hash=hash_password("pw12345"), role="viewer"
    )
    resp = app_client.post(f"/api/v1/admin/users/{target_id}/reset-password")
    assert resp.status_code == 200
    assert len(resp.json()["new_password"]) > 0
