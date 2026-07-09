"""R3 (FRONTEND.md §3.2/§4): POST /design — фоновая джоба поверх
Experiment.design(), включая isolation=warn -> requires_confirmation -> confirmed=true."""

from __future__ import annotations

import time

from abkit.auth.passwords import hash_password
from abkit.db.repositories import ExperimentRepo, UserRepo


def _login(app_client, email="editor@co.com", role="editor"):
    UserRepo().create(email=email, first_name="E", password_hash=hash_password("pw12345"), role=role)
    app_client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345"})


def _upload_csv(app_client, csv_text: str, kind: str = "pre_design", experiment_name: str | None = None):
    data = {"kind": kind}
    if experiment_name:
        data["experiment_name"] = experiment_name
    resp = app_client.post(
        "/api/v1/datasets", data=data, files={"file": ("data.csv", csv_text, "text/csv")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _design_csv(n=200) -> str:
    lines = ["user_id,revenue"] + [f"u{i},{100 + i % 10}" for i in range(n)]
    return "\n".join(lines)


def _design_config(name: str, isolation: str = "off", exclude_experiments="all_active") -> dict:
    return {
        "name": name,
        "unit_col": "user_id",
        "groups": {"control": 0.5, "treatment": 0.5},
        "metrics": [{"name": "revenue", "type": "continuous", "role": "primary"}],
        "sample_size": 200,
        "split_method": "simple",
        "isolation": isolation,
        "exclude_experiments": exclude_experiments,
    }


def _poll_job(app_client, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = app_client.get(f"/api/v1/jobs/{job_id}")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] not in ("pending", "running"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not finish within {timeout}s")


def test_design_requires_editor_role(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client, email="editor_uploader@co.com", role="editor")
    dataset_id = _upload_csv(app_client, _design_csv())
    app_client.post("/api/v1/auth/logout")

    _login(app_client, email="viewer@co.com", role="viewer")
    resp = app_client.post(
        "/api/v1/design", json={"config": _design_config("viewer_design"), "dataset_id": dataset_id},
    )
    assert resp.status_code == 403


def test_design_happy_path_creates_experiment_and_attaches_dataset(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    dataset_id = _upload_csv(app_client, _design_csv())

    resp = app_client.post(
        "/api/v1/design", json={"config": _design_config("design_happy"), "dataset_id": dataset_id},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    job = _poll_job(app_client, job_id)
    assert job["status"] == "completed"
    assert job["result"]["experiment_name"] == "design_happy"

    exp = ExperimentRepo().get_by_name("design_happy")
    assert exp is not None
    assert exp.status == "designed"

    ds_resp = app_client.get(f"/api/v1/datasets/{dataset_id}/preview")
    assert ds_resp.status_code == 200

    datasets_resp = app_client.get("/api/v1/datasets")
    linked = next(d for d in datasets_resp.json()["items"] if d["id"] == dataset_id)
    assert linked["experiment_name"] == "design_happy"


def test_design_isolation_warn_requires_confirmation_then_confirmed_continues(
    app_client, tmp_path, monkeypatch
):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)

    # Первый эксперимент занимает юнитов u0..u49 (все активные, mode=off).
    first_dataset = _upload_csv(app_client, _design_csv(n=200))
    first_resp = app_client.post(
        "/api/v1/design",
        json={"config": _design_config("warn_base", isolation="off"), "dataset_id": first_dataset},
    )
    first_job = _poll_job(app_client, first_resp.json()["job_id"])
    assert first_job["status"] == "completed"

    # Второй эксперимент, isolation=warn, те же юниты -> пересечение.
    second_dataset = _upload_csv(app_client, _design_csv(n=200))
    warn_resp = app_client.post(
        "/api/v1/design",
        json={
            "config": _design_config("warn_second", isolation="warn"),
            "dataset_id": second_dataset,
        },
    )
    warn_job = _poll_job(app_client, warn_resp.json()["job_id"])
    assert warn_job["status"] == "requires_confirmation"
    assert warn_job["result"]["overlap"] > 0
    assert "warn_base" in warn_job["result"]["by_experiment"]
    assert ExperimentRepo().get_by_name("warn_second") is None

    # Повторный вызов с confirmed=true продолжает и реально создает эксперимент.
    confirm_resp = app_client.post(
        "/api/v1/design",
        json={
            "config": _design_config("warn_second", isolation="warn"),
            "dataset_id": second_dataset,
            "confirmed": True,
        },
    )
    confirm_job = _poll_job(app_client, confirm_resp.json()["job_id"])
    assert confirm_job["status"] == "completed"
    assert ExperimentRepo().get_by_name("warn_second") is not None


def test_design_invalid_dataset_id_404(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.post(
        "/api/v1/design",
        json={
            "config": _design_config("no_dataset"),
            "dataset_id": "11111111-1111-1111-1111-111111111111",
        },
    )
    assert resp.status_code == 404


def test_design_duplicate_name_fails_job_not_http(app_client, tmp_path, monkeypatch):
    """Ошибка "эксперимент уже существует" — асинхронная (job.error), а не
    HTTP-статус: POST /design всегда 202, пока сама джоба не запустится."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    dataset_id = _upload_csv(app_client, _design_csv())
    first = app_client.post(
        "/api/v1/design", json={"config": _design_config("dup_exp"), "dataset_id": dataset_id},
    )
    _poll_job(app_client, first.json()["job_id"])

    dataset_id_2 = _upload_csv(app_client, _design_csv())
    second = app_client.post(
        "/api/v1/design", json={"config": _design_config("dup_exp"), "dataset_id": dataset_id_2},
    )
    assert second.status_code == 202
    job = _poll_job(app_client, second.json()["job_id"])
    assert job["status"] == "failed"
    assert "already exists" in job["error"]
