"""Edit Properties (UX package, section 3): GET/PUT /{name}/properties, the
experiment_access grant table (additional owners/editors), and visible_roles
list-visibility filtering. See the "Permissions model" section in CLAUDE.md
for the intended policy this enforces."""

from __future__ import annotations

from abkit.auth.passwords import hash_password
from abkit.db.repositories import ExperimentRepo, UserRepo


def _make_user(email: str, role: str = "editor") -> str:
    return str(UserRepo().create(email=email, first_name="U", password_hash=hash_password("pw12345"), role=role))


def _login(app_client, email: str, role: str = "editor") -> str:
    user_id = _make_user(email, role=role)
    resp = app_client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345"})
    assert resp.status_code == 200
    return user_id


def _make_experiment(name: str, owner_id: str) -> None:
    ExperimentRepo().create(name=name, owner_id=owner_id, status="designed", config={"name": name})


def test_owner_can_get_and_put_properties(app_client):
    owner_id = _login(app_client, "owner_props@co.com")
    _make_experiment("props_exp", owner_id)

    resp = app_client.get("/api/v1/experiments/props_exp/properties")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "props_exp"
    assert body["owner"]["id"] == owner_id
    assert body["owners"] == []
    assert body["editors"] == []
    assert body["visible_roles"] is None

    put_resp = app_client.put(
        "/api/v1/experiments/props_exp/properties",
        json={"name": "props_exp_renamed", "owner_ids": [], "editor_ids": [], "visible_roles": None},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["name"] == "props_exp_renamed"
    assert ExperimentRepo().get_by_name("props_exp") is None
    assert ExperimentRepo().get_by_name("props_exp_renamed") is not None


def test_properties_forbidden_for_unrelated_editor(app_client):
    other_owner = _make_user("owner_props2@co.com")
    _make_experiment("props_exp2", other_owner)

    _login(app_client, "unrelated_editor@co.com")
    get_resp = app_client.get("/api/v1/experiments/props_exp2/properties")
    assert get_resp.status_code == 403

    put_resp = app_client.put(
        "/api/v1/experiments/props_exp2/properties",
        json={"name": "props_exp2", "owner_ids": [], "editor_ids": [], "visible_roles": None},
    )
    assert put_resp.status_code == 403


def test_granting_editor_access_allows_that_editor_to_edit(app_client):
    owner_id = _login(app_client, "owner_props3@co.com")
    granted_editor_id = _make_user("granted_editor@co.com")
    _make_experiment("props_exp3", owner_id)

    # Before the grant, the editor cannot rename (edit) the experiment.
    app_client.post("/api/v1/auth/logout")
    app_client.post("/api/v1/auth/login", json={"email": "granted_editor@co.com", "password": "pw12345"})
    forbidden = app_client.patch("/api/v1/experiments/props_exp3", json={"name": "props_exp3_x"})
    assert forbidden.status_code == 403

    # Owner grants editor access via Properties.
    app_client.post("/api/v1/auth/logout")
    app_client.post("/api/v1/auth/login", json={"email": "owner_props3@co.com", "password": "pw12345"})
    put_resp = app_client.put(
        "/api/v1/experiments/props_exp3/properties",
        json={"name": "props_exp3", "owner_ids": [], "editor_ids": [granted_editor_id], "visible_roles": None},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["editors"][0]["id"] == granted_editor_id

    # After the grant, the same editor can now edit (rename) the experiment.
    app_client.post("/api/v1/auth/logout")
    app_client.post("/api/v1/auth/login", json={"email": "granted_editor@co.com", "password": "pw12345"})
    allowed = app_client.patch("/api/v1/experiments/props_exp3", json={"name": "props_exp3_renamed"})
    assert allowed.status_code == 200
    assert ExperimentRepo().get_by_name("props_exp3_renamed") is not None


def test_visible_roles_hides_experiment_from_uninvolved_roles_in_list(app_client):
    owner_id = _login(app_client, "owner_props4@co.com")
    _make_experiment("props_exp4", owner_id)
    app_client.patch("/api/v1/experiments/props_exp4", json={"publication_status": "published"})
    app_client.put(
        "/api/v1/experiments/props_exp4/properties",
        json={"name": "props_exp4", "owner_ids": [], "editor_ids": [], "visible_roles": ["admin"]},
    )

    # An unrelated editor no longer sees it in the list or on its detail page.
    app_client.post("/api/v1/auth/logout")
    _login(app_client, "outside_editor@co.com")
    list_resp = app_client.get("/api/v1/experiments", params={"page_size": 200})
    names = [item["name"] for item in list_resp.json()["items"]]
    assert "props_exp4" not in names
    assert app_client.get("/api/v1/experiments/props_exp4").status_code == 404

    # An admin still sees it (visible_roles always includes admin).
    app_client.post("/api/v1/auth/logout")
    _login(app_client, "admin_props4@co.com", role="admin")
    admin_list = app_client.get("/api/v1/experiments", params={"page_size": 200})
    admin_names = [item["name"] for item in admin_list.json()["items"]]
    assert "props_exp4" in admin_names
