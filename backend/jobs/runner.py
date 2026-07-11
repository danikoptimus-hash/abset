"""ThreadPoolExecutor-based исполнитель фоновых задач (FRONTEND.md §4).

Без Celery: ABKIT_JOB_WORKERS (default 2) потоков + таблица jobs в Postgres —
интерфейс (submit/ProgressReporter) изолирован от routers так, чтобы будущая
замена на Celery не потребовала их менять (только реализацию JobRunner)."""

from __future__ import annotations

import os
import threading
import uuid as uuid_mod
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from abkit.db.models import Job
from abkit.db.repositories import JobRepo
from abkit.logging_config import get_logger

log = get_logger("backend.jobs")

_DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 60


def _human_readable_message(exc: BaseException, error_id: str) -> str:
    """job.error долетает до UI как есть (GET /jobs/{id}) — доменные исключения
    (AnalysisError/DesignError/PipelineError/StorageError) уже несут
    сообщение, написанное для пользователя, пробрасываем его. Все прочее
    (сырые pandas/Python-исключения вроде ValueError на merge несовместимых
    dtype, а также SystemExit и прочие BaseException) технического вида и
    никогда не должно долетать до UI — полная трассировка уже ушла в лог
    (см. вызов ниже), сюда — только общая фраза. error_id (короткий uuid,
    тот же, что уже пишется в лог рядом с traceback'ом) — по той же причине,
    что и в backend/errors.py::_handle_unexpected_error: голое "Internal
    processing error" без него бесполезно для диагностики, а до этого
    момента у job-уровня (в отличие от HTTP-уровня) его вообще не было."""
    from abkit import checks, storage
    from abkit.db_connections.sql_dataset import SqlExecutionError
    from abkit.db_connections.sql_guard import SqlValidationError
    from abkit.experiment import DesignError
    from abkit.pipeline import PipelineError

    if isinstance(
        exc,
        (
            checks.AnalysisError, DesignError, PipelineError, storage.StorageError,
            SqlValidationError, SqlExecutionError,
        ),
    ):
        return str(exc)
    return f"Internal processing error (ref: {error_id})"


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
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

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
        except BaseException as e:
            # BaseException, не Exception: a job must never disappear or
            # hang in 'running' because of what it raised — SystemExit/
            # MemoryError/anything else still needs status=failed+error.
            # A hard OOM-kill of the whole process is the one thing this
            # can't catch (see _sweep_stale_jobs for that case).
            error_id = uuid_mod.uuid4().hex[:8]
            log.error("job.failed", job_id=str(job_id), error_id=error_id, exc_info=True)
            self._repo.mark_failed(job_id, _human_readable_message(e, error_id))
        else:
            self._repo.mark_completed(job_id, result)

    def mark_unfinished_jobs_failed_on_startup(self) -> None:
        """FRONTEND.md §4: "Незавершенные при старте бэкенда помечаются failed
        с понятной ошибкой" — их future-объекты потеряны вместе с прошлым
        процессом, дожидаться нечего."""
        for job in self._repo.list_unfinished():
            self._repo.mark_failed(
                job.id, "The backend restarted while this job was running — please run it again"
            )

    def start_heartbeat_sweeper(self, interval_seconds: int = _DEFAULT_HEARTBEAT_INTERVAL_SECONDS) -> None:
        """Периодически (раз в interval_seconds) помечает failed job'ы,
        застрявшие в 'running' без обновления прогресса дольше
        ABKIT_JOB_TIMEOUT_MINUTES (env, default 30) — покрывает случай, когда
        воркер умер БЕЗ исключения (например, процесс убит OOM-killer'ом) и
        никогда не дойдет до mark_failed сам, а mark_unfinished_jobs_failed_on_startup
        срабатывает только на СЛЕДУЮЩЕМ старте процесса, не в течение его жизни."""
        self._heartbeat_stop.clear()
        thread = threading.Thread(target=self._heartbeat_loop, args=(interval_seconds,), daemon=True)
        thread.name = "abkit-job-heartbeat"
        thread.start()
        self._heartbeat_thread = thread

    def _heartbeat_loop(self, interval_seconds: int) -> None:
        while not self._heartbeat_stop.wait(interval_seconds):
            try:
                self._sweep_stale_jobs()
            except Exception:
                log.error("job.heartbeat_sweep_failed", exc_info=True)

    def _sweep_stale_jobs(self) -> None:
        timeout_minutes = int(os.environ.get("ABKIT_JOB_TIMEOUT_MINUTES", "30"))
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
        for job in self._repo.list_stale_running(cutoff):
            log.error("job.timed_out", job_id=str(job.id), timeout_minutes=timeout_minutes)
            self._repo.mark_failed(job.id, "Job timed out or worker died")

    def shutdown(self, wait: bool = False) -> None:
        self._heartbeat_stop.set()
        self._executor.shutdown(wait=wait)
