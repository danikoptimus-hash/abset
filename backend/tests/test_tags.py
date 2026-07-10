"""Tags for A/B tests (Superset-style dashboard tags, CLAUDE.md): GET/POST
/tags, PUT /experiments/{name}/tags, GET /tags/{id}/usage, DELETE /tags/{id}
— plus tags showing up on experiment list/detail/properties and the list's
q/tag filters."""

from __future__ import annotations

from abkit.auth.passwords import hash_password
from abkit.db.repositories import AuditRepo, ExperimentRepo, UserRepo


def _make_user(email: str, role: str = "editor") -> str:
    return str(UserRepo().create(email=email, first_name="U", password_hash=hash_password("pw12345"), role=role))


def _login(app_client, email: str, role: str = "editor") -> str:
    user_id = _make_user(email, role=role)
    resp = app_client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345"})
    assert resp.status_code == 200
    return user_id


def _make_experiment(name: str, owner_id: str) -> None:
    ExperimentRepo().create(name=name, owner_id=owner_id, status="designed", config={"name": name})


def test_search_tags_empty_then_after_create(app_client):
    _login(app_client, "tags_search@co.com")
    empty = app_client.get("/api/v1/tags")
    assert empty.status_code == 200
    assert empty.json()["items"] == []

    create_resp = app_client.post("/api/v1/tags", json={"name": "Checkout"})
    assert create_resp.status_code == 201, create_resp.text
    assert create_resp.json()["name"] == "Checkout"

    search_resp = app_client.get("/api/v1/tags", params={"q": "check"})
    assert search_resp.status_code == 200
    names = [t["name"] for t in search_resp.json()["items"]]
    assert names == ["Checkout"]


def test_create_tag_is_case_insensitive_get_or_create(app_client):
    _login(app_client, "tags_getorcreate@co.com")
    first = app_client.post("/api/v1/tags", json={"name": "Growth"})
    second = app_client.post("/api/v1/tags", json={"name": "growth"})
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    all_tags = app_client.get("/api/v1/tags").json()["items"]
    assert len([t for t in all_tags if t["name"].lower() == "growth"]) == 1


def test_create_tag_requires_editor(app_client):
    _login(app_client, "tags_viewer@co.com", role="viewer")
    resp = app_client.post("/api/v1/tags", json={"name": "blocked"})
    assert resp.status_code == 403


def test_put_experiment_tags_and_visible_on_detail_and_list(app_client):
    owner_id = _login(app_client, "tags_owner@co.com")
    _make_experiment("tags_exp", owner_id)

    tag_a = app_client.post("/api/v1/tags", json={"name": "product-x"}).json()
    tag_b = app_client.post("/api/v1/tags", json={"name": "team-growth"}).json()

    put_resp = app_client.put(
        "/api/v1/experiments/tags_exp/tags", json={"tag_ids": [tag_a["id"], tag_b["id"]]},
    )
    assert put_resp.status_code == 200, put_resp.text
    put_names = sorted(t["name"] for t in put_resp.json())
    assert put_names == ["product-x", "team-growth"]

    detail = app_client.get("/api/v1/experiments/tags_exp").json()
    assert sorted(t["name"] for t in detail["tags"]) == ["product-x", "team-growth"]

    listing = app_client.get("/api/v1/experiments").json()
    row = next(e for e in listing["items"] if e["name"] == "tags_exp")
    assert sorted(t["name"] for t in row["tags"]) == ["product-x", "team-growth"]

    properties = app_client.get("/api/v1/experiments/tags_exp/properties").json()
    assert sorted(t["name"] for t in properties["tags"]) == ["product-x", "team-growth"]

    # A second PUT with only one tag id fully REPLACES the set, not merges.
    replace_resp = app_client.put("/api/v1/experiments/tags_exp/tags", json={"tag_ids": [tag_a["id"]]})
    assert [t["name"] for t in replace_resp.json()] == ["product-x"]

    audit = AuditRepo().list_recent(limit=10, object_name="tags_exp")
    assert any(a.action == "experiment.tags_change" for a in audit)


