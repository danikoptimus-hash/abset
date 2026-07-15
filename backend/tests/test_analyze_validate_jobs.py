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


def _post_csv_with_outliers(n=200) -> str:
    """Same shape/unit-ids as _post_csv, but the first 2 users get an extreme
    revenue value — item 3.2/3.5: gives RemoveOutliers(upper_q=0.99) real
    outliers to trim so variance_reduction ends up non-null and positive.
    Exactly 2 (not more): upper_q=0.99 on n=200 keeps the top ~1% (2 rows) —
    more identical extreme values would put the quantile threshold ON the
    outlier plateau itself (inclusive `values <= hi` keeps them all), which
    is what happened with 5 before this was narrowed down to 2."""
    lines = ["user_id,revenue"]
    for i in range(n):
        value = 100_000 if i < 2 else 95 + i % 15
        lines.append(f"u{i},{value}")
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


def _zero_pad_design_csv(n=200) -> str:
    lines = ["user_id,revenue"] + [f"{i:05d},{100 + i % 10}.5" for i in range(n)]
    return "\n".join(lines)


def _zero_pad_post_csv(n=200) -> str:
    lines = ["user_id,revenue"] + [f"{i:05d},{95 + i % 15}" for i in range(n)]
    return "\n".join(lines)


def test_analyze_purely_numeric_unit_id_joins_without_losing_leading_zeros(
    app_client, tmp_path, monkeypatch
):
    """Regression for the unit_id dtype-mismatch bug: a post-analysis CSV
    whose unit_id column is purely numeric with leading zeros (e.g. "00007")
    must join fully against assignments. Without the dtype=str read-time
    hint pandas auto-parses it as int64 first, which both strips the leading
    zeros irrecoverably AND used to crash the merge with a str-vs-int64
    dtype error."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)

    dataset_id = _upload_csv(app_client, _zero_pad_design_csv())
    resp = app_client.post(
        "/api/v1/design", json={"config": _design_config("zero_pad_exp"), "dataset_id": dataset_id},
    )
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "completed", job

    post_dataset_id = _upload_csv(
        app_client, _zero_pad_post_csv(), kind="post_analysis", experiment_name="zero_pad_exp"
    )
    resp = app_client.post(
        "/api/v1/experiments/zero_pad_exp/analyze", json={"dataset_id": post_dataset_id},
    )
    assert resp.status_code == 202
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "completed", job

    results = app_client.get("/api/v1/experiments/zero_pad_exp/results").json()
    revenue_result = next(r for r in results["results"] if r["metric"] == "revenue")
    assert sum(revenue_result["n"].values()) == 200


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


def test_re_analyze_creates_new_history_row_and_updates_run_meta(app_client, tmp_path, monkeypatch):
    """UX package, п.3 (Re-run analysis): a second analyze run doesn't
    overwrite the first — GET .../results returns the LATEST run, with
    run_meta.run_number counting up and dataset_filename reflecting whichever
    dataset that specific run used."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_experiment(app_client, "rerun_exp")

    first_dataset_id = _upload_csv(
        app_client, _post_csv(seed_offset=0), kind="post_analysis", experiment_name="rerun_exp"
    )
    resp1 = app_client.post(
        "/api/v1/experiments/rerun_exp/analyze", json={"dataset_id": first_dataset_id},
    )
    job1 = _poll_job(app_client, resp1.json()["job_id"])
    assert job1["status"] == "completed", job1

    results1 = app_client.get("/api/v1/experiments/rerun_exp/results").json()
    assert results1["run_meta"]["run_number"] == 1
    assert results1["run_meta"]["dataset_filename"] == "data.csv"
    assert results1["run_meta"]["created_at"]

    second_dataset_id = _upload_csv(
        app_client, _post_csv(seed_offset=7), kind="post_analysis", experiment_name="rerun_exp"
    )
    resp2 = app_client.post(
        "/api/v1/experiments/rerun_exp/analyze", json={"dataset_id": second_dataset_id},
    )
    job2 = _poll_job(app_client, resp2.json()["job_id"])
    assert job2["status"] == "completed", job2

    results2 = app_client.get("/api/v1/experiments/rerun_exp/results").json()
    assert results2["run_meta"]["run_number"] == 2

    from abkit.db.repositories import ExperimentRepo, ResultRepo

    exp = ExperimentRepo().get_by_name("rerun_exp")
    assert ResultRepo().count_for_experiment(exp.id) == 2


