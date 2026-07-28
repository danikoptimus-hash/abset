"""Part 2 (removable columns) at the HTTP layer: a persisted exclusion list is
saved from creation AND edit, applied on read (columns/preview), survives an
SQL refresh, restores, protects the ID column, and warns for used columns."""

from __future__ import annotations

import time

from sqlalchemy import text as sa_text
from sqlalchemy.engine import make_url

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


def _results_csv(n=60):
    rows = ["user_id,revenue,group"]
    for i in range(n):
        rows.append(f"u{i},{100 + i % 7},{'A' if i % 2 == 0 else 'B'}")
    return "\n".join(rows) + "\n"


def test_exclude_via_edit_hides_column_from_reads_and_restores(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    ds = _upload(app_client, "results.csv", _results_csv())
    assert set(ds["columns"]) == {"user_id", "revenue", "group"}

    # Exclude 'group' via Edit.
    patched = app_client.patch(
        f"/api/v1/datasets/{ds['id']}", json={"excluded_columns": ["group"]},
    )
    assert patched.status_code == 200, patched.text
    out = patched.json()["dataset"]
    assert out["columns"] == ["user_id", "revenue"]  # visible set, order preserved
    assert out["excluded_columns"] == ["group"]

    # A fresh GET reflects it — every picker reads `columns`, so 'group' is gone.
    row = next(
        d for d in app_client.get("/api/v1/datasets", params={"page_size": 200}).json()["items"]
        if d["id"] == ds["id"]
    )
    assert "group" not in row["columns"]

    # Preview must not show the excluded column either.
    preview = app_client.get(f"/api/v1/datasets/{ds['id']}/preview").json()
    assert "group" not in preview["columns"]
    assert all("group" not in r for r in preview["rows"])

    # Restore: clear the exclusion — the column comes back.
    restored = app_client.patch(
        f"/api/v1/datasets/{ds['id']}", json={"excluded_columns": []},
    )
    assert restored.status_code == 200, restored.text
    assert set(restored.json()["dataset"]["columns"]) == {"user_id", "revenue", "group"}


def _design(app_client, name, dataset_id):
    resp = app_client.post(
        "/api/v1/design",
        json={
            "config": {
                "name": name, "unit_col": "user_id",
                "groups": {"control": 0.5, "treatment": 0.5},
                "metrics": [{"name": "revenue", "type": "continuous", "role": "primary"}],
                "sample_size": 40, "split_method": "simple", "isolation": "off",
            },
            "dataset_id": dataset_id,
        },
    )
    assert resp.status_code == 202, resp.text
    assert _poll(app_client, resp.json()["job_id"])["status"] == "completed"


def test_id_column_protected_metric_column_warns(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client)
    ds = _upload(app_client, "results.csv", _results_csv())
    _design(app_client, "usage_exp", ds["id"])

    # Column-usage: user_id is the unit (ID), revenue is a metric.
    usage = app_client.get(f"/api/v1/datasets/{ds['id']}/column-usage").json()["usage"]
    assert any(r["role"] == "unit" for r in usage["user_id"])
    assert any(r["role"] == "metric" and r["experiment"] == "usage_exp" for r in usage["revenue"])

    # Excluding the ID column is hard-blocked (400 protected_column).
    blocked = app_client.patch(
        f"/api/v1/datasets/{ds['id']}", json={"excluded_columns": ["user_id"]},
    )
    assert blocked.status_code == 400, blocked.text
    assert blocked.json()["error"]["code"] == "protected_column"

    # Excluding a metric column is allowed (the UI warned+confirmed first).
    ok = app_client.patch(
        f"/api/v1/datasets/{ds['id']}", json={"excluded_columns": ["revenue"]},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["dataset"]["excluded_columns"] == ["revenue"]


# ---- SQL source: exclusion persists from creation and survives refresh ----

def _seed_table(db_url, n=50):
    from sqlalchemy import create_engine

    engine = create_engine(db_url, future=True)
    with engine.begin() as conn:
        conn.execute(sa_text("DROP TABLE IF EXISTS excl_probe"))
        conn.execute(sa_text("CREATE TABLE excl_probe (user_id TEXT, revenue FLOAT, grp TEXT)"))
        conn.execute(
            sa_text(
                "INSERT INTO excl_probe SELECT 'u' || g, 100 + (g % 10), "
                "CASE WHEN g % 2 = 0 THEN 'A' ELSE 'B' END FROM generate_series(1, :n) AS g"
            ),
            {"n": n},
        )
    engine.dispose()


def _create_connection(app_client, db_url) -> str:
    url = make_url(db_url)
    resp = app_client.post(
        "/api/v1/admin/db-connections",
        json={
            "display_name": "Self", "engine": "postgresql", "host": url.host, "port": url.port,
            "database": url.database, "username": url.username, "password": url.password, "ssl": False,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_sql_exclusion_from_creation_survives_refresh(app_client, db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _seed_table(db_url, n=50)
    _login(app_client, role="admin")  # admin: also allowed to create connections
    conn_id = _create_connection(app_client, db_url)

    # Create From SQL with an exclusion already set (creation-time removal).
    resp = app_client.post(
        "/api/v1/datasets/from-sql",
        json={
            "connection_id": conn_id, "sql": "SELECT user_id, revenue, grp FROM excl_probe",
            "name": "sql_excl", "kind": "post_analysis", "excluded_columns": ["grp"],
        },
    )
    job = _poll(app_client, resp.json()["job_id"])
    assert job["status"] == "completed", job
    dataset_id = job["result"]["dataset_id"]

    def _entry():
        items = app_client.get("/api/v1/datasets", params={"page_size": 200}).json()["items"]
        return next(d for d in items if d["id"] == dataset_id)

    assert _entry()["excluded_columns"] == ["grp"]
    assert "grp" not in _entry()["columns"]

    # Refresh (re-fetch brings 'grp' back into the raw snapshot) — the KEY case:
    # the stored exclusion must re-apply, not silently disappear.
    _seed_table(db_url, n=80)
    refresh = app_client.post(f"/api/v1/datasets/{dataset_id}/refresh")
    assert refresh.status_code == 202
    assert _poll(app_client, refresh.json()["job_id"])["status"] == "completed"

    entry = _entry()
    assert entry["n_rows"] == 80  # refreshed
    assert entry["excluded_columns"] == ["grp"]  # exclusion survived
    assert "grp" not in entry["columns"]