def test_put_experiment_tags_forbidden_for_unrelated_editor(app_client):
    other_owner = _make_user("tags_other_owner@co.com")
    _make_experiment("tags_exp_forbidden", other_owner)
    _login(app_client, "tags_unrelated@co.com")  # an unrelated editor
    tag_resp = app_client.post("/api/v1/tags", json={"name": "irrelevant"})

    resp = app_client.put(
        "/api/v1/experiments/tags_exp_forbidden/tags", json={"tag_ids": [tag_resp.json()["id"]]},
    )
    assert resp.status_code == 403


def test_list_filters_by_tag_id_with_and_logic(app_client):
    owner_id = _login(app_client, "tags_filter_owner@co.com")
    _make_experiment("tags_filter_both", owner_id)
    _make_experiment("tags_filter_one", owner_id)
    _make_experiment("tags_filter_neither", owner_id)

    tag_a = app_client.post("/api/v1/tags", json={"name": "filter-a"}).json()
    tag_b = app_client.post("/api/v1/tags", json={"name": "filter-b"}).json()
    app_client.put("/api/v1/experiments/tags_filter_both/tags", json={"tag_ids": [tag_a["id"], tag_b["id"]]})
    app_client.put("/api/v1/experiments/tags_filter_one/tags", json={"tag_ids": [tag_a["id"]]})

    only_a = app_client.get("/api/v1/experiments", params={"tag": [tag_a["id"]]}).json()
    names_a = {e["name"] for e in only_a["items"]}
    assert {"tags_filter_both", "tags_filter_one"}.issubset(names_a)
    assert "tags_filter_neither" not in names_a

    both = app_client.get("/api/v1/experiments", params={"tag": [tag_a["id"], tag_b["id"]]}).json()
    names_both = {e["name"] for e in both["items"]}
    assert names_both == {"tags_filter_both"}


def test_list_search_matches_tag_name(app_client):
    owner_id = _login(app_client, "tags_qsearch_owner@co.com")
    _make_experiment("tags_qsearch_exp", owner_id)
    tag = app_client.post("/api/v1/tags", json={"name": "special-unique-tagname"}).json()
    app_client.put("/api/v1/experiments/tags_qsearch_exp/tags", json={"tag_ids": [tag["id"]]})

    resp = app_client.get("/api/v1/experiments", params={"q": "special-unique-tagname"})
    names = {e["name"] for e in resp.json()["items"]}
    assert names == {"tags_qsearch_exp"}


def test_tag_usage_count(app_client):
    owner_id = _login(app_client, "tags_usage_owner@co.com")
    _make_experiment("tags_usage_exp1", owner_id)
    _make_experiment("tags_usage_exp2", owner_id)
    tag = app_client.post("/api/v1/tags", json={"name": "usage-count-tag"}).json()

    zero = app_client.get(f"/api/v1/tags/{tag['id']}/usage")
    assert zero.json()["count"] == 0

    app_client.put("/api/v1/experiments/tags_usage_exp1/tags", json={"tag_ids": [tag["id"]]})
    app_client.put("/api/v1/experiments/tags_usage_exp2/tags", json={"tag_ids": [tag["id"]]})

    two = app_client.get(f"/api/v1/tags/{tag['id']}/usage")
    assert two.json()["count"] == 2


def test_delete_tag_requires_admin_and_detaches_from_experiments(app_client):
    owner_id = _login(app_client, "tags_delete_owner@co.com")
    _make_experiment("tags_delete_exp", owner_id)
    tag = app_client.post("/api/v1/tags", json={"name": "to-delete"}).json()
    app_client.put("/api/v1/experiments/tags_delete_exp/tags", json={"tag_ids": [tag["id"]]})

    forbidden = app_client.delete(f"/api/v1/tags/{tag['id']}")
    assert forbidden.status_code == 403

    _login(app_client, "tags_delete_admin@co.com", role="admin")
    ok = app_client.delete(f"/api/v1/tags/{tag['id']}")
    assert ok.status_code == 200, ok.text
    assert ok.json()["affected_experiments"] == 1

    detail = app_client.get("/api/v1/experiments/tags_delete_exp").json()
    assert detail["tags"] == []

    search_resp = app_client.get("/api/v1/tags", params={"q": "to-delete"})
    assert search_resp.json()["items"] == []

    audit = AuditRepo().list_recent(limit=10, object_name="to-delete")
    assert any(a.action == "tag.delete" for a in audit)