def test_demo_post_data_prepares_dataset_without_running_analysis(app_client, tmp_path, monkeypatch):
    """UX package (explicit run, item B): "Generate demo post-period data"
    only PREPARES a dataset (synchronous, 201) — it must NOT start analysis
    or create an analysis_results row. Running is a separate, explicit step
    (POST .../analyze), same as for a real upload."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_experiment(app_client, "demo_prepare_exp")

    resp = app_client.post("/api/v1/experiments/demo_prepare_exp/demo-post-data", json={"effect": 0.03})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "post_analysis"
    assert body["experiment_name"] == "demo_prepare_exp"
    assert body["n_rows"] > 0
    assert body["filename"] == "demo_post_data.csv"

    # No analysis was run yet.
    results_resp = app_client.get("/api/v1/experiments/demo_prepare_exp/results")
    assert results_resp.status_code == 404


def test_analyze_demo_generates_post_data_itself(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_experiment(app_client, "analyze_demo_exp")

    prepare_resp = app_client.post("/api/v1/experiments/analyze_demo_exp/demo-post-data", json={"effect": 0.03})
    assert prepare_resp.status_code == 201, prepare_resp.text
    dataset_id = prepare_resp.json()["id"]

    resp = app_client.post(
        "/api/v1/experiments/analyze_demo_exp/analyze", json={"dataset_id": dataset_id},
    )
    assert resp.status_code == 202
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "completed", job

    results_resp = app_client.get("/api/v1/experiments/analyze_demo_exp/results")
    assert results_resp.status_code == 200
    # The demo dataset is a real, persisted dataset now — run_meta reports
    # its filename (not null, unlike before this dataset was ephemeral).
    assert results_resp.json()["run_meta"]["dataset_filename"] == "demo_post_data.csv"


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
        json={"dataset_id": dataset_id, "n_sims": 100, "effect": 0.1},
    )
    assert resp.status_code == 202
    job = _poll_job(app_client, resp.json()["job_id"], timeout=30.0)
    assert job["status"] == "completed", job
    assert "aa" in job["result"] and "ab" in job["result"]
    assert len(job["result"]["aa"]["methods"]) > 0
    assert len(job["result"]["ab"]["methods"]) > 0


def test_validate_rejects_n_sims_below_minimum(app_client, tmp_path, monkeypatch):
    """UX-package, Validation п.3.4: too few simulations make FPR/power
    estimates too noisy to interpret — reject rather than silently running
    a degenerate validation. The UI enforces this too (Validation.tsx); this
    is defense-in-depth against direct API calls."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_experiment(app_client, "validate_n_sims_exp")

    dataset_id = _upload_csv(app_client, _design_csv(n=300))
    resp = app_client.post(
        "/api/v1/experiments/validate_n_sims_exp/validate",
        json={"dataset_id": dataset_id, "n_sims": 10, "effect": 0.1},
    )
    assert resp.status_code == 422


def test_validate_result_records_dataset_id_and_filename(app_client, tmp_path, monkeypatch):
    """UX package, Validation п.C.5: the job result fixes which dataset the
    validation ran on."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_experiment(app_client, "validate_dataset_exp")

    dataset_id = _upload_csv(app_client, _design_csv(n=300))
    resp = app_client.post(
        "/api/v1/experiments/validate_dataset_exp/validate",
        json={"dataset_id": dataset_id, "n_sims": 100, "effect": 0.1},
    )
    job = _poll_job(app_client, resp.json()["job_id"], timeout=30.0)
    assert job["status"] == "completed", job
    assert job["result"]["dataset_id"] == dataset_id
    assert job["result"]["dataset_filename"] == "data.csv"


def test_get_design_dataset_returns_pre_design_dataset_for_experiment(app_client, tmp_path, monkeypatch):
    """UX package, Validation п.C.1: auto-datasource — the pre_design
    dataset used to design the experiment (via the wizard/API dataset
    upload) is what Validation should auto-select."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_experiment(app_client, "design_dataset_exp")

    resp = app_client.get("/api/v1/experiments/design_dataset_exp/design-dataset")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "pre_design"
    assert body["experiment_name"] == "design_dataset_exp"
    assert body["n_rows"] > 0


