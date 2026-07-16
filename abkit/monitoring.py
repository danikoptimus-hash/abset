"""Admin monitoring panel — resource-usage collector with persistent
history (Postgres, not an OS task manager, so it survives restarts).

Explicitly out of scope (see CLAUDE.md-style constraints from the feature
request): no Docker socket, no per-container stats, no new services
(Prometheus/Grafana) — this reads only the backend's own process (psutil),
its own Postgres connection, and its own data directory. A single daemon
thread, started alongside the job runner (backend/main.py), not a
separate service.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psutil

from abkit.db.repositories import MonitoringRepo
from abkit.db.store import get_data_dir
from abkit.logging_config import get_logger

log = get_logger("abkit.monitoring")

SNAPSHOT_INTERVAL_SECONDS = 60
# The data-dir walk (os.walk + stat, du-equivalent) is the one potentially
# slow metric — cached and only recomputed on this cadence, reused across
# snapshot ticks in between (feature request's explicit instruction).
DU_CACHE_SECONDS = 300
RAW_RETENTION = timedelta(hours=24)
HOURLY_RETENTION = timedelta(days=90)
# How often the retention pass (downsample + purge) runs — independent of
# the 60s snapshot cadence, since there's no need to re-check this often.
RETENTION_INTERVAL_SECONDS = 300

METRICS = ("backend_rss_mb", "db_total_mb", "data_volume_mb", "disk_free_mb")


def dir_size_mb(path: Path) -> float:
    """du-equivalent walk — os.walk + stat per file, no shelling out to
    `du` (portable, no subprocess). A file disappearing mid-walk (e.g. a
    dataset being deleted concurrently) is skipped, not fatal."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return total / (1024 * 1024)


@dataclass
class RetentionPlan:
    """Output of plan_retention() — what a caller should write (hourly_rows)
    and delete (raw_ids_to_delete) to apply the plan. Never touches the DB
    itself, so it's trivial to unit-test."""

    hourly_rows: list[dict[str, Any]] = field(default_factory=list)
    raw_ids_to_delete: list[int] = field(default_factory=list)


def plan_retention(raw_rows: list[Any], *, now: datetime) -> RetentionPlan:
    """Pure decision logic for the 24h-raw -> hourly-min/avg/max downsample
    step — no DB access, so tests pass in plain row-like objects (anything
    with .id/.ts/the METRICS attributes) and an explicit `now` instead of
    needing to mock the clock.

    Groups `raw_rows` into hour buckets (floor of .ts to the hour) and
    aggregates each metric to (avg in the plain key, min/max in the
    _min/_max keys) — except the bucket containing `now`'s own hour, which
    is skipped as still in progress (collapsing an incomplete hour would
    either need re-collapsing on the next tick, or permanently lose
    whatever raw points hadn't arrived yet)."""
    current_hour_start = now.replace(minute=0, second=0, microsecond=0)
    buckets: dict[datetime, list[Any]] = {}
    for row in raw_rows:
        bucket_start = row.ts.replace(minute=0, second=0, microsecond=0)
        if bucket_start >= current_hour_start:
            continue
        buckets.setdefault(bucket_start, []).append(row)

    hourly_rows: list[dict[str, Any]] = []
    raw_ids_to_delete: list[int] = []
    for bucket_start, rows in buckets.items():
        agg: dict[str, Any] = {"ts": bucket_start, "resolution": "hourly", "active_jobs": None}
        for metric in METRICS:
            values = [v for r in rows if (v := getattr(r, metric)) is not None]
            if values:
                agg[metric] = sum(values) / len(values)
                agg[f"{metric}_min"] = min(values)
                agg[f"{metric}_max"] = max(values)
            else:
                agg[metric] = None
                agg[f"{metric}_min"] = None
                agg[f"{metric}_max"] = None
        hourly_rows.append(agg)
        raw_ids_to_delete.extend(r.id for r in rows)

    return RetentionPlan(hourly_rows=hourly_rows, raw_ids_to_delete=raw_ids_to_delete)


