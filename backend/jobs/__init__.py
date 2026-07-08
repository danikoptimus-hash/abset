"""Менеджер фоновых задач (FRONTEND.md §4): design/analyze/validate — без
Celery, ThreadPoolExecutor + таблица jobs (Postgres) как источник правды."""

from __future__ import annotations

from backend.jobs.runner import JobRunner, ProgressReporter, RequiresConfirmation

__all__ = ["JobRunner", "ProgressReporter", "RequiresConfirmation"]
