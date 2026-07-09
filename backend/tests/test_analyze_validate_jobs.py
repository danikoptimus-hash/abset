"""R3 (FRONTEND.md §3.2/§4): POST /experiments/{name}/analyze(+/demo)/validate
— фоновые джобы поверх Experiment.analyze()/run_aa/run_ab."""

from __future__ import annotations

import time

from abkit.auth.passwords import hash_password
from abkit.db.repositories import UserRepo


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


def test_analyze_blocked_on_experiment_editor_cannot_see(app_client, tmp_path, monkeypatch):
    """UX package (see CLAUDE.md 'Permissions model'): Analyze/Validate stay
    open to any editor+ role (test_run_analyze_editor_allowed_on_others_experiment
    in tests/test_jobs_permission_matrix.py), but ONLY for an experiment the
    editor can actually see — a draft experiment they don't own and have no
    experiment_access grant on is invisible (404), so it's also unreachable
    via analyze, same as it's unreachable via GET /experiments/{name}."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client, email="owner3@co.com", role="editor")
    _design_experiment(app_client, "analyze_invisible_exp")  # publication_status="draft" by default
    dataset_id = _upload_csv(
        app_client, _post_csv(), kind="post_analysis", experiment_name="analyze_invisible_exp"
    )
    app_client.post("/api/v1/auth/logout")

    _login(app_client, email="outsider_editor@co.com", role="editor")
    list_resp = app_client.get("/api/v1/experiments")
    assert list_resp.json()["total"] == 0

    resp = app_client.post(
        "/api/v1/experiments/analyze_invisible_exp/analyze", json={"dataset_id": dataset_id},
    )
    assert resp.status_code == 404


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


def test_analyze_results_include_chart_data_for_continuous_binary_segments_and_daily(
    app_client, tmp_path, monkeypatch
):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)

    n = 300
    design_lines = ["user_id,revenue,clicks,platform"] + [
        f"u{i},{100 + i % 10}.5,{i % 2},{'ios' if i % 2 == 0 else 'android'}" for i in range(n)
    ]
    dataset_id = _upload_csv(app_client, "\n".join(design_lines))
    config = {
        "name": "chart_data_exp",
        "unit_col": "user_id",
        "groups": {"control": 0.5, "treatment": 0.5},
        "metrics": [
            {"name": "revenue", "type": "continuous", "role": "primary"},
            {"name": "clicks", "type": "binary", "role": "secondary"},
        ],
        "strata": ["platform"],
        "sample_size": n,
        "split_method": "stratified",
        "isolation": "off",
    }
    resp = app_client.post("/api/v1/design", json={"config": config, "dataset_id": dataset_id})
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "completed", job

    # Пост-данные с разбивкой по 2 дням -> daily_results заполнится (нужен date_col).
    post_lines = ["user_id,revenue,clicks,event_date"] + [
        f"u{i},{95 + i % 15}.5,{(i + 1) % 2},2026-01-0{1 + i % 2}" for i in range(n)
    ]
    post_dataset_id = _upload_csv(
        app_client, "\n".join(post_lines), kind="post_analysis", experiment_name="chart_data_exp"
    )
    analyze_resp = app_client.post(
        "/api/v1/experiments/chart_data_exp/analyze",
        json={"dataset_id": post_dataset_id, "date_col": "event_date"},
    )
    analyze_job = _poll_job(app_client, analyze_resp.json()["job_id"])
    # Регрессия: один из сегментов (стратифицированный по platform) вырожден
    # (нулевая дисперсия) -> effect_rel=NaN у designed-результата этого
    # сегмента. json.dumps() пишет NaN как ЛИТЕРАЛ (валидно для Python, НЕ
    # валидно по спецификации JSON) — Postgres JSONB отклонял такую вставку
    # ("Token \"NaN\" is invalid"), job падал в failed. sanitize_json_floats()
    # в _save_analysis чинит это, заменяя NaN/Infinity на null.
    assert analyze_job["status"] == "completed", analyze_job

    raw_results_resp = app_client.get("/api/v1/experiments/chart_data_exp/results")
    assert "NaN" not in raw_results_resp.text
    results = raw_results_resp.json()
    chart_data = results["chart_data"]
    assert chart_data["checks"]["srm"]["passed"] in (True, False)
    assert chart_data["checks"]["loss"]["symmetric"] in (True, False)

    revenue = chart_data["metrics"]["revenue"]
    assert revenue["metric_type"] == "continuous"
    treat_dist = next(iter(revenue["distributions"].values()))
    assert treat_dist["kind"] == "continuous"
    assert len(treat_dist["clipped"]["bin_edges"]) > 1
    assert len(treat_dist["full_range"]["bin_edges"]) > 1
    assert len(treat_dist["control_ecdf"]) > 0

    clicks = chart_data["metrics"]["clicks"]
    assert clicks["metric_type"] == "binary"
    clicks_dist = next(iter(clicks["distributions"].values()))
    assert clicks_dist["kind"] == "binary"
    assert 0.0 <= clicks_dist["control"]["prop"] <= 1.0

    assert len(next(iter(revenue["segments"].values()))) == 2  # ios/android
    assert len(next(iter(revenue["daily"].values()))) == 2  # 2 дня


def test_analyze_unknown_experiment_404(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    dataset_id = _upload_csv(app_client, _post_csv())
    resp = app_client.post(
        "/api/v1/experiments/does_not_exist/analyze", json={"dataset_id": dataset_id},
    )
    assert resp.status_code == 404