def test_get_design_dataset_404_when_none_stored(app_client, tmp_path, monkeypatch):
    """п.C.4: an experiment with no linked pre_design dataset (e.g. created
    directly via the repo/CLI, not the wizard) — frontend falls back to a
    manual upload."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    from abkit.db.repositories import ExperimentRepo, UserRepo

    _login(app_client)
    owner_id = UserRepo().get_by_email("editor@co.com").id
    ExperimentRepo().create(
        name="no_design_dataset_exp", owner_id=owner_id, status="designed",
        config={"name": "no_design_dataset_exp", "groups": {"control": 0.5, "treatment": 0.5}, "metrics": []},
    )

    resp = app_client.get("/api/v1/experiments/no_design_dataset_exp/design-dataset")
    assert resp.status_code == 404


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

    # Item 3: single stratum column ("platform") -> segments_by_dimension's
    # only key is that column's own name (no separate "combined" dimension
    # needed when there's just one).
    assert set(revenue["segments_by_dimension"].keys()) == {"platform"}
    assert len(next(iter(revenue["segments_by_dimension"]["platform"].values()))) == 2  # ios/android
    assert len(next(iter(revenue["daily"].values()))) == 2  # 2 дня


def test_analyze_unknown_experiment_404(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    dataset_id = _upload_csv(app_client, _post_csv())
    resp = app_client.post(
        "/api/v1/experiments/does_not_exist/analyze", json={"dataset_id": dataset_id},
    )
    assert resp.status_code == 404


def test_deleting_analyzed_dataset_leaves_results_intact(app_client, tmp_path, monkeypatch):
    """UX package, Datasets §2.2: deleting a dataset must not be blocked by
    — nor break — the results of experiments that already analyzed it
    (results.json is self-sufficient; migration 0009 SET NULLs the live FK
    and dataset_filename is a frozen snapshot, not a live lookup)."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client, email="admin_analyze@co.com", role="admin")
    name = "analyzed_then_deleted_exp"
    _design_experiment(app_client, name)

    post_dataset_id = _upload_csv(app_client, _post_csv())
    resp = app_client.post(
        f"/api/v1/experiments/{name}/analyze", json={"dataset_id": post_dataset_id},
    )
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "completed", job

    before = app_client.get(f"/api/v1/experiments/{name}/results")
    assert before.status_code == 200
    assert before.json()["run_meta"]["dataset_filename"] == "data.csv"

    delete_resp = app_client.request(
        "DELETE", f"/api/v1/datasets/{post_dataset_id}", json={"confirm": "DELETE"}
    )
    assert delete_resp.status_code == 204, delete_resp.text

    after = app_client.get(f"/api/v1/experiments/{name}/results")
    assert after.status_code == 200
    # frozen at analyze time — survives the dataset row itself being gone
    assert after.json()["run_meta"]["dataset_filename"] == "data.csv"
    assert after.json()["results"] == before.json()["results"]


