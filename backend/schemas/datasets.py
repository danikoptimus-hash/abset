from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class DatasetOut(BaseModel):
    id: str
    experiment_id: str | None
    experiment_name: str | None
    kind: str
    filename: str
    n_rows: int
    columns: list[str]
    dtypes: dict[str, str] | None = None
    uploaded_by_email: str | None
    uploaded_at: datetime


class PaginatedDatasets(BaseModel):
    items: list[DatasetOut]
    total: int
    page: int
    page_size: int


class DatasetPreview(BaseModel):
    filename: str
    n_rows: int
    columns: list[str]
    rows: list[dict[str, Any]]


class MetricBaselineRequest(BaseModel):
    """Форма метрики (как MetricConfig) для расчета baseline-среднего —
    нужен визарду дизайна (FRONTEND.md §5.2, шаг 3: live-пересчет абсолютного
    MDE в относительный)."""

    name: str
    type: str
    pre_col: str | None = None
    num: str | None = None
    den: str | None = None


class MetricBaselineResponse(BaseModel):
    baseline_mean: float | None


class DemoDesignDatasetResponse(BaseModel):
    dataset_id: str
    suggested_config: dict[str, Any]
