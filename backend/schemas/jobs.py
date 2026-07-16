from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class JobProgress(BaseModel):
    stage: str | None = None
    pct: int | None = None
    message: str | None = None


class JobOut(BaseModel):
    id: str
    type: str
    status: str
    progress: JobProgress | None
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    # Admin monitoring panel: peak whole-process RSS observed while this job
    # ran (backend/jobs/runner.py samples every 2s). NULL for jobs that
    # finished before this column existed, or that ran shorter than the
    # first sample.
    peak_memory_mb: float | None = None
