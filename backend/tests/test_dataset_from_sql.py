"""DB2 (CLAUDE.md dataset-from-SQL feature): POST /datasets/from-sql (async
job) -> dataset usable for design; POST /db-connections/{id}/preview; POST
/datasets/{id}/refresh — end to end via the real API, self-referencing the
same testcontainers-postgres this test session already runs against."""

from __future__ import annotations

import time
from pathlib import Path

from sqlalchemy import text as sa_text
from sqlalchemy.engine import make_url

from abkit.auth.passwords import hash_password
from abkit.db.repositories import UserRepo


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


def _seed_table(db_url, n=200):
    from sqlalchemy import create_engine

    engine = create_engine(db_url, future=True)
    with engine.begin() as conn:
        conn.execute(sa_text("DROP TABLE IF EXISTS from_sql_probe"))
        conn.execute(sa_text("CREATE TABLE from_sql_probe (user_id TEXT, revenue FLOAT)"))
        conn.execute(
            sa_text(
                "INSERT INTO from_sql_probe SELECT 'u' || g, 100 + (g % 10) "
                "FROM generate_series(1, :n) AS g"
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


def test_from_sql_dataset_end_to_end_usable_for_design(app_client, db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _seed_table(db_url, n=200)
    _login(app_client, role="admin")  # admin: also allowed to create connections
    conn_id = _create_connection(app_client, db_url)

    resp = app_client.post(
        "/api/v1/datasets/from-sql",
        json={
            "connection_id": conn_id, "sql": "SELECT user_id, revenue FROM from_sql_probe",
            "name": "sql_design_data", "kind": "pre_design",
        },
    )
    assert resp.status_code == 202, resp.text
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "completed", job
    dataset_id = job["result"]["dataset_id"]
    assert job["result"]["n_rows"] == 200
    assert job["result"]["truncated"] is False

    ds = app_client.get("/api/v1/datasets").json()
    entry = next(d for d in ds["items"] if d["id"] == dataset_id)
    assert entry["source"] == "sql"
    assert entry["connection_name"] == "Self"
    assert entry["n_rows"] == 200

    preview = app_client.get(f"/api/v1/datasets/{dataset_id}/preview", params={"rows": 5})
    assert preview.status_code == 200
    assert len(preview.json()["rows"]) == 5

    design_resp = app_client.post(
        "/api/v1/design",
        json={
            "config": {
                "name": "from_sql_exp", "unit_col": "user_id",
                "groups": {"control": 0.5, "treatment": 0.5},
                "metrics": [{"name": "revenue", "type": "continuous", "role": "primary"}],
                "sample_size": 200, "split_method": "simple", "isolation": "off",
            },
            "dataset_id": dataset_id,
        },
    )
    assert design_resp.status_code == 202
    design_job = _poll_job(app_client, design_resp.json()["job_id"])
    assert design_job["status"] == "completed", design_job


def test_from_sql_rejects_non_select(app_client, db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _seed_table(db_url, n=5)
    _login(app_client, role="admin")
    conn_id = _create_connection(app_client, db_url)

    resp = app_client.post(
        "/api/v1/datasets/from-sql",
        json={
            "connection_id": conn_id, "sql": "DELETE FROM from_sql_probe",
            "name": "bad", "kind": "pre_design",
        },
    )
    assert resp.status_code == 202
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "failed"
    assert "SELECT" in job["error"] or "select" in job["error"].lower()


def test_from_sql_truncates_at_max_rows_env(app_client, db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ABKIT_SQL_MAX_ROWS", "30")
    _seed_table(db_url, n=200)
    _login(app_client, role="admin")
    conn_id = _create_connection(app_client, db_url)

    resp = app_client.post(
        "/api/v1/datasets/from-sql",
        json={
            "connection_id": conn_id, "sql": "SELECT user_id, revenue FROM from_sql_probe",
            "name": "truncated", "kind": "pre_design",
        },
    )
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "completed", job
    assert job["result"]["n_rows"] == 30
    assert job["result"]["truncated"] is True


def test_preview_connection_sql(app_client, db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _seed_table(db_url, n=200)
    _login(app_client, role="admin")
    conn_id = _create_connection(app_client, db_url)

    resp = app_client.post(
        f"/api/v1/db-connections/{conn_id}/preview",
        json={"sql": "SELECT user_id, revenue FROM from_sql_probe ORDER BY user_id"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["rows"]) == 100  # default preview batch size
    assert set(body["columns"]) == {"user_id", "revenue"}


def test_preview_connection_sql_rejects_non_select(app_client, db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _seed_table(db_url, n=5)
    _login(app_client, role="admin")
    conn_id = _create_connection(app_client, db_url)

    resp = app_client.post(
        f"/api/v1/db-connections/{conn_id}/preview", json={"sql": "DROP TABLE from_sql_probe"},
    )
    assert resp.status_code == 422


def test_list_connection_schemas_includes_public(app_client, db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _seed_table(db_url, n=5)
    _login(app_client, role="admin")
    conn_id = _create_connection(app_client, db_url)

    resp = app_client.get(f"/api/v1/db-connections/{conn_id}/schemas")
    assert resp.status_code == 200, resp.text
    assert "public" in resp.json()["schemas"]


def test_list_connection_tables_in_schema(app_client, db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _seed_table(db_url, n=5)
    _login(app_client, role="admin")
    conn_id = _create_connection(app_client, db_url)

    resp = app_client.get(f"/api/v1/db-connections/{conn_id}/schemas/public/tables")
    assert resp.status_code == 200, resp.text
    assert "from_sql_probe" in resp.json()["tables"]


def test_list_connection_tables_cached_until_refresh(app_client, db_url, tmp_path, monkeypatch):
    """60s TTL cache (UX-package, Datasets п.1.2) — a second call without
    ?refresh=true returns the cached list even after a new table appears;
    ?refresh=true (the selects' 🗘 button) bypasses it."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _seed_table(db_url, n=5)
    _login(app_client, role="admin")
    conn_id = _create_connection(app_client, db_url)

    first = app_client.get(f"/api/v1/db-connections/{conn_id}/schemas/public/tables")
    assert "brand_new_table" not in first.json()["tables"]

    from sqlalchemy import create_engine
    from sqlalchemy import text as sa_text

    engine = create_engine(db_url, future=True)
    with engine.begin() as conn:
        conn.execute(sa_text("CREATE TABLE brand_new_table (id INT)"))
    engine.dispose()

    cached = app_client.get(f"/api/v1/db-connections/{conn_id}/schemas/public/tables")
    assert "brand_new_table" not in cached.json()["tables"]

    refreshed = app_client.get(f"/api/v1/db-connections/{conn_id}/schemas/public/tables?refresh=true")
    assert "brand_new_table" in refreshed.json()["tables"]


def test_list_connection_schemas_requires_editor(app_client, db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client, role="viewer")
    resp = app_client.get("/api/v1/db-connections/00000000-0000-0000-0000-000000000000/schemas")
    assert resp.status_code == 403


def test_refresh_sql_dataset(app_client, db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _seed_table(db_url, n=50)
    _login(app_client, role="admin")
    conn_id = _create_connection(app_client, db_url)

    resp = app_client.post(
        "/api/v1/datasets/from-sql",
        json={
            "connection_id": conn_id, "sql": "SELECT user_id, revenue FROM from_sql_probe",
            "name": "refreshable", "kind": "post_analysis",
        },
    )
    job = _poll_job(app_client, resp.json()["job_id"])
    dataset_id = job["result"]["dataset_id"]
    assert job["result"]["n_rows"] == 50

    _seed_table(db_url, n=80)  # underlying data changed
    refresh_resp = app_client.post(f"/api/v1/datasets/{dataset_id}/refresh")
    assert refresh_resp.status_code == 202
    refresh_job = _poll_job(app_client, refresh_resp.json()["job_id"])
    assert refresh_job["status"] == "completed", refresh_job
    assert refresh_job["result"]["n_rows"] == 80

    ds = app_client.get("/api/v1/datasets").json()
    entry = next(d for d in ds["items"] if d["id"] == dataset_id)
    assert entry["n_rows"] == 80
    assert entry["fetched_at"] is not None


def test_refresh_leaves_old_snapshot_untouched_on_mid_fetch_failure(app_client, db_url, tmp_path, monkeypatch):
    """UX-package, Datasets п.1.4: a source error (dropped connection,
    table gone) during refresh must not corrupt the existing snapshot —
    execute_select_to_parquet fetches into a temp file that's only swapped
    in on success (abkit/jobs.py::run_refresh_sql_dataset)."""
    import abkit.db_connections.sql_dataset as sql_dataset_module

    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _seed_table(db_url, n=50)
    _login(app_client, role="admin")
    conn_id = _create_connection(app_client, db_url)

    resp = app_client.post(
        "/api/v1/datasets/from-sql",
        json={
            "connection_id": conn_id, "sql": "SELECT user_id, revenue FROM from_sql_probe",
            "name": "refresh_failure", "kind": "post_analysis",
        },
    )
    job = _poll_job(app_client, resp.json()["job_id"])
    dataset_id = job["result"]["dataset_id"]

    ds_before = app_client.get("/api/v1/datasets").json()
    entry_before = next(d for d in ds_before["items"] if d["id"] == dataset_id)
    # storage_path isn't in the API response — resolve it from disk instead:
    # the from-sql upload dir has exactly one file for this fresh dataset.
    uploads_dir = Path(tmp_path) / "_uploads"
    original_files = list(uploads_dir.glob("*refresh_failure.parquet"))
    assert len(original_files) == 1
    original_path = original_files[0]
    original_bytes = original_path.read_bytes()

    def _boom(*args, **kwargs):
        raise RuntimeError("source unreachable mid-fetch")

    monkeypatch.setattr(sql_dataset_module, "execute_select_to_parquet", _boom)

    refresh_resp = app_client.post(f"/api/v1/datasets/{dataset_id}/refresh")
    assert refresh_resp.status_code == 202
    refresh_job = _poll_job(app_client, refresh_resp.json()["job_id"])
    assert refresh_job["status"] == "failed"

    assert original_path.read_bytes() == original_bytes
    # no stray temp file left behind either
    assert list(uploads_dir.glob(".refresh_*")) == []

    ds_after = app_client.get("/api/v1/datasets").json()
    entry_after = next(d for d in ds_after["items"] if d["id"] == dataset_id)
    assert entry_after["n_rows"] == entry_before["n_rows"]


def test_from_sql_dataset_persists_source_schema_table_when_provided(app_client, db_url, tmp_path, monkeypatch):
    """Datasets follow-up (persist source schema/table): the create form
    sends source_schema/source_table when the SQL box still exactly matches
    a cascade table pick — must round-trip through GET /datasets."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _seed_table(db_url, n=5)
    _login(app_client, role="admin")
    conn_id = _create_connection(app_client, db_url)

    resp = app_client.post(
        "/api/v1/datasets/from-sql",
        json={
            "connection_id": conn_id, "sql": 'SELECT * FROM "public"."from_sql_probe"',
            "name": "with_source", "kind": "pre_design",
            "source_schema": "public", "source_table": "from_sql_probe",
        },
    )
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "completed", job
    dataset_id = job["result"]["dataset_id"]

    ds = app_client.get("/api/v1/datasets").json()
    entry = next(d for d in ds["items"] if d["id"] == dataset_id)
    assert entry["source_schema"] == "public"
    assert entry["source_table"] == "from_sql_probe"


def test_from_sql_dataset_source_schema_table_default_null(app_client, db_url, tmp_path, monkeypatch):
    """A hand-written query (no cascade pick) leaves source_schema/table
    null — no guessing at creation time either."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _seed_table(db_url, n=5)
    _login(app_client, role="admin")
    conn_id = _create_connection(app_client, db_url)

    resp = app_client.post(
        "/api/v1/datasets/from-sql",
        json={
            "connection_id": conn_id, "sql": "SELECT user_id FROM from_sql_probe WHERE revenue > 100",
            "name": "no_source", "kind": "pre_design",
        },
    )
    job = _poll_job(app_client, resp.json()["job_id"])
    assert job["status"] == "completed", job
    dataset_id = job["result"]["dataset_id"]

    ds = app_client.get("/api/v1/datasets").json()
    entry = next(d for d in ds["items"] if d["id"] == dataset_id)
    assert entry["source_schema"] is None
    assert entry["source_table"] is None


def test_patch_dataset_sql_clears_source_schema_table_when_hand_edited(app_client, db_url, tmp_path, monkeypatch):
    """Datasets follow-up §2: editing the SQL so it no longer matches the
    stored table must clear source_schema/source_table, not leave a stale
    (lying) pointer at the old table."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _seed_table(db_url, n=5)
    _login(app_client, role="admin")
    conn_id = _create_connection(app_client, db_url)

    resp = app_client.post(
        "/api/v1/datasets/from-sql",
        json={
            "connection_id": conn_id, "sql": 'SELECT * FROM "public"."from_sql_probe"',
            "name": "patchable", "kind": "pre_design",
            "source_schema": "public", "source_table": "from_sql_probe",
        },
    )
    job = _poll_job(app_client, resp.json()["job_id"])
    dataset_id = job["result"]["dataset_id"]

    patch_resp = app_client.patch(
        f"/api/v1/datasets/{dataset_id}",
        json={"sql_text": "SELECT user_id FROM from_sql_probe WHERE revenue > 100"},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    job_id = patch_resp.json()["job_id"]
    assert job_id is not None
    _poll_job(app_client, job_id)

    ds = app_client.get("/api/v1/datasets").json()
    entry = next(d for d in ds["items"] if d["id"] == dataset_id)
    assert entry["source_schema"] is None
    assert entry["source_table"] is None


def test_patch_dataset_sql_sets_source_schema_table_when_provided(app_client, db_url, tmp_path, monkeypatch):
    """Inverse of the above: patching SQL together with a fresh cascade pick
    updates source_schema/source_table to the new values."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _seed_table(db_url, n=5)
    _login(app_client, role="admin")
    conn_id = _create_connection(app_client, db_url)

    resp = app_client.post(
        "/api/v1/datasets/from-sql",
        json={
            "connection_id": conn_id, "sql": "SELECT user_id FROM from_sql_probe",
            "name": "patchable2", "kind": "pre_design",
        },
    )
    job = _poll_job(app_client, resp.json()["job_id"])
    dataset_id = job["result"]["dataset_id"]

    patch_resp = app_client.patch(
        f"/api/v1/datasets/{dataset_id}",
        json={
            "sql_text": 'SELECT * FROM "public"."from_sql_probe"',
            "source_schema": "public", "source_table": "from_sql_probe",
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    _poll_job(app_client, patch_resp.json()["job_id"])

    ds = app_client.get("/api/v1/datasets").json()
    entry = next(d for d in ds["items"] if d["id"] == dataset_id)
    assert entry["source_schema"] == "public"
    assert entry["source_table"] == "from_sql_probe"


def test_refresh_rejects_non_sql_dataset(app_client, db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client, role="editor")
    upload_resp = app_client.post(
        "/api/v1/datasets",
        data={"kind": "pre_design"},
        files={"file": ("data.csv", "user_id,revenue\nu1,10\n", "text/csv")},
    )
    dataset_id = upload_resp.json()["id"]

    refresh_resp = app_client.post(f"/api/v1/datasets/{dataset_id}/refresh")
    assert refresh_resp.status_code == 202
    job = _poll_job(app_client, refresh_resp.json()["job_id"])
    assert job["status"] == "failed"
    assert "SQL" in job["error"] or "sql" in job["error"].lower()
