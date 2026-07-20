"""Item 12: external split — HTTP/job-level coverage (core analyze logic is
tested directly in tests/test_experiment_external_split.py; this file
exercises the full POST /design (split_source="external") -> upload
post-data -> POST /analyze (group_column/group_mapping) -> GET /results path
through the real backend, DB-mode assignments/store included)."""

from __future__ import annotations

import time

from abkit.auth.passwords import hash_password
from abkit.db.repositories import AssignmentRepo, ExperimentRepo, UserRepo


def _login(app_client, email="editor@co.com", role="editor"):
    UserRepo().create(email=email, first_name="E", password_hash=hash_password("pw12345"), role=role)
    app_client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345"})


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


def _design_external(app_client, name: str, **overrides) -> dict:
    config = {
        "name": name,
        "unit_col": "",
        "groups": {"control": 0.5, "treatment": 0.5},
        "metrics": [{"name": "conversion", "type": "binary", "role": "primary"}],
        "split_source": "external",
        "isolation": "off",
    }
    config.update(overrides)
    resp = app_client.post("/api/v1/design", json={"config": config})
    assert resp.status_code == 202, resp.text
    return _poll_job(app_client, resp.json()["job_id"])


def test_design_external_creates_experiment_designed_no_dataset_needed(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)

    job = _design_external(app_client, "http_ext_exp")
    assert job["status"] == "completed", job
    assert job["result"]["experiment_name"] == "http_ext_exp"

    exp = ExperimentRepo().get_by_name("http_ext_exp")
    assert exp is not None
    assert exp.status == "designed"
    assert exp.config["split_source"] == "external"
    assert AssignmentRepo().load(exp.id).empty

    detail_resp = app_client.get("/api/v1/experiments/http_ext_exp")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["config"]["split_source"] == "external"


def test_design_external_requires_no_dataset_id_field(app_client, tmp_path, monkeypatch):
    """A normal split_source="abkit" design without dataset_id must still be
    rejected — dataset_id is only optional for split_source="external"."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    resp = app_client.post(
        "/api/v1/design",
        json={
            "config": {
                "name": "no_dataset_abkit", "unit_col": "user_id",
                "groups": {"control": 0.5, "treatment": 0.5},
                "metrics": [{"name": "revenue", "type": "continuous", "role": "primary"}],
                "sample_size": 100, "split_method": "simple", "isolation": "off",
            },
        },
    )
    assert resp.status_code == 422


def test_analyze_external_full_flow_with_column_values_endpoint(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    job = _design_external(app_client, "http_ext_analyze")
    assert job["status"] == "completed", job

    n = 60
    lines = ["variant,conversion"] + [
        f"{'A' if i < 30 else 'B' if i < 55 else 'C'},{1 if i % 3 == 0 else 0}" for i in range(n)
    ]
    csv_text = "\n".join(lines)
    up = app_client.post(
        "/api/v1/datasets", data={"kind": "post_analysis"},
        files={"file": ("post.csv", csv_text, "text/csv")},
    )
    assert up.status_code == 201, up.text
    dataset_id = up.json()["id"]

    values_resp = app_client.get(
        f"/api/v1/datasets/{dataset_id}/column-values", params={"column": "variant"},
    )
    assert values_resp.status_code == 200, values_resp.text
    values_body = values_resp.json()
    assert values_body["column"] == "variant"
    seen = {v["value"]: v["count"] for v in values_body["values"]}
    assert seen == {"A": 30, "B": 25, "C": 5}
    assert values_body["truncated"] is False

    analyze_resp = app_client.post(
        "/api/v1/experiments/http_ext_analyze/analyze",
        json={
            "dataset_id": dataset_id, "correction": "none", "compare_methods": False,
            "group_column": "variant",
            "group_mapping": {"A": "control", "B": "treatment", "C": "exclude"},
        },
    )
    assert analyze_resp.status_code == 202, analyze_resp.text
    analyze_job = _poll_job(app_client, analyze_resp.json()["job_id"])
    assert analyze_job["status"] == "completed", analyze_job

    results_resp = app_client.get("/api/v1/experiments/http_ext_analyze/results")
    assert results_resp.status_code == 200, results_resp.text
    body = results_resp.json()
    assert len(body["results"]) == 1
    assert any("Group column coverage" in w for w in body["global_warnings"])


def test_analyze_external_missing_mapping_fails_with_clear_error(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_external(app_client, "http_ext_no_mapping")

    up = app_client.post(
        "/api/v1/datasets", data={"kind": "post_analysis"},
        files={"file": ("post.csv", "variant,conversion\nA,1\nB,0\n", "text/csv")},
    )
    dataset_id = up.json()["id"]

    analyze_resp = app_client.post(
        "/api/v1/experiments/http_ext_no_mapping/analyze",
        json={"dataset_id": dataset_id, "correction": "none", "compare_methods": False},
    )
    assert analyze_resp.status_code == 202
    job = _poll_job(app_client, analyze_resp.json()["job_id"])
    assert job["status"] == "failed"
    assert "select a group column and map" in job["error"]


def _upload_dataset(app_client, filename: str, csv_text: str) -> str:
    up = app_client.post(
        "/api/v1/datasets", data={"kind": "post_analysis"},
        files={"file": (filename, csv_text, "text/csv")},
    )
    assert up.status_code == 201, up.text
    return up.json()["id"]


def test_design_external_persists_and_links_reference_dataset(app_client, tmp_path, monkeypatch):
    """External split rework (§1): an optional reference dataset is stored on
    the experiment (config.reference_dataset_id) AND linked in
    experiment_datasets, plus declared strata are persisted."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)

    ref_id = _upload_dataset(app_client, "reference.csv", "variant,conversion,country\nA,1,US\nB,0,UK\n")

    job = _design_external(
        app_client, "http_ext_ref",
        reference_dataset_id=ref_id, strata=["country"],
    )
    assert job["status"] == "completed", job

    exp = ExperimentRepo().get_by_name("http_ext_ref")
    assert exp.config["reference_dataset_id"] == ref_id
    assert exp.config["strata"] == ["country"]

    from abkit.db.repositories import ExperimentDatasetRepo

    links = ExperimentDatasetRepo().list_for_experiment(exp.id)
    assert any(str(link.dataset_id) == ref_id for link in links)


