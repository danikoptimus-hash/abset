from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

REPORT_FILENAMES = ("design_report.html", "report.html")


class ExperimentSummary(BaseModel):
    name: str
    status: str
    publication_status: str
    owner_email: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    archived_at: datetime | None


class PaginatedExperiments(BaseModel):
    items: list[ExperimentSummary]
    total: int
    page: int
    page_size: int


class FileInfo(BaseModel):
    path: str
    size_kb: float


class SampleInfo(BaseModel):
    filename: str
    n_rows: int
    size_kb: float


class ExperimentDetail(BaseModel):
    name: str
    status: str
    publication_status: str
    owner_email: str | None
    owner_name: str | None
    config: dict[str, Any]
    # Реальная колонка Experiment.design_summary — сегодня всегда None
    # (create_experiment ее не заполняет, см. abkit/db/store.py), но поле
    # честно прокидывается как есть на случай будущего заполнения (R3+),
    # а не скрывается и не подделывается.
    design_summary: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    archived_at: datetime | None
    available_reports: list[str]
    files: list[FileInfo]


class AuditEntryOut(BaseModel):
    id: int
    ts: datetime
    user_email: str | None
    action: str
    object_type: str | None
    object_id: str | None
    object_name: str | None
    details: dict[str, Any] | None


class PaginatedAudit(BaseModel):
    items: list[AuditEntryOut]
    total: int
    page: int
    page_size: int


class StatusChangeRequest(BaseModel):
    to: str


class PatchExperimentRequest(BaseModel):
    publication_status: str | None = None
    name: str | None = None


class DeleteExperimentRequest(BaseModel):
    confirm: str


class AnalyzeRequest(BaseModel):
    dataset_id: str
    correction: str = "holm"
    compare_methods: bool = False
    date_col: str | None = None


class AnalyzeDemoRequest(BaseModel):
    effect: float = 0.03


class ValidateRequest(BaseModel):
    dataset_id: str
    n_sims: int = 2000
    compare_methods: bool = False
    effect: float = 0.05
