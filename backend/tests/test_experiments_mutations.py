"""R3 (FRONTEND.md §3.2/§3.3): POST /{name}/status, PATCH /{name} (rename +
publication_status), DELETE /{name} (confirm=="DELETE"), GET/PUT /{name}/blocks."""

from __future__ import annotations

from abkit.auth.passwords import hash_password
from abkit.db.repositories import ExperimentRepo, UserRepo


def _login(app_client, email="editor@co.com", role="editor"):
    user_id = UserRepo().create(email=email, first_name="E", password_hash=hash_password("pw12345"), role=role)
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
        email="owner_status@co.com", first_name="O", password_hash=hash_password("pw12345"), role="editor"
    )
    _make_experiment("status_exp2", owner_id=other_owner)

    _login(app_client, email="not_owner@co.com", role="editor")
    resp = app_client.post("/api/v1/experiments/status_exp2/status", json={"to": "running"})
    assert resp.status_code == 403


def test_change_status_404_for_missing_experiment(app_client):
    _login(app_client)
    resp = app_client.post("/api/v1/experiments/missing/status", json={"to": "running"})
    assert resp.status_code == 404


def test_change_status_backward_transitions_allowed_and_audited(app_client):
    """6-part package pt.8: the backend has never restricted direction (any
    status -> any status, gated only by require_experiment_edit_access) —
    the frontend's status-badge dropdown used to only OFFER forward
    transitions, but the API itself already accepted backward ones. This
    locks that in for running->designed, completed->running, and
    archived->designed (unarchive), with audit_log recording from/to for
    each, same as any other status change."""
    from abkit.db.repositories import AuditRepo

    owner_id = _login(app_client)
    _make_experiment("backward_exp", status="completed", owner_id=owner_id)

    resp = app_client.post("/api/v1/experiments/backward_exp/status", json={"to": "running"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"

    resp = app_client.post("/api/v1/experiments/backward_exp/status", json={"to": "designed"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "designed"

    resp = app_client.post("/api/v1/experiments/backward_exp/status", json={"to": "archived"})
    assert resp.status_code == 200

    resp = app_client.post("/api/v1/experiments/backward_exp/status", json={"to": "designed"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "designed"

    audit = AuditRepo().list_recent(
        limit=10, action="experiment.status_change", object_name="backward_exp",
    )
    transitions = [(a.details["from"], a.details["to"]) for a in reversed(audit)]
    assert transitions == [
        ("completed", "running"),
        ("running", "designed"),
        ("designed", "archived"),
        ("archived", "designed"),
    ]


def test_change_status_backward_transition_resets_stale_lifecycle_timestamps(app_client):
    """Stage 2 item 2.5: timestamps must only ever reflect the furthest
    point the CURRENT run has reached — a backward transition clears the
    timestamps for stages no longer occupied (completed_at on
    completed->running, both started_at/completed_at on any->designed,
    archived_at on any unarchive), while the audit_log entry (asserted
    above) still records the transition regardless."""
    owner_id = _login(app_client)
    _make_experiment("reset_exp", status="designed", owner_id=owner_id)

    app_client.post("/api/v1/experiments/reset_exp/status", json={"to": "running"})
    exp = ExperimentRepo().get_by_name("reset_exp")
    assert exp.started_at is not None
    assert exp.completed_at is None
    original_started_at = exp.started_at

    app_client.post("/api/v1/experiments/reset_exp/status", json={"to": "completed"})
    exp = ExperimentRepo().get_by_name("reset_exp")
    assert exp.completed_at is not None

    # completed -> running: completed_at clears; started_at is the ORIGINAL
    # start (reopening a test continues its run, it doesn't restart it).
    app_client.post("/api/v1/experiments/reset_exp/status", json={"to": "running"})
    exp = ExperimentRepo().get_by_name("reset_exp")
    assert exp.completed_at is None
    assert exp.started_at == original_started_at

    # ...-> designed: both clear (implies the test hasn't started).
    app_client.post("/api/v1/experiments/reset_exp/status", json={"to": "designed"})
    exp = ExperimentRepo().get_by_name("reset_exp")
    assert exp.started_at is None
    assert exp.completed_at is None

    app_client.post("/api/v1/experiments/reset_exp/status", json={"to": "archived"})
    exp = ExperimentRepo().get_by_name("reset_exp")
    assert exp.archived_at is not None

    # unarchiving clears archived_at.
    app_client.post("/api/v1/experiments/reset_exp/status", json={"to": "running"})
    exp = ExperimentRepo().get_by_name("reset_exp")
    assert exp.archived_at is None
    assert exp.started_at is not None


def test_change_status_backward_transition_forbidden_for_non_owner_editor(app_client):
    other_owner = UserRepo().create(
        email="owner_backward@co.com", first_name="O", password_hash=hash_password("pw12345"), role="editor"
    )
    _make_experiment("backward_exp_guard", status="completed", owner_id=other_owner)

    _login(app_client, email="not_owner_backward@co.com", role="editor")
    resp = app_client.post("/api/v1/experiments/backward_exp_guard/status", json={"to": "running"})
    assert resp.status_code == 403


def test_unarchive_to_designed_reoccupies_units_for_isolation(app_client, tmp_path, monkeypatch):
    """6-part package pt.8.4: isolation reads Experiment.status live
    (abkit/db/repositories.py::_ACTIVE_STATUSES via a real-time query, not a
    cached snapshot) — archiving an experiment frees its users for a new
    design, and un-archiving it back to 'designed' must reserve them again."""
    import time

    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)

    lines = ["user_id,revenue"] + [f"u{i},{100 + i % 10}" for i in range(50)]
    csv_text = "\n".join(lines)

    def _upload(name_hint: str) -> str:
        resp = app_client.post(
            "/api/v1/datasets", data={"kind": "pre_design"},
            files={"file": (f"{name_hint}.csv", csv_text, "text/csv")},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    def _design(name: str, dataset_id: str, isolation: str) -> dict:
        resp = app_client.post(
            "/api/v1/design",
            json={
                "config": {
                    "name": name, "unit_col": "user_id",
                    "groups": {"control": 0.5, "treatment": 0.5},
                    "metrics": [{"name": "revenue", "type": "continuous", "role": "primary"}],
                    "sample_size": 50, "split_method": "simple",
                    "isolation": isolation, "exclude_experiments": "all_active",
                },
                "dataset_id": dataset_id,
            },
        )
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["job_id"]
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            job = app_client.get(f"/api/v1/jobs/{job_id}").json()
            if job["status"] not in ("pending", "running"):
                return job
            time.sleep(0.05)
        raise AssertionError("design job did not finish in time")

    first_job = _design("isolation_base", _upload("base"), isolation="off")
    assert first_job["status"] == "completed"

    archive_resp = app_client.post("/api/v1/experiments/isolation_base/status", json={"to": "archived"})
    assert archive_resp.status_code == 200

    # Archived: its units are free — isolation=warn against the same pool
    # must find no overlap.
    free_job = _design("isolation_probe_free", _upload("probe_free"), isolation="warn")
    assert free_job["status"] == "completed"

    unarchive_resp = app_client.post("/api/v1/experiments/isolation_base/status", json={"to": "designed"})
    assert unarchive_resp.status_code == 200

    # Back to 'designed': its units are reserved again — isolation=warn
    # against the same pool must now find the overlap and pause for confirm.
    reoccupied_job = _design("isolation_probe_reoccupied", _upload("probe_reoccupied"), isolation="warn")
    assert reoccupied_job["status"] == "requires_confirmation"
    assert reoccupied_job["result"]["overlap"] > 0


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
        email="owner_patch@co.com", first_name="O", password_hash=hash_password("pw12345"), role="editor"
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
        email="owner_del@co.com", first_name="O", password_hash=hash_password("pw12345"), role="editor"
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
        email="owner_blocks@co.com", first_name="O", password_hash=hash_password("pw12345"), role="editor"
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
        email="owner_delsum@co.com", first_name="O", password_hash=hash_password("pw12345"), role="editor"
    )
    _make_experiment("delsum_exp", owner_id=other_owner)

    _login(app_client, email="not_owner5@co.com", role="editor")
    resp = app_client.get("/api/v1/experiments/delsum_exp/deletion-summary")
    assert resp.status_code == 403


def test_bulk_delete_requires_exact_confirm_text(app_client):
    owner_id = _login(app_client)
    _make_experiment("bulk_del_confirm_exp", owner_id=owner_id)

    resp = app_client.post(
        "/api/v1/experiments/bulk-delete",
        json={"names": ["bulk_del_confirm_exp"], "confirm": "delete"},
    )
    assert resp.status_code == 400
    assert ExperimentRepo().get_by_name("bulk_del_confirm_exp") is not None


def test_bulk_delete_removes_owned_experiments(app_client):
    owner_id = _login(app_client)
    _make_experiment("bulk_del_a", owner_id=owner_id)
    _make_experiment("bulk_del_b", owner_id=owner_id)

    resp = app_client.post(
        "/api/v1/experiments/bulk-delete",
        json={"names": ["bulk_del_a", "bulk_del_b"], "confirm": "DELETE"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert sorted(body["deleted"]) == ["bulk_del_a", "bulk_del_b"]
    assert body["skipped"] == []
    assert ExperimentRepo().get_by_name("bulk_del_a") is None
    assert ExperimentRepo().get_by_name("bulk_del_b") is None


def test_bulk_delete_skips_experiments_without_permission_and_missing_names(app_client):
    """UX package, list п.E.5: mixed selection — the caller's own experiment
    is deleted, someone else's is skipped (no permission), a nonexistent
    name is skipped too, and the response reports both outcomes."""
    owner_id = _login(app_client)
    other_owner = UserRepo().create(
        email="owner_bulkdel_other@co.com", first_name="O", password_hash=hash_password("pw12345"), role="editor"
    )
    _make_experiment("bulk_del_own", owner_id=owner_id)
    _make_experiment("bulk_del_others", owner_id=other_owner)

    resp = app_client.post(
        "/api/v1/experiments/bulk-delete",
        json={"names": ["bulk_del_own", "bulk_del_others", "bulk_del_missing"], "confirm": "DELETE"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == ["bulk_del_own"]
    skipped_by_name = {s["name"]: s["reason"] for s in body["skipped"]}
    assert skipped_by_name["bulk_del_others"] == "no permission"
    assert skipped_by_name["bulk_del_missing"] == "not found"
    assert ExperimentRepo().get_by_name("bulk_del_own") is None
    assert ExperimentRepo().get_by_name("bulk_del_others") is not None


def test_bulk_delete_requires_editor_role(app_client):
    _login(app_client, email="viewer_bulkdel@co.com", role="viewer")
    resp = app_client.post(
        "/api/v1/experiments/bulk-delete",
        json={"names": ["whatever"], "confirm": "DELETE"},
    )
    assert resp.status_code == 403
