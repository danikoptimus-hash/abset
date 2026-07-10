"""Tags for A/B tests (Superset-style dashboard tags, CLAUDE.md) — typeahead
search/create here; assignment to a specific experiment is
PUT /experiments/{name}/tags (backend/routers/experiments.py, same router as
the rest of that resource)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from abkit.auth.guards import CurrentUser
from backend.deps import get_current_user
from backend.schemas.tags import (
    CreateTagRequest,
    DeleteTagResponse,
    TagOut,
    TagsResponse,
    TagUsageResponse,
)

router = APIRouter(prefix="/tags", tags=["tags"])


def _to_tag_out(t) -> TagOut:
    return TagOut(id=str(t.id), name=t.name, color=t.color)


@router.get("", response_model=TagsResponse)
def search_tags(
    q: str | None = Query(default=None, description="Typeahead substring match"),
    user: CurrentUser = Depends(get_current_user),
) -> TagsResponse:
    from abkit.jobs import search_tags as _search_tags

    return TagsResponse(items=[_to_tag_out(t) for t in _search_tags(user, q)])


@router.post("", response_model=TagOut, status_code=201)
def create_tag(body: CreateTagRequest, user: CurrentUser = Depends(get_current_user)) -> TagOut:
    """Get-or-create (abkit/jobs.py::run_create_tag) — typing an existing
    name (case-insensitively) reuses it instead of erroring."""
    from abkit.jobs import run_create_tag

    return _to_tag_out(run_create_tag(user, body.name))


@router.get("/{tag_id}/usage", response_model=TagUsageResponse)
def get_tag_usage(tag_id: str, user: CurrentUser = Depends(get_current_user)) -> TagUsageResponse:
    """The frontend calls this before showing the delete-tag confirmation,
    so the affected-experiment count is visible up front."""
    from abkit.jobs import get_tag_usage_count

    return TagUsageResponse(count=get_tag_usage_count(user, tag_id))


@router.delete("/{tag_id}", response_model=DeleteTagResponse)
def delete_tag(tag_id: str, user: CurrentUser = Depends(get_current_user)) -> DeleteTagResponse:
    """Admin-only (enforced in abkit/jobs.py::run_delete_tag) — detaches from
    every experiment via ON DELETE CASCADE, not a separate step."""
    from abkit.jobs import run_delete_tag

    affected = run_delete_tag(user, tag_id)
    return DeleteTagResponse(affected_experiments=affected)