def test_analyze_external_strata_emits_balance_and_segments_in_results(app_client, tmp_path, monkeypatch):
    """External split rework (§2): analysis of an external experiment with
    declared strata surfaces a balance table + a per-segment breakdown in the
    results payload the frontend reads (chart_data)."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_external(app_client, "http_ext_strata", strata=["country"])

    # US: control ~10%, treatment ~30% (clear lift); UK: ~flat. Enough rows
    # per stratum to clear min_stratum_size and produce a segment breakdown.
    rows = ["variant,conversion,country"]
    for _ in range(60):
        rows += ["A,0,US", "B,1,US"]  # US treatment much higher
    for _ in range(60):
        rows += ["A,0,UK", "B,0,UK"]  # UK flat
    dataset_id = _upload_dataset(app_client, "post.csv", "\n".join(rows) + "\n")

    analyze_resp = app_client.post(
        "/api/v1/experiments/http_ext_strata/analyze",
        json={
            "dataset_id": dataset_id, "correction": "none",
            "group_column": "variant",
            "group_mapping": {"A": "control", "B": "treatment"},
            "segment_columns": ["country"],
        },
    )
    assert analyze_resp.status_code == 202, analyze_resp.text
    job = _poll_job(app_client, analyze_resp.json()["job_id"])
    assert job["status"] == "completed", job

    body = app_client.get("/api/v1/experiments/http_ext_strata/results").json()
    chart = body["chart_data"]
    # (a) balance table present with per-group columns.
    assert chart["strata_balance"] is not None
    assert set(chart["strata_balance"]["groups"]) == {"control", "treatment"}
    # (b) per-segment breakdown by the declared stratum.
    seg = chart["metrics"]["conversion"]["segments_by_dimension"]
    assert "country" in seg
    assert chart["ad_hoc_dimensions"] == []


def test_analyze_external_ad_hoc_segment_marked_in_results(app_client, tmp_path, monkeypatch):
    """External split rework (§3): a segment column not declared at design is
    reported as ad-hoc in the results payload."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_external(app_client, "http_ext_adhoc")  # no declared strata

    rows = ["variant,conversion,country"]
    for _ in range(40):
        rows += ["A,0,US", "B,1,US", "A,0,UK", "B,0,UK"]
    dataset_id = _upload_dataset(app_client, "post.csv", "\n".join(rows) + "\n")

    analyze_resp = app_client.post(
        "/api/v1/experiments/http_ext_adhoc/analyze",
        json={
            "dataset_id": dataset_id, "correction": "none",
            "group_column": "variant",
            "group_mapping": {"A": "control", "B": "treatment"},
            "segment_columns": ["country"],  # ad-hoc: never declared
        },
    )
    assert analyze_resp.status_code == 202, analyze_resp.text
    job = _poll_job(app_client, analyze_resp.json()["job_id"])
    assert job["status"] == "completed", job

    chart = app_client.get("/api/v1/experiments/http_ext_adhoc/results").json()["chart_data"]
    assert chart["ad_hoc_dimensions"] == ["country"]
    assert "country" in chart["metrics"]["conversion"]["segments_by_dimension"]


def test_redesign_rejected_for_external_experiment(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    _design_external(app_client, "http_ext_no_redesign")

    resp = app_client.post(
        "/api/v1/experiments/http_ext_no_redesign/redesign",
        json={
            "config": {
                "name": "http_ext_no_redesign", "unit_col": "",
                "groups": {"control": 0.5, "treatment": 0.5},
                "metrics": [{"name": "conversion", "type": "binary", "role": "primary"}],
                "split_source": "external", "isolation": "off",
            },
        },
    )
    assert resp.status_code == 422
