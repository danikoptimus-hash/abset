"""R3 (FRONTEND.md §3.2/§4): POST /experiments/{name}/analyze(+/demo)/validate
— фоновые джобы поверх Experiment.analyze()/run_aa/run_ab."""

from __future__ import annotations

import time

from abkit.auth.passwords import hash_password
from abkit.db.repositories import UserRepo


def _login(app_client, email="editor@co.com", role="editor"):
    UserRepo().create(email=email, name="E", password_hash=hash_password("pw12345"), role=role)
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
    # revenue как float (не int): run_validate_ab инжектит эффект как
    # float-прибавку (_inject_effect в abkit/validation/simulation.py) — на
    # int64-колонке pandas это ломается с LossySetitemError, это особенность
    # синтетических тестовых данных, а не ядра.
    lines = ["user_id,revenue"] + [f"u{i},{100 + i % 10}.5" for i in range(n)]
    return "\n".join(lines)


def _post_csv(n=200, seed_offset=0) -> str:
    lines = ["user_id,revenue"] + [f"u{i},{95 + (i + seed_offset) % 15}" for i in range(n)]
    return "\n".join(lines)


def _design_config(name: str) -> dict:
    return {
        "name": name,
        "unit_col": "user_id",
        "groups": {"control": 0.5, "treatment": 0.5},
        "metrics": [{"name": "revenue", "type": "continuous", "role": "primary"}],
        "sample_size": 200,
        "split_method": "simple",
        "isolation": "off",
    }


def _poll_job(app_client, job_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = app_client.get(f"/api/v1/jobs/{job_id}")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] not in ("pending", "running"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not finish within {timeout}s")


def _design_experiment(app_client, name: str) -> None:
    dataset_id = _upload_csv(app_client, _design_csv())
    resp = app_client.post(
        "/api/v1/design", json={"config": _design_config(name), "dataset_id": dataset_id},
    )
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "completed", job


def test_analyze_requires_dataset_and_populates_results_endpoint(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_experiment(app_client, "analyze_exp")

    post_dataset_id = _upload_csv(
        app_client, _post_csv(), kind="post_analysis", experiment_name="analyze_exp"
    )
    resp = app_client.post(
        "/api/v1/experiments/analyze_exp/analyze", json={"dataset_id": post_dataset_id},
    )
    assert resp.status_code == 202
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "completed", job
    assert job["result"]["experiment_name"] == "analyze_exp"

    # save_analysis_result вызван внутри джобы -> R2's GET .../results теперь
    # реально что-то возвращает (analysis_results иначе никогда не заполняется).
    results_resp = app_client.get("/api/v1/experiments/analyze_exp/results")
    assert results_resp.status_code == 200
    assert "results" in results_resp.json()

    detail = app_client.get("/api/v1/experiments/analyze_exp").json()
    assert "report.html" in detail["available_reports"]


def test_analyze_demo_generates_post_data_itself(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_experiment(app_client, "analyze_demo_exp")

    resp = app_client.post("/api/v1/experiments/analyze_demo_exp/analyze/demo", json={"effect": 0.03})
    assert resp.status_code == 202
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "completed", job

    results_resp = app_client.get("/api/v1/experiments/analyze_demo_exp/results")
    assert results_resp.status_code == 200


def test_analyze_requires_editor_role(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client, email="ed@co.com", role="editor")
    _design_experiment(app_client, "analyze_perm_exp")
    dataset_id = _upload_csv(
        app_client, _post_csv(), kind="post_analysis", experiment_name="analyze_perm_exp"
    )
    app_client.post("/api/v1/auth/logout")

    _login(app_client, email="viewer2@co.com", role="viewer")
    resp = app_client.post(
        "/api/v1/experiments/analyze_perm_exp/analyze", json={"dataset_id": dataset_id},
    )
    assert resp.status_code == 403


def test_validate_runs_aa_and_ab(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_experiment(app_client, "validate_exp")

    dataset_id = _upload_csv(app_client, _design_csv(n=300))
    resp = app_client.post(
        "/api/v1/experiments/validate_exp/validate",
        json={"dataset_id": dataset_id, "n_sims": 20, "effect": 0.1},
    )
    assert resp.status_code == 202
    job = _poll_job(app_client, resp.json()["job_id"], timeout=30.0)
    assert job["status"] == "completed", job
    assert "aa" in job["result"] and "ab" in job["result"]
    assert len(job["result"]["aa"]["methods"]) > 0
    assert len(job["result"]["ab"]["methods"]) > 0


def test_analyze_unknown_experiment_404(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    dataset_id = _upload_csv(app_client, _post_csv())
    resp = app_client.post(
        "/api/v1/experiments/does_not_exist/analyze", json={"dataset_id": dataset_id},
    )
    assert resp.status_code == 404
