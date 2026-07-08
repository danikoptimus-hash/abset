"""R3 (FRONTEND.md §3.2/§4): GET /jobs/{id} edge cases + JobRunner unit
behavior (startup repair of unfinished jobs)."""

from __future__ import annotations

import time

from abkit.auth.passwords import hash_password
from abkit.db.repositories import JobRepo, UserRepo


def _login(app_client, email="viewer@co.com", role="viewer"):
    UserRepo().create(email=email, name="V", password_hash=hash_password("pw12345"), role=role)
    app_client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345"})


def test_get_job_requires_login(app_client):
    resp = app_client.get("/api/v1/jobs/11111111-1111-1111-1111-111111111111")
    assert resp.status_code == 401


def test_get_job_404_for_unknown_id(app_client):
    _login(app_client)
    resp = app_client.get("/api/v1/jobs/11111111-1111-1111-1111-111111111111")
    assert resp.status_code == 404


def test_get_job_422_for_malformed_id(app_client):
    _login(app_client)
    resp = app_client.get("/api/v1/jobs/not-a-uuid")
    assert resp.status_code == 422


def test_get_job_visible_to_any_logged_in_user(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    job = JobRepo().create(type="design")
    _login(app_client, email="anyone9@co.com", role="viewer")
    resp = app_client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


def test_job_runner_submit_completes_and_reports_progress(db_url):
    from backend.jobs.runner import JobRunner

    runner = JobRunner(max_workers=1)
    try:
        def _fn(reporter):
            reporter.stage("шаг 1")
            return {"ok": True}

        job = runner.submit("design", None, _fn)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            fetched = JobRepo().get_by_id(job.id)
            if fetched.status == "completed":
                break
            time.sleep(0.02)
        assert fetched.status == "completed"
        assert fetched.result_ref == {"ok": True}
    finally:
        runner.shutdown(wait=True)


def test_job_runner_marks_unfinished_jobs_failed_on_startup(db_url):
    from backend.jobs.runner import JobRunner

    stale_pending = JobRepo().create(type="design")
    stale_running = JobRepo().create(type="analyze")
    JobRepo().mark_running(stale_running.id)

    runner = JobRunner(max_workers=1)
    try:
        runner.mark_unfinished_jobs_failed_on_startup()
        assert JobRepo().get_by_id(stale_pending.id).status == "failed"
        assert JobRepo().get_by_id(stale_running.id).status == "failed"
    finally:
        runner.shutdown(wait=True)
