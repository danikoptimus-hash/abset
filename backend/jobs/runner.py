"""ThreadPoolExecutor-based исполнитель фоновых задач (FRONTEND.md §4).

Без Celery: ABKIT_JOB_WORKERS (default 2) потоков + таблица jobs в Postgres —
интерфейс (submit/ProgressReporter) изолирован от routers так, чтобы будущая
замена на Celery не потребовала их менять (только реализацию JobRunner)."""

from __future__ import annotations

import os
import uuid as uuid_mod
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from abkit.db.models import Job
from abkit.db.repositories import JobRepo
from abkit.logging_config import get_logger

log = get_logger("backend.jobs")


class RequiresConfirmation(Exception):
    """Job-функция бросает это, если нужно подтверждение пользователя (напр.
    isolation=warn с непустым пересечением, FRONTEND.md §3.2) — JobRunner
    переводит job в статус requires_confirmation с payload вместо failed."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__("requires_confirmation")


class ProgressReporter:
    """Передается в job-функцию; job вызывает .stage()/.counts() на каждом
    этапе — транслируется в progress {stage, pct, message} в таблице jobs."""

    def __init__(self, job_id: uuid_mod.UUID, repo: JobRepo) -> None:
        self._job_id = job_id
        self._repo = repo

    def stage(self, message: str, pct: int | None = None) -> None:
        """Совместимо по сигнатуре с progress_callback(label) в
        Experiment.design()/analyze() — можно передавать напрямую."""
        self._repo.update_progress(self._job_id, {"stage": message, "pct": pct, "message": message})

    def counts(self, completed: int, total: int, message: str = "") -> None:
        """Совместимо с progress_callback(completed, total) в run_aa/run_ab
        (abkit/validation/simulation.py)."""
        pct = int(completed / total * 100) if total else 0
        label = message or f"{completed}/{total}"
        self._repo.update_progress(self._job_id, {"stage": label, "pct": pct, "message": label})


class JobRunner:
    def __init__(self, max_workers: int | None = None) -> None:
        workers = max_workers or int(os.environ.get("ABKIT_JOB_WORKERS", "2"))
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="abkit-job")
        self._repo = JobRepo()

    def submit(
        self,
        job_type: str,
        created_by: uuid_mod.UUID | None,
        fn: Callable[[ProgressReporter], dict[str, Any]],
    ) -> Job:
        job = self._repo.create(type=job_type, created_by=created_by)
        self._executor.submit(self._run, job.id, fn)
        return job

    def _run(self, job_id: uuid_mod.UUID, fn: Callable[[ProgressReporter], dict[str, Any]]) -> None:
        self._repo.mark_running(job_id)
        reporter = ProgressReporter(job_id, self._repo)
        try:
            result = fn(reporter)
        except RequiresConfirmation as e:
            self._repo.mark_requires_confirmation(job_id, e.payload)
        except Exception as e:
            log.error("job.failed", job_id=str(job_id), exc_info=True)
            self._repo.mark_failed(job_id, str(e))
        else:
            self._repo.mark_completed(job_id, result)

    def mark_unfinished_jobs_failed_on_startup(self) -> None:
        """FRONTEND.md §4: "Незавершенные при старте бэкенда помечаются failed
        с понятной ошибкой" — их future-объекты потеряны вместе с прошлым
        процессом, дожидаться нечего."""
        for job in self._repo.list_unfinished():
            self._repo.mark_failed(
                job.id, "Backend был перезапущен во время выполнения задачи — запустите ее заново"
            )

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait)
