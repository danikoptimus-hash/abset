"""Admin monitoring panel: abkit/monitoring.py.

plan_retention()/dir_size_mb() are pure (no DB) — tested directly with an
explicit `now` parameter instead of mocking the clock. MonitoringCollector/
MonitoringRepo need a real Postgres (db_url, testcontainers) for the
insert/query/retention round trip.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from abkit.db.repositories import JobRepo, MonitoringRepo
from abkit.monitoring import MonitoringCollector, dir_size_mb, plan_retention


@dataclass
class _RawRow:
    """Minimal stand-in for a MonitoringSnapshot ORM row — plan_retention()
    only reads .id/.ts/the metric attributes, so a plain dataclass is enough
    to test it without touching a database."""

    id: int
    ts: datetime
    backend_rss_mb: float | None = None
    db_total_mb: float | None = None
    data_volume_mb: float | None = None
    disk_free_mb: float | None = None


def _row(id_, hour, minute, **metrics):
    return _RawRow(id=id_, ts=datetime(2026, 7, 16, hour, minute, tzinfo=timezone.utc), **metrics)


def test_plan_retention_empty_input_is_a_noop():
    plan = plan_retention([], now=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc))
    assert plan.hourly_rows == []
    assert plan.raw_ids_to_delete == []


def test_plan_retention_buckets_by_hour_and_aggregates_min_avg_max():
    rows = [
        _row(1, 10, 0, backend_rss_mb=100.0, db_total_mb=10.0, data_volume_mb=1.0, disk_free_mb=500.0),
        _row(2, 10, 20, backend_rss_mb=200.0, db_total_mb=10.0, data_volume_mb=1.0, disk_free_mb=500.0),
        _row(3, 10, 40, backend_rss_mb=300.0, db_total_mb=10.0, data_volume_mb=1.0, disk_free_mb=500.0),
    ]
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)  # well past 10:00-11:00
    plan = plan_retention(rows, now=now)

    assert len(plan.hourly_rows) == 1
    bucket = plan.hourly_rows[0]
    assert bucket["ts"] == datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
    assert bucket["resolution"] == "hourly"
    assert bucket["active_jobs"] is None
    assert bucket["backend_rss_mb"] == pytest.approx(200.0)  # avg(100,200,300)
    assert bucket["backend_rss_mb_min"] == pytest.approx(100.0)
    assert bucket["backend_rss_mb_max"] == pytest.approx(300.0)
    assert bucket["db_total_mb"] == pytest.approx(10.0)
    assert set(plan.raw_ids_to_delete) == {1, 2, 3}


def test_plan_retention_keeps_separate_hours_separate():
    rows = [
        _row(1, 10, 0, backend_rss_mb=100.0),
        _row(2, 11, 0, backend_rss_mb=900.0),
    ]
    now = datetime(2026, 7, 16, 13, 0, tzinfo=timezone.utc)
    plan = plan_retention(rows, now=now)

    assert len(plan.hourly_rows) == 2
    by_ts = {b["ts"]: b for b in plan.hourly_rows}
    assert by_ts[datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)]["backend_rss_mb"] == pytest.approx(100.0)
    assert by_ts[datetime(2026, 7, 16, 11, 0, tzinfo=timezone.utc)]["backend_rss_mb"] == pytest.approx(900.0)


def test_plan_retention_skips_the_current_incomplete_hour():
    """A row that landed in `now`'s own hour must NOT be collapsed yet —
    that hour isn't finished, more raw points may still arrive for it."""
    now = datetime(2026, 7, 16, 12, 30, tzinfo=timezone.utc)
    rows = [
        _row(1, 10, 0, backend_rss_mb=100.0),  # complete hour -> collapsed
        _row(2, 12, 0, backend_rss_mb=999.0),  # current hour -> left alone
    ]
    plan = plan_retention(rows, now=now)

    assert len(plan.hourly_rows) == 1
    assert plan.hourly_rows[0]["ts"] == datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
    assert plan.raw_ids_to_delete == [1]


def test_plan_retention_none_values_excluded_from_aggregation():
    """A metric that failed to collect on some ticks (None) doesn't drag the
    average down or corrupt min/max — it's just excluded from that bucket's
    stats, not treated as 0."""
    rows = [
        _row(1, 10, 0, backend_rss_mb=100.0),
        _row(2, 10, 20, backend_rss_mb=None),
        _row(3, 10, 40, backend_rss_mb=300.0),
    ]
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    plan = plan_retention(rows, now=now)

    bucket = plan.hourly_rows[0]
    assert bucket["backend_rss_mb"] == pytest.approx(200.0)  # avg(100, 300), None skipped
    assert bucket["backend_rss_mb_min"] == pytest.approx(100.0)
    assert bucket["backend_rss_mb_max"] == pytest.approx(300.0)