# Item 3 (consolidated package, multi-select analysis methods):
# AnalyzeRequest.methods is a {metric_name: [method_id, ...]} override
# (first id = primary/designed, rest = comparison chains), translated
# (backend/routers/experiments.py) to the {metric_name: [Step, ...]} /
# {metric_name: [[Step, ...], ...]} shapes Experiment.analyze()'s `methods`/
# `extra_methods` parameters accept.
def test_analyze_with_explicit_method_override_changes_designed_result(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_experiment(app_client, "manual_method_exp")

    post_dataset_id = _upload_csv(
        app_client, _post_csv(), kind="post_analysis", experiment_name="manual_method_exp"
    )
    resp = app_client.post(
        "/api/v1/experiments/manual_method_exp/analyze",
        json={"dataset_id": post_dataset_id, "methods": {"revenue": ["mann_whitney"]}},
    )
    assert resp.status_code == 202
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "completed", job

    results = app_client.get("/api/v1/experiments/manual_method_exp/results").json()
    revenue_designed = next(
        r for r in results["results"] if r["metric"] == "revenue" and r["is_designed_method"]
    )
    assert revenue_designed["method"] == "Mann-Whitney (Hodges-Lehmann)"


def test_analyze_with_unknown_metric_in_methods_override_fails_job(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_experiment(app_client, "bad_method_exp")

    post_dataset_id = _upload_csv(
        app_client, _post_csv(), kind="post_analysis", experiment_name="bad_method_exp"
    )
    resp = app_client.post(
        "/api/v1/experiments/bad_method_exp/analyze",
        json={"dataset_id": post_dataset_id, "methods": {"does_not_exist": ["welch"]}},
    )
    assert resp.status_code == 202
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "failed", job
    assert "does_not_exist" in job["error"]


def test_analyze_with_unknown_method_id_fails_job(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_experiment(app_client, "bad_method_id_exp")

    post_dataset_id = _upload_csv(
        app_client, _post_csv(), kind="post_analysis", experiment_name="bad_method_id_exp"
    )
    resp = app_client.post(
        "/api/v1/experiments/bad_method_id_exp/analyze",
        json={"dataset_id": post_dataset_id, "methods": {"revenue": ["not_a_real_method"]}},
    )
    assert resp.status_code == 202
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "failed", job
    assert "not_a_real_method" in job["error"]


def test_analyze_with_empty_method_list_fails_job(app_client, tmp_path, monkeypatch):
    """Item 3.1: a metric present in the override with an empty method list
    is a client bug (the frontend never sends this — at least one method is
    always selected), not a silent fallback to the default."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_experiment(app_client, "empty_methods_exp")

    post_dataset_id = _upload_csv(
        app_client, _post_csv(), kind="post_analysis", experiment_name="empty_methods_exp"
    )
    resp = app_client.post(
        "/api/v1/experiments/empty_methods_exp/analyze",
        json={"dataset_id": post_dataset_id, "methods": {"revenue": []}},
    )
    assert resp.status_code == 202
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "failed", job
    assert "revenue" in job["error"]


def test_analyze_method_override_with_multiple_selected_methods_produces_comparison_rows(
    app_client, tmp_path, monkeypatch
):
    """Item 3.1/3.4: 2+ selected methods for a metric IS the comparison set
    now (replaces the old compare_methods bool + fixed
    compare_methods_chains() set) — the methods override's first id is
    designed, the rest run as extra (non-designed) chains, exactly as
    listed, nothing more and nothing less."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_experiment(app_client, "multiselect_exp")

    post_dataset_id = _upload_csv(
        app_client, _post_csv(), kind="post_analysis", experiment_name="multiselect_exp"
    )
    resp = app_client.post(
        "/api/v1/experiments/multiselect_exp/analyze",
        json={
            "dataset_id": post_dataset_id,
            "methods": {"revenue": ["mann_whitney", "welch", "remove_outliers_welch", "bootstrap_bca"]},
        },
    )
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "completed", job

    results = app_client.get("/api/v1/experiments/multiselect_exp/results").json()
    revenue_results = [r for r in results["results"] if r["metric"] == "revenue"]
    designed = [r for r in revenue_results if r["is_designed_method"]]
    assert len(designed) == 1
    assert designed[0]["method"] == "Mann-Whitney (Hodges-Lehmann)"
    # Exactly the three EXPLICITLY selected extras — not the old fixed
    # standard set (which would also include a plain CUPED+Welch row if
    # revenue had a pre_col, none of which was requested here).
    alt_methods = {r["method"] for r in revenue_results if not r["is_designed_method"]}
    assert alt_methods == {"Welch t-test", "RemoveOutliers + Welch t-test", "Bootstrap (bca)"}


def test_analyze_method_override_with_single_selected_method_produces_one_row(app_client, tmp_path, monkeypatch):
    """Item 3.1/3.4: exactly one selected method = pure calculation, no
    comparison rows at all — not even the old fixed standard set."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_experiment(app_client, "singleselect_exp")

    post_dataset_id = _upload_csv(
        app_client, _post_csv(), kind="post_analysis", experiment_name="singleselect_exp"
    )
    resp = app_client.post(
        "/api/v1/experiments/singleselect_exp/analyze",
        json={"dataset_id": post_dataset_id, "methods": {"revenue": ["welch"]}},
    )
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "completed", job

    results = app_client.get("/api/v1/experiments/singleselect_exp/results").json()
    revenue_results = [r for r in results["results"] if r["metric"] == "revenue"]
    assert len(revenue_results) == 1
    assert revenue_results[0]["is_designed_method"] is True
    assert revenue_results[0]["method"] == "Welch t-test"


def test_analyze_with_remove_outliers_method_populates_variance_reduction(app_client, tmp_path, monkeypatch):
    """Item 3.2/3.5: variance_reduction should be non-null and positive on a
    RemoveOutliers+Welch designed row when the post-period data has real
    outliers, and flow unchanged through to GET /results — that endpoint has
    no typed response schema for a single result (get_results in
    backend/routers/experiments.py returns the raw results.json dict), so
    this exercises the same to_json() round-trip the frontend actually
    reads."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_experiment(app_client, "outliers_method_exp")
    post_dataset_id = _upload_csv(
        app_client, _post_csv_with_outliers(), kind="post_analysis", experiment_name="outliers_method_exp"
    )
    resp = app_client.post(
        "/api/v1/experiments/outliers_method_exp/analyze",
        json={"dataset_id": post_dataset_id, "methods": {"revenue": ["remove_outliers_welch"]}},
    )
    assert resp.status_code == 202
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "completed", job

    results = app_client.get("/api/v1/experiments/outliers_method_exp/results").json()
    revenue_designed = next(
        r for r in results["results"] if r["metric"] == "revenue" and r["is_designed_method"]
    )
    assert revenue_designed["method"] == "RemoveOutliers + Welch t-test"
    assert revenue_designed["variance_reduction"] is not None
    assert revenue_designed["variance_reduction"] > 0
