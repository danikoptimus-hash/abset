"""R3 (FRONTEND.md §3.2/§3.3): POST /{name}/status, PATCH /{name} (rename +
publication_status), DELETE /{name} (confirm=="DELETE"), GET/PUT /{name}/blocks."""

from __future__ import annotations

from abkit.auth.passwords import hash_password
from abkit.db.repositories import ExperimentRepo, UserRepo


def _login(app_client, email="editor@co.com", role="editor"):
    user_id = UserRepo().create(email=email, name="E", password_hash=hash_password("pw12345"), role=role)
    app_client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345"})
    return user_id


def _make_experiment(name="exp_a", status="designed", owner_id=None):
    return ExperimentRepo().create(
        name=name, owner_id=owner_id, status=status, config={"name": name},
    )


def test_change_status_owner_can_change_own(app_client):
    owner_id = _login(app_client)
    _make_experiment("status_exp", owner_id=owner_id)

    resp = app_client.post("/api/v1/experiments/status_exp/status", json={"to": "running"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
    assert ExperimentRepo().get_by_name("status_exp").started_at is not None


def test_change_status_forbidden_for_non_owner_editor(app_client):
    other_owner = UserRepo().create(
        email="owner_status@co.com", name="O", password_hash=hash_password("pw12345"), role="editor"
    )
    _make_experiment("status_exp2", owner_id=other_owner)

    _login(app_client, email="not_owner@co.com", role="editor")
    resp = app_client.post("/api/v1/experiments/status_exp2/status", json={"to": "running"})
    assert resp.status_code == 403


def test_change_status_404_for_missing_experiment(app_client):
    _login(app_client)
    resp = app_client.post("/api/v1/experiments/missing/status", json={"to": "running"})
    assert resp.status_code == 404


def test_patch_publication_status_toggle(app_client):
    owner_id = _login(app_client)
    _make_experiment("pub_toggle_exp", owner_id=owner_id)

    resp = app_client.patch(
        "/api/v1/experiments/pub_toggle_exp", json={"publication_status": "published"}
    )
    assert resp.status_code == 200
    assert resp.json()["publication_status"] == "published"

    resp2 = app_client.patch(
        "/api/v1/experiments/pub_toggle_exp", json={"publication_status": "draft"}
    )
    assert resp2.json()["publication_status"] == "draft"


def test_patch_rename(app_client):
    owner_id = _login(app_client)
    _make_experiment("old_exp_name", owner_id=owner_id)

    resp = app_client.patch("/api/v1/experiments/old_exp_name", json={"name": "new_exp_name"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "new_exp_name"
    assert ExperimentRepo().get_by_name("old_exp_name") is None
    assert ExperimentRepo().get_by_name("new_exp_name") is not None


def test_patch_rename_conflict_409(app_client):
    owner_id = _login(app_client)
    _make_experiment("taken_name", owner_id=owner_id)
    _make_experiment("rename_me", owner_id=owner_id)

    resp = app_client.patch("/api/v1/experiments/rename_me", json={"name": "taken_name"})
    assert resp.status_code == 409


def test_patch_forbidden_for_non_owner(app_client):
    other_owner = UserRepo().create(
        email="owner_patch@co.com", name="O", password_hash=hash_password("pw12345"), role="editor"
    )
    _make_experiment("patch_forbidden_exp", owner_id=other_owner)

    _login(app_client, email="not_owner4@co.com", role="editor")
    resp = app_client.patch(
        "/api/v1/experiments/patch_forbidden_exp", json={"publication_status": "published"}
    )
    assert resp.status_code == 403


def test_delete_requires_exact_confirm_text(app_client):
    owner_id = _login(app_client)
    _make_experiment("del_exp", owner_id=owner_id)

    resp_bad = app_client.request(
        "DELETE", "/api/v1/experiments/del_exp", json={"confirm": "delete"}
    )
    assert resp_bad.status_code == 400
    assert ExperimentRepo().get_by_name("del_exp") is not None

    resp_ok = app_client.request(
        "DELETE", "/api/v1/experiments/del_exp", json={"confirm": "DELETE"}
    )
    assert resp_ok.status_code == 200
    assert ExperimentRepo().get_by_name("del_exp") is None


def test_delete_forbidden_for_non_owner(app_client):
    other_owner = UserRepo().create(
        email="owner_del@co.com", name="O", password_hash=hash_password("pw12345"), role="editor"
    )
    _make_experiment("del_exp2", owner_id=other_owner)

    _login(app_client, email="not_owner2@co.com", role="editor")
    resp = app_client.request("DELETE", "/api/v1/experiments/del_exp2", json={"confirm": "DELETE"})
    assert resp.status_code == 403


def test_blocks_auto_created_and_readable(app_client):
    owner_id = _login(app_client)
    _make_experiment("blocks_exp", owner_id=owner_id)

    resp = app_client.get("/api/v1/experiments/blocks_exp/blocks")
    assert resp.status_code == 200
    kinds = [b["kind"] for b in resp.json()]
    assert kinds == ["hypothesis", "conclusion", "decision"]


def test_blocks_put_updates_and_adds_custom(app_client):
    owner_id = _login(app_client)
    _make_experiment("blocks_edit_exp", owner_id=owner_id)

    existing = app_client.get("/api/v1/experiments/blocks_edit_exp/blocks").json()
    hypothesis = next(b for b in existing if b["kind"] == "hypothesis")

    resp = app_client.put(
        "/api/v1/experiments/blocks_edit_exp/blocks",
        json=[
            {"id": hypothesis["id"], "kind": "hypothesis", "title": "H", "content_md": "новый текст", "position": 0},
            {"kind": "custom", "title": "Заметка", "content_md": "текст", "position": 3},
        ],
    )
    assert resp.status_code == 200
    updated = resp.json()
    updated_hypothesis = next(b for b in updated if b["kind"] == "hypothesis")
    assert updated_hypothesis["content_md"] == "новый текст"
    # тот же id, не новый ряд (регрессия: id из запроса приходит строкой, а
    # не uuid.UUID — lookup по строке против словаря на UUID-ключах ранее
    # всегда промахивался и плодил дубликаты вместо апдейта, см.
    # abkit/db/repositories.py::BlockRepo.upsert_many)
    assert updated_hypothesis["id"] == hypothesis["id"]
    assert any(b["kind"] == "custom" for b in updated)

    all_blocks = app_client.get("/api/v1/experiments/blocks_edit_exp/blocks").json()
    assert len(all_blocks) == 4  # hypothesis+conclusion+decision+custom, БЕЗ дубликата


def test_blocks_put_forbidden_for_non_owner(app_client):
    other_owner = UserRepo().create(
        email="owner_blocks@co.com", name="O", password_hash=hash_password("pw12345"), role="editor"
    )
    _make_experiment("blocks_forbidden_exp", owner_id=other_owner)

    _login(app_client, email="not_owner3@co.com", role="editor")
    resp = app_client.put("/api/v1/experiments/blocks_forbidden_exp/blocks", json=[])
    assert resp.status_code == 403


def test_deletion_summary_returns_real_counts(app_client):
    import pandas as pd

    from abkit.db.repositories import AssignmentRepo

    owner_id = _login(app_client)
    exp = _make_experiment("deletion_summary_exp", owner_id=owner_id)
    AssignmentRepo().bulk_insert(
        exp.id,
        pd.DataFrame(
            {"unit_id": ["u1", "u2"], "group": ["control", "treatment"], "stratum": [None, None], "assigned_at": pd.Timestamp.now(tz="UTC")}
        ),
    )

    resp = app_client.get("/api/v1/experiments/deletion_summary_exp/deletion-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["assignments"] == 2
    assert body["datasets"] == 0
    assert body["results"] == 0


def test_deletion_summary_forbidden_for_non_owner(app_client):
    other_owner = UserRepo().create(
        email="owner_delsum@co.com", name="O", password_hash=hash_password("pw12345"), role="editor"
    )
    _make_experiment("delsum_exp", owner_id=other_owner)

    _login(app_client, email="not_owner5@co.com", role="editor")
    resp = app_client.get("/api/v1/experiments/delsum_exp/deletion-summary")
    assert resp.status_code == 403