def test_plan_retention_all_none_metric_stays_none_not_zero():
    rows = [_row(1, 10, 0, backend_rss_mb=None), _row(2, 10, 20, backend_rss_mb=None)]
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    plan = plan_retention(rows, now=now)

    bucket = plan.hourly_rows[0]
    assert bucket["backend_rss_mb"] is None
    assert bucket["backend_rss_mb_min"] is None
    assert bucket["backend_rss_mb_max"] is None


def test_dir_size_mb_sums_file_sizes(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 1000)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"y" * 2000)

    assert dir_size_mb(tmp_path) == pytest.approx(3000 / (1024 * 1024))


def test_dir_size_mb_empty_dir_is_zero(tmp_path):
    assert dir_size_mb(tmp_path) == 0.0


def test_collector_snapshot_now_inserts_a_raw_row(db_url, tmp_path):
    collector = MonitoringCollector(data_dir=tmp_path)
    point = collector.snapshot_now()

    assert point["backend_rss_mb"] is not None and point["backend_rss_mb"] > 0
    assert point["data_volume_mb"] == pytest.approx(0.0)  # tmp_path is empty
    assert point["active_jobs"] == 0

    latest = MonitoringRepo().latest()
    assert latest is not None
    assert latest.resolution == "raw"
    assert latest.backend_rss_mb == pytest.approx(point["backend_rss_mb"])


def test_collector_run_retention_downsamples_and_purges(db_url, tmp_path):
    repo = MonitoringRepo()
    collector = MonitoringCollector(data_dir=tmp_path, repo=repo)

    now = datetime.now(timezone.utc)
    # 2 days back (not 1) — safely past the 24h raw-retention cutoff no
    # matter what minute `now` itself falls on; 1 day back plus a +40min
    # offset could land LESS than 24h before `now` depending on now's own
    # minutes, which is exactly what happened here originally (the +40min
    # point silently stayed 'raw' instead of being included in the plan).
    old_hour = (now - timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
    for minute, rss in [(0, 100.0), (20, 200.0), (40, 300.0)]:
        repo.insert_raw(
            ts=old_hour + timedelta(minutes=minute),
            backend_rss_mb=rss, db_total_mb=10.0, data_volume_mb=1.0, disk_free_mb=500.0, active_jobs=0,
        )
    # A recent raw point, well inside the 24h raw-retention window — must
    # survive the retention pass untouched.
    repo.insert_raw(
        ts=now, backend_rss_mb=999.0, db_total_mb=10.0, data_volume_mb=1.0, disk_free_mb=500.0, active_jobs=0,
    )
    # A point far older than the 90-day hourly retention — must be purged,
    # not just downsampled.
    ancient = now - timedelta(days=200)
    repo.insert_raw(
        ts=ancient, backend_rss_mb=1.0, db_total_mb=1.0, data_volume_mb=1.0, disk_free_mb=1.0, active_jobs=0,
    )

    collector.run_retention(now=now)

    raw_rows = repo.list_range(resolution="raw", ts_from=now - timedelta(hours=1), ts_to=now + timedelta(hours=1))
    assert [r.backend_rss_mb for r in raw_rows] == pytest.approx([999.0])

    hourly_rows = repo.list_range(
        resolution="hourly", ts_from=old_hour - timedelta(hours=1), ts_to=old_hour + timedelta(hours=1)
    )
    assert len(hourly_rows) == 1
    assert hourly_rows[0].backend_rss_mb == pytest.approx(200.0)
    assert hourly_rows[0].backend_rss_mb_min == pytest.approx(100.0)
    assert hourly_rows[0].backend_rss_mb_max == pytest.approx(300.0)

    # The 200-day-old point is gone entirely (purged, past HOURLY_RETENTION).
    all_raw_left = repo.list_range(resolution="raw", ts_from=ancient - timedelta(days=1), ts_to=now + timedelta(days=1))
    assert all(r.ts != ancient for r in all_raw_left)


def test_monitoring_repo_database_total_mb_and_top_tables(db_url):
    repo = MonitoringRepo()
    assert repo.database_total_mb() > 0

    tables = repo.top_tables(limit=10)
    assert len(tables) <= 10
    assert all(t["size_bytes"] >= 0 for t in tables)
    assert any(t["table_name"].endswith(".users") for t in tables)


def test_job_repo_update_peak_memory_takes_the_max(db_url):
    job = JobRepo().create(type="test")
    JobRepo().update_peak_memory(job.id, 100.0)
    JobRepo().update_peak_memory(job.id, 50.0)  # lower — must not overwrite the peak
    JobRepo().update_peak_memory(job.id, 300.0)

    reloaded = JobRepo().get_by_id(job.id)
    assert reloaded.peak_memory_mb == pytest.approx(300.0)
