"""GET /experiments/by-id/{id} — резолв permalink'а кнопки Share.

Смысл фичи в одной строке: ссылка должна пережить ПЕРЕИМЕНОВАНИЕ теста, чего
именной URL не умеет (CLAUDE.md, "Известный техдолг"). Поэтому центральный
тест здесь — именно ренейм.
"""

from __future__ import annotations

import time

from abkit.auth.passwords import hash_password
from abkit.db.repositories import ExperimentRepo, UserRepo


def _login(app_client, email="editor@co.com", role="editor"):
    UserRepo().create(email=email, first_name="E", password_hash=hash_password("pw12345"), role=role)
    resp = app_client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345"})
    assert resp.status_code == 200, resp.text


def _design_csv(n=200) -> str:
    return "\n".join(["user_id,revenue"] + [f"u{i},{100 + i % 10}.5" for i in range(n)])


def _poll_job(app_client, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = app_client.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] not in ("pending", "running"):
            return body
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _design(app_client, name: str) -> None:
    upload = app_client.post(
        "/api/v1/datasets",
        data={"kind": "pre_design"},
        files={"file": ("data.csv", _design_csv(), "text/csv")},
    )
    assert upload.status_code == 201, upload.text
    resp = app_client.post(
        "/api/v1/design",
        json={
            "config": {
                "name": name,
                "unit_col": "user_id",
                "groups": {"control": 0.5, "treatment": 0.5},
                "metrics": [{"name": "revenue", "type": "continuous", "role": "primary"}],
                "sample_size": 200,
                "split_method": "simple",
                "isolation": "off",
            },
            "dataset_id": upload.json()["id"],
        },
    )
    assert resp.status_code == 202, resp.text
    assert _poll_job(app_client, resp.json()["job_id"])["status"] == "completed"


def test_experiment_detail_exposes_a_stable_id(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design(app_client, "pl_exp")

    detail = app_client.get("/api/v1/experiments/pl_exp").json()
    assert detail["id"] == str(ExperimentRepo().get_by_name("pl_exp").id)


def test_by_id_resolves_to_the_current_name(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design(app_client, "pl_resolve")
    exp_id = app_client.get("/api/v1/experiments/pl_resolve").json()["id"]

    resp = app_client.get(f"/api/v1/experiments/by-id/{exp_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": exp_id, "name": "pl_resolve"}


def test_permalink_survives_a_rename(app_client, tmp_path, monkeypatch):
    """Ровно то, ради чего фича существует: именной URL после ренейма мертв,
    id-шный — ведет на новое имя."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design(app_client, "pl_old_name")
    exp_id = app_client.get("/api/v1/experiments/pl_old_name").json()["id"]

    renamed = app_client.patch("/api/v1/experiments/pl_old_name", json={"name": "pl_new_name"})
    assert renamed.status_code == 200, renamed.text

    # Старый именной URL больше не резолвится...
    assert app_client.get("/api/v1/experiments/pl_old_name").status_code == 404
    # ...а permalink ведет на новое имя, id не изменился.
    resp = app_client.get(f"/api/v1/experiments/by-id/{exp_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": exp_id, "name": "pl_new_name"}


def test_by_id_is_404_for_unknown_id(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.get("/api/v1/experiments/by-id/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404, resp.text


def test_by_id_is_422_for_a_malformed_id(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.get("/api/v1/experiments/by-id/not-a-uuid")
    assert resp.status_code == 422, resp.text


def test_by_id_hides_an_invisible_draft_behind_404_not_403(app_client, tmp_path, monkeypatch):
    """Получатель ссылки без доступа: 404, а не 403 — существование чужого
    черновика не подтверждается даже отказом (abkit/access.py)."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design(app_client, "pl_secret")
    exp_id = app_client.get("/api/v1/experiments/pl_secret").json()["id"]
    app_client.post("/api/v1/auth/logout")

    _login(app_client, email="stranger@co.com", role="editor")
    resp = app_client.get(f"/api/v1/experiments/by-id/{exp_id}")
    assert resp.status_code == 404, resp.text


def test_by_id_works_for_a_viewer_on_a_published_experiment(app_client, tmp_path, monkeypatch):
    """Share доступен любой роли, которая ВИДИТ тест — включая viewer."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design(app_client, "pl_public")
    exp_id = app_client.get("/api/v1/experiments/pl_public").json()["id"]
    published = app_client.patch(
        "/api/v1/experiments/pl_public", json={"publication_status": "published"}
    )
    assert published.status_code == 200, published.text
    app_client.post("/api/v1/auth/logout")

    _login(app_client, email="viewer@co.com", role="viewer")
    resp = app_client.get(f"/api/v1/experiments/by-id/{exp_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "pl_public"
