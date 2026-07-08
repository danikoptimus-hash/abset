from __future__ import annotations

from pydantic import BaseModel

from abkit.config import DesignConfig


class DesignRequest(BaseModel):
    config: DesignConfig
    dataset_id: str
    confirmed: bool = False


class JobAccepted(BaseModel):
    job_id: str
