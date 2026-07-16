"""Admin monitoring panel API: GET /admin/monitoring/current, GET .../history,
POST .../snapshot-now — admin-only (403 for editor/viewer); plus
GET /jobs/{id} carrying peak_memory_mb through."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from abkit.auth.passwords import hash_password
from abkit.db.repositories import JobRepo, MonitoringRepo, UserRepo


def _login(app_client, email="editor@co.com", role="admin"):
    UserRepo().create(email=email, first_name="E", password_hash=hash_password("pw12345"), role=role)
    app_client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345"})


def test_current_requires_admin(app_client, db_url, monkeypatch, tmp_path):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    for role in ("editor", "viewer"):
        _login(app_client, email=f"{role}@co.com", role=role)
        resp = app_client.get("/api/v1/admin/monitoring/current")
        assert resp.status_code == 403
        app_client.post("/api/v1/auth/logout")


def test_history_requires_admin(app_client, db_url, monkeypatch, tmp_path):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    now = datetime.now(timezone.utc).isoformat()
    for role in ("editor", "viewer"):
        _login(app_client, email=f"{role}@co.com", role=role)
        resp = app_client.get(
            "/api/v1/admin/monitoring/history",
            params={"from": now, "to": now, "resolution": "raw"},
        )
        assert resp.status_code == 403
        app_client.post("/api/v1/auth/logout")


def test_snapshot_now_requires_admin(app_client, db_url, monkeypatch, tmp_path):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    for role in ("editor", "viewer"):
        _login(app_client, email=f"{role}@co.com", role=role)
        resp = app_client.post("/api/v1/admin/monitoring/snapshot-now")
        assert resp.status_code == 403
        app_client.post("/api/v1/auth/logout")


def test_snapshot_now_inserts_and_current_reflects_it(app_client, db_url, monkeypatch, tmp_path):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client, role="admin")

    resp = app_client.post("/api/v1/admin/monitoring/snapshot-now")
    assert resp.status_code == 200
    body = resp.json()
    assert body["backend_rss_mb"] > 0
    assert body["db_total_mb"] > 0
    assert body["disk_total_mb"] > 0
    assert body["active_jobs"] == 0

    current = app_client.get("/api/v1/admin/monitoring/current").json()
    assert current["backend_rss_mb"] == body["backend_rss_mb"]
    assert current["ts"] is not None


def test_current_before_any_snapshot_returns_nulls_not_an_error(app_client, db_url, monkeypatch, tmp_path):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client, role="admin")

    resp = app_client.get("/api/v1/admin/monitoring/current")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ts"] is None
    assert body["backend_rss_mb"] is None
    # top_tables/disk_total_mb are queried fresh (not from the empty
    # snapshot history), so they're populated even with zero history.
    assert body["disk_total_mb"] > 0
    assert isinstance(body["top_tables"], list) and len(body["top_tables"]) > 0


def test_current_includes_top_tables(app_client, db_url, monkeypatch, tmp_path):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client, role="admin")

    current = app_client.get("/api/v1/admin/monitoring/current").json()
    table_names = [t["table_name"] for t in current["top_tables"]]
    assert any(name.endswith(".users") for name in table_names)
    assert all(t["size_bytes"] >= 0 for t in current["top_tables"])


def test_current_includes_mem_limit_and_bloated_tables(app_client, db_url, monkeypatch, tmp_path):
    """Item A2/B2 — both are computed fresh on every /current call, not
    stored history columns; mem limit is None off-Docker (no cgroup files
    on this test machine), bloated_tables is empty on a freshly-migrated
    test DB with no meaningful dead-tuple buildup."""
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client, role="admin")

    current = app_client.get("/api/v1/admin/monitoring/current").json()
    assert "backend_mem_limit_mb" in current
    assert current["bloated_tables"] == []


def test_current_surfaces_bloated_tables_from_find_bloated_tables(app_client, db_url, monkeypatch, tmp_path):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client, role="admin")

    from abkit.db.maintenance import TableBloatInfo

    monkeypatch.setattr(
        "abkit.db.maintenance.find_bloated_tables",
        lambda: [TableBloatInfo(table_name="assignments", dead_pct=87.5, size_mb=2183.0)],
    )

    current = app_client.get("/api/v1/admin/monitoring/current").json()
    assert current["bloated_tables"] == [{"table_name": "assignments", "dead_pct": 87.5, "size_mb": 2183.0}]


def test_current_mem_limit_reflects_read_memory_limit_mb(app_client, db_url, monkeypatch, tmp_path):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client, role="admin")

    # backend/routers/admin.py imports read_memory_limit_mb by name at
    # module load time (not a lazy/local import like find_bloated_tables
    # above) — the patch target is the router module's own binding.
    monkeypatch.setattr("backend.routers.admin.read_memory_limit_mb", lambda: 4096.0)
    current = app_client.get("/api/v1/admin/monitoring/current").json()
    assert current["backend_mem_limit_mb"] == 4096.0


def test_history_filters_by_range_and_resolution(app_client, db_url, monkeypatch, tmp_path):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    _login(app_client, role="admin")

    repo = MonitoringRepo()
    now = datetime.now(timezone.utc)
    in_range = now - timedelta(hours=1)
    out_of_range = now - timedelta(days=10)
    repo.insert_raw(
        ts=in_range, backend_rss_mb=111.0, db_total_mb=1.0, data_volume_mb=1.0, disk_free_mb=1.0, active_jobs=0
    )
    repo.insert_raw(
        ts=out_of_range, backend_rss_mb=222.0, db_total_mb=1.0, data_volume_mb=1.0, disk_free_mb=1.0, active_jobs=0
    )
    repo.insert_hourly(
        [
            {
                "ts": in_range.replace(minute=0, second=0, microsecond=0),
                "resolution": "hourly",
                "backend_rss_mb": 555.0,
                "backend_rss_mb_min": 500.0,
                "backend_rss_mb_max": 600.0,
                "active_jobs": None,
            }
        ]
    )

    resp = app_client.get(
        "/api/v1/admin/monitoring/history",
        params={
            "from": (now - timedelta(days=1)).isoformat(),
            "to": now.isoformat(),
            "resolution": "raw",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"] == "raw"
    values = [p["backend_rss_mb"] for p in body["points"]]
    assert values == [111.0]  # in_range only — out_of_range is outside from/to

    resp_hourly = app_client.get(
        "/api/v1/admin/monitoring/history",
        params={
            "from": (now - timedelta(days=1)).isoformat(),
            "to": now.isoformat(),
            "resolution": "hourly",
        },
    )
    hourly_points = resp_hourly.json()["points"]
    assert len(hourly_points) == 1
    assert hourly_points[0]["backend_rss_mb"] == 555.0
    assert hourly_points[0]["backend_rss_mb_min"] == 500.0
    assert hourly_points[0]["backend_rss_mb_max"] == 600.0


def test_job_out_includes_peak_memory_mb(app_client, db_url):
    _login(app_client, role="viewer")  # GET /jobs/{id} is open to any logged-in role
    job = JobRepo().create(type="test")
    JobRepo().update_peak_memory(job.id, 42.5)

    resp = app_client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200
    assert resp.json()["peak_memory_mb"] == 42.5


def test_job_out_peak_memory_null_when_never_sampled(app_client, db_url):
    _login(app_client, role="viewer")
    job = JobRepo().create(type="test")

    resp = app_client.get(f"/api/v1/jobs/{job.id}")
    assert resp.json()["peak_memory_mb"] is None
