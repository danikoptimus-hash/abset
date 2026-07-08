from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class BlockIn(BaseModel):
    id: str | None = None
    kind: str = "custom"
    title: str = ""
    content_md: str = ""
    position: int = 0


class BlockOut(BaseModel):
    id: str
    kind: str
    title: str
    content_md: str
    position: int
    updated_at: datetime
