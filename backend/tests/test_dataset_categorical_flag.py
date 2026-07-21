"""Part 2 at the HTTP layer: the categorical flag is stored on creation (via
the heuristic), editable and persisted, and drives per-value vs binned strata —
with human-readable interval labels (no raw pandas "(0.999, 2.0]") in the
design report."""

from __future__ import annotations

import time

from abkit.auth.passwords import hash_password
from abkit.db.repositories import UserRepo


def _login(app_client, email="editor@co.com", role="editor"):
    UserRepo().create(email=email, first_name="E", password_hash=hash_password("pw12345"), role=role)
    app_client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345"})


def _poll(app_client, job_id, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = app_client.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] not in ("pending", "running"):
            return body
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _upload(app_client, filename, csv_text):
    up = app_client.post(
        "/api/v1/datasets", data={"kind": "pre_design"},
        files={"file": (filename, csv_text, "text/csv")},
    )
    assert up.status_code == 201, up.text
    return up.json()


def _strata_csv():
    # months_ago ∈ {1,2,3,5} (integer categories, the motivating bug),
    # income continuous, plus a unit id and a metric.
    rows = ["user_id,revenue,months_ago,income"]
    incomes = list(range(1000, 9000, 40))  # many distinct -> continuous
    i = 0
    for months in (1, 2, 3, 5):
        for _ in range(40):
            rows.append(f"u{i},{100 + i % 7},{months},{incomes[i % len(incomes)]}")
            i += 1
    return "\n".join(rows) + "\n"


def test_upload_stores_heuristic_categorical_and_edit_persists_override(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    ds = _upload(app_client, "strata.csv", _strata_csv())

    # Heuristic default: months_ago (4 distinct) categorical, income (many) not.
    cats = set(ds["categorical_columns"])
    assert "months_ago" in cats
    assert "income" not in cats

    # Edit: flag income categorical too; must persist.
    patched = app_client.patch(
        f"/api/v1/datasets/{ds['id']}",
        json={"categorical_columns": ["months_ago", "income"]},
    )
    assert patched.status_code == 200, patched.text
    assert set(patched.json()["dataset"]["categorical_columns"]) == {"months_ago", "income"}
    # And a fresh GET reflects it.
    got = app_client.get("/api/v1/datasets", params={"page_size": 200}).json()
    row = next(d for d in got["items"] if d["id"] == ds["id"])
    assert set(row["categorical_columns"]) == {"months_ago", "income"}


def _design(app_client, name, dataset_id, strata):
    resp = app_client.post(
        "/api/v1/design",
        json={
            "config": {
                "name": name, "unit_col": "user_id",
                "groups": {"control": 0.5, "treatment": 0.5},
                "metrics": [{"name": "revenue", "type": "continuous", "role": "primary"}],
                "sample_size": 160, "split_method": "stratified", "isolation": "off",
                "strata": strata,
            },
            "dataset_id": dataset_id,
        },
    )
    assert resp.status_code == 202, resp.text
    assert _poll(app_client, resp.json()["job_id"])["status"] == "completed"


def test_flagged_integer_column_yields_per_value_strata_no_interval_labels(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    ds = _upload(app_client, "strata.csv", _strata_csv())  # months_ago auto-categorical

    _design(app_client, "cat_strata", ds["id"], ["months_ago"])
    report = app_client.get("/api/v1/experiments/cat_strata/reports/design_report.html").text

    # Per-value strata (raw labels), and NO raw pandas interval syntax anywhere.
    assert ">1<" in report or ">1|" in report or "1</td>" in report  # a raw "1" stratum cell
    for bad in ("(0.999", "0.999,", "(0.", ", 2.0]"):
        assert bad not in report


def test_unflagged_continuous_column_bins_with_clean_labels(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    ds = _upload(app_client, "strata.csv", _strata_csv())  # income NOT categorical

    _design(app_client, "cont_strata", ds["id"], ["income"])
    report = app_client.get("/api/v1/experiments/cont_strata/reports/design_report.html").text

    # income is binned, but with human "lo–hi" labels — no interval syntax.
    for bad in ("(0.999", "0.999,", ".999]", ", 2.0]", "(999"):
        assert bad not in report
