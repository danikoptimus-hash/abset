"""R3 (FRONTEND.md §3.2/§4): GET /jobs/{id} edge cases + JobRunner unit
behavior (startup repair of unfinished jobs)."""

from __future__ import annotations

import time

from abkit.auth.passwords import hash_password
from abkit.db.repositories import JobRepo, UserRepo


def _login(app_client, email="viewer@co.com", role="viewer"):
    UserRepo().create(email=email, first_name="V", password_hash=hash_password("pw12345"), role=role)
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


def _wait_for_terminal_status(job_id, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        fetched = JobRepo().get_by_id(job_id)
        if fetched.status in ("completed", "failed", "requires_confirmation"):
            return fetched
        time.sleep(0.02)
    raise AssertionError(f"Job {job_id} did not reach a terminal status within {timeout}s")


def test_job_runner_marks_job_failed_on_raw_exception_with_generic_message(db_url):
    """A raw/technical exception (not one of our human-readable domain
    errors) must never leak str(e) to job.error — the job still ends up
    failed (not stuck/lost), just with a generic message; full detail goes
    to the log instead (backend/jobs/runner.py::_human_readable_message).
    16 (samples-download-bug follow-up): the generic message must carry an
    error_id — same reasoning as backend/errors.py's HTTP-level handler, a
    bare "Internal processing error" with no way to correlate it to a log
    line was exactly what made a real production regression hard to
    diagnose."""
    import re

    from backend.jobs.runner import JobRunner

    runner = JobRunner(max_workers=1)
    try:
        def _fn(reporter):
            raise ValueError("You are trying to merge on str and int64 columns for key 'unit_id'")

        job = runner.submit("analyze", None, _fn)
        fetched = _wait_for_terminal_status(job.id)
        assert fetched.status == "failed"
        match = re.fullmatch(r"Internal processing error \(ref: [0-9a-f]{8}\)", fetched.error)
        assert match, f"expected a generic message with a (ref: <8 hex>) suffix, got {fetched.error!r}"
    finally:
        runner.shutdown(wait=True)


def test_job_runner_marks_job_failed_with_domain_error_message_preserved(db_url):
    """A known domain error (AnalysisError etc.) already carries a message
    written for the user — it must pass through unchanged, not get
    replaced by the generic fallback."""
    from abkit.checks import AnalysisError
    from backend.jobs.runner import JobRunner

    runner = JobRunner(max_workers=1)
    try:
        def _fn(reporter):
            raise AnalysisError("The data has 3 duplicate 'user_id' values — cannot analyze")

        job = runner.submit("analyze", None, _fn)
        fetched = _wait_for_terminal_status(job.id)
        assert fetched.status == "failed"
        assert fetched.error == "The data has 3 duplicate 'user_id' values — cannot analyze"
    finally:
        runner.shutdown(wait=True)


def test_job_runner_marks_job_failed_on_base_exception_not_just_exception(db_url):
    """Job wrapper must catch BaseException, not just Exception — a job must
    never disappear or hang in 'running' regardless of what it raises."""
    import re

    from backend.jobs.runner import JobRunner

    runner = JobRunner(max_workers=1)
    try:
        def _fn(reporter):
            raise SystemExit("simulated abrupt worker exit")

        job = runner.submit("analyze", None, _fn)
        fetched = _wait_for_terminal_status(job.id)
        assert fetched.status == "failed"
        assert re.fullmatch(r"Internal processing error \(ref: [0-9a-f]{8}\)", fetched.error)
    finally:
        runner.shutdown(wait=True)


def test_job_runner_heartbeat_sweep_fails_stale_running_job(db_url):
    """A job stuck in 'running' whose heartbeat (updated_at) hasn't moved in
    longer than the timeout is a dead/hung worker (e.g. the process was
    OOM-killed without raising a catchable exception) — the periodic sweep
    must mark it failed instead of leaving it running forever."""
    from datetime import datetime, timedelta, timezone

    from abkit.db.engine import session_scope
    from abkit.db.models import Job
    from backend.jobs.runner import JobRunner

    stale = JobRepo().create(type="analyze")
    JobRepo().mark_running(stale.id)
    with session_scope() as s:
        row = s.get(Job, stale.id)
        row.updated_at = datetime.now(timezone.utc) - timedelta(minutes=31)

    runner = JobRunner(max_workers=1)
    try:
        runner._sweep_stale_jobs()
        fetched = JobRepo().get_by_id(stale.id)
        assert fetched.status == "failed"
        assert fetched.error == "Job timed out or worker died"
    finally:
        runner.shutdown(wait=True)


def test_job_runner_heartbeat_sweep_leaves_recent_running_job_alone(db_url):
    fresh = JobRepo().create(type="analyze")
    JobRepo().mark_running(fresh.id)

    from backend.jobs.runner import JobRunner

    runner = JobRunner(max_workers=1)
    try:
        runner._sweep_stale_jobs()
        assert JobRepo().get_by_id(fresh.id).status == "running"
    finally:
        runner.shutdown(wait=True)
