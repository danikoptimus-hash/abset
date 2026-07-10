from __future__ import annotations

from pydantic import BaseModel


class TagOut(BaseModel):
    id: str
    name: str
    # Nullable, currently unused by any code path — the UI always computes a
    # deterministic color from a hash of the name instead (see
    # abkit/db/models.py::Tag). Exists for a future manual color picker.
    color: str | None = None


class TagsResponse(BaseModel):
    items: list[TagOut]


class CreateTagRequest(BaseModel):
    name: str


class SetExperimentTagsRequest(BaseModel):
    tag_ids: list[str]


class TagUsageResponse(BaseModel):
    count: int


class DeleteTagResponse(BaseModel):
    affected_experiments: int
