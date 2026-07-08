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