class MonitoringCollector:
    """Daemon thread: every SNAPSHOT_INTERVAL_SECONDS, records one 'raw'
    monitoring_snapshots row (backend RSS, Postgres size, data-volume
    size, free disk space, active job count); every
    RETENTION_INTERVAL_SECONDS (same thread, no separate timer), downsamples
    raw rows older than RAW_RETENTION into hourly min/avg/max and purges
    anything older than HOURLY_RETENTION."""

    def __init__(self, data_dir: Path | None = None, repo: MonitoringRepo | None = None) -> None:
        self._data_dir = data_dir or get_data_dir()
        self._repo = repo or MonitoringRepo()
        self._process = psutil.Process()
        self._du_cache_value_mb: float | None = None
        self._du_cache_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_retention_at = 0.0

    def _cached_data_volume_mb(self) -> float | None:
        now = time.monotonic()
        if self._du_cache_value_mb is None or now - self._du_cache_at >= DU_CACHE_SECONDS:
            try:
                self._du_cache_value_mb = dir_size_mb(self._data_dir)
            except OSError:
                log.error("monitoring.data_volume_walk_failed", exc_info=True)
            self._du_cache_at = now
        return self._du_cache_value_mb

    def snapshot_now(self) -> dict[str, Any]:
        """Collects one point and inserts it (resolution='raw'). Also the
        entry point for the admin-only 'force a snapshot' endpoint and this
        feature's own tests — not just the timer loop. Each metric is
        collected independently (one failing — e.g. a transient DB hiccup —
        doesn't blank out the others)."""
        backend_rss_mb: float | None = None
        try:
            backend_rss_mb = self._process.memory_info().rss / (1024 * 1024)
        except Exception:
            log.error("monitoring.rss_read_failed", exc_info=True)

        db_total_mb: float | None = None
        try:
            db_total_mb = self._repo.database_total_mb()
        except Exception:
            log.error("monitoring.db_size_query_failed", exc_info=True)

        data_volume_mb = self._cached_data_volume_mb()

        disk_free_mb: float | None = None
        try:
            disk_free_mb = shutil.disk_usage(self._data_dir).free / (1024 * 1024)
        except OSError:
            log.error("monitoring.disk_usage_failed", exc_info=True)

        active_jobs: int | None = None
        try:
            active_jobs = self._repo.active_job_count()
        except Exception:
            log.error("monitoring.active_jobs_query_failed", exc_info=True)

        ts = datetime.now(timezone.utc)
        self._repo.insert_raw(
            ts=ts,
            backend_rss_mb=backend_rss_mb,
            db_total_mb=db_total_mb,
            data_volume_mb=data_volume_mb,
            disk_free_mb=disk_free_mb,
            active_jobs=active_jobs,
        )
        return {
            "ts": ts,
            "backend_rss_mb": backend_rss_mb,
            "db_total_mb": db_total_mb,
            "data_volume_mb": data_volume_mb,
            "disk_free_mb": disk_free_mb,
            "active_jobs": active_jobs,
        }

    def run_retention(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        raw_cutoff = now - RAW_RETENTION
        raw_rows = self._repo.raw_older_than(raw_cutoff)
        plan = plan_retention(raw_rows, now=now)
        if plan.hourly_rows:
            self._repo.insert_hourly(plan.hourly_rows)
        if plan.raw_ids_to_delete:
            self._repo.delete_by_id(plan.raw_ids_to_delete)
        purged = self._repo.purge_older_than(now - HOURLY_RETENTION)
        if purged:
            log.info("monitoring.retention_purged", count=purged)

    def _loop(self) -> None:
        while not self._stop.wait(SNAPSHOT_INTERVAL_SECONDS):
            try:
                self.snapshot_now()
            except Exception:
                log.error("monitoring.snapshot_failed", exc_info=True)
            if time.monotonic() - self._last_retention_at >= RETENTION_INTERVAL_SECONDS:
                self._last_retention_at = time.monotonic()
                try:
                    self.run_retention()
                except Exception:
                    log.error("monitoring.retention_failed", exc_info=True)

    def start(self) -> None:
        # No synchronous snapshot here on purpose: this runs on every
        # backend startup, including every backend/tests/conftest.py
        # app_client fixture instantiation (TestClient's lifespan) — an
        # eager DB write + data-dir walk on literally every test would add
        # real overhead across a large suite for no benefit, since a fresh
        # process is content to wait for the first regular tick. The admin-
        # only POST /admin/monitoring/snapshot-now (also this feature's own
        # e2e test) covers "I want a data point right now".
        self._last_retention_at = time.monotonic()
        self._stop.clear()
        thread = threading.Thread(target=self._loop, daemon=True)
        thread.name = "abkit-monitoring"
        thread.start()
        self._thread = thread

    def shutdown(self) -> None:
        self._stop.set()
