"""GET /jobs/{id} (FRONTEND.md §3.2/§4) — статус фоновой задачи; фронт
поллит раз в 1с (TanStack Query refetchInterval). Доступно любому
залогиненному пользователю (как и остальное чтение в этом API — мутации
гейтятся правами, чтение открыто всем ролям)."""

from __future__ import annotations

import uuid as uuid_mod

from fastapi import APIRouter, Depends

from abkit.auth.guards import CurrentUser
from abkit.db.repositories import JobRepo
from backend.deps import get_current_user
from backend.errors import APIError
from backend.schemas.jobs import JobOut

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, user: CurrentUser = Depends(get_current_user)) -> JobOut:
    try:
        parsed_id = uuid_mod.UUID(job_id)
    except ValueError as e:
        raise APIError(422, "validation_error", "Некорректный идентификатор задачи") from e

    job = JobRepo().get_by_id(parsed_id)
    if job is None:
        raise APIError(404, "not_found", f"Задача '{job_id}' не найдена")
    return JobOut(
        id=str(job.id), type=job.type, status=job.status, progress=job.progress,
        result=job.result_ref, error=job.error, created_at=job.created_at, finished_at=job.finished_at,
    )
