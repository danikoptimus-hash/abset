from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class DatasetExperimentUse(BaseModel):
    """One (experiment, kind) row from experiment_datasets — item 1 bug fix:
    the Datasets list column used to read only datasets.experiment_id (the
    single legacy PRIMARY/first-use field), which stays null for a dataset
    uploaded standalone and only later picked for analyze/validate on an
    experiment it wasn't created under — this is the many-to-many source of
    truth, one entry per actual use."""

    experiment_id: str
    experiment_name: str
    kind: str


class DatasetOut(BaseModel):
    id: str
    experiment_id: str | None
    experiment_name: str | None
    kind: str
    filename: str
    n_rows: int
    columns: list[str]
    dtypes: dict[str, str] | None = None
    uploaded_by: str | None = None
    uploaded_by_email: str | None
    uploaded_at: datetime
    source: str = "upload"
    connection_id: str | None = None
    connection_name: str | None = None
    sql_text: str | None = None
    fetched_at: datetime | None = None
    # Datasets follow-up (persist source schema/table): explicit schema/
    # table picked via the From SQL cascade, when sql_text still matches
    # what it generates — None for hand-written queries / once sql_text has
    # diverged. See abkit/db/models.py::Dataset for the full rationale.
    source_schema: str | None = None
    source_table: str | None = None
    # Item 1 bug fix: every experiment that has actually used this dataset
    # (design/analyze/validate), from experiment_datasets — the Datasets
    # list column renders all of these, not just the legacy single
    # experiment_id/experiment_name pair above (kept for other consumers,
    # e.g. the design-dataset lookup, per CLAUDE.md's backward-compat note).
    experiments: list[DatasetExperimentUse] = []


class DatasetFromSqlRequest(BaseModel):
    connection_id: str
    sql: str
    name: str
    # DB3 (dataset-centric model): no longer required at creation — see
    # upload_dataset's kind param in backend/routers/datasets.py.
    kind: str = "pre_design"
    experiment_id: str | None = None
    # Only sent when `sql` is still exactly what selecting this schema/table
    # in the cascade would generate (Datasets follow-up) — the frontend
    # omits both otherwise, e.g. for hand-written SQL.
    source_schema: str | None = None
    source_table: str | None = None


class DatasetFromSqlResult(BaseModel):
    dataset_id: str
    n_rows: int
    truncated: bool


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


class ColumnValueCount(BaseModel):
    value: str
    count: int


class ColumnValuesResponse(BaseModel):
    """Item 12 (external split) — Group assignment mapping step: after the
    user picks the group column, the UI shows its distinct values (most
    frequent first, up to `limit`) so each one can be mapped to a declared
    group or "exclude"."""

    column: str
    values: list[ColumnValueCount]
    truncated: bool


class DuplicateCheckResponse(BaseModel):
    """Analyze tab, before "Run analysis" — whether the chosen post-period
    dataset has duplicate values in the experiment's unit_col (day-by-day/
    multi-row-per-user data). If so, the frontend makes Date column required
    and disables Run analysis until one is picked — abkit/experiment.py's
    analyze() already refuses to run this combination server-side (dup +
    no date_col -> AnalysisError); this just surfaces that requirement
    BEFORE submission instead of after a failed job."""

    has_duplicates: bool
    n_duplicated_units: int


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


class DatasetUsageResponse(BaseModel):
    """GET /datasets/{id}/usage (UX package, Datasets §2.2) — which
    experiments use this dataset, drives which Delete confirmation the
    frontend shows: empty -> plain confirm, non-empty -> strict DELETE-typed
    modal listing them."""

    experiments: list[str]


class DeleteDatasetRequest(BaseModel):
    confirm: str | None = None


class PatchDatasetRequest(BaseModel):
    """PATCH /datasets/{id} (UX package, Datasets §2.3): name is always
    editable; connection_id/sql_text only apply to source=sql datasets and
    trigger a re-fetch (same mechanism as Refresh) when either changes.
    source_schema/source_table (Datasets follow-up) are only meaningful
    alongside a sql_text change — sent when the edited SQL still exactly
    matches a cascade schema/table pick, omitted (-> cleared) otherwise."""

    name: str | None = None
    connection_id: str | None = None
    sql_text: str | None = None
    source_schema: str | None = None
    source_table: str | None = None


class PatchDatasetResponse(BaseModel):
    dataset: DatasetOut
    # Set when connection_id/sql_text changed — a re-fetch job was submitted
    # (same mechanism as Refresh); None for a name-only edit (immediate).
    job_id: str | None = None


class BulkDeleteDatasetsRequest(BaseModel):
    """Bulk select + Delete on the Datasets list (mirrors experiments'
    /experiments/bulk-delete): one typed-DELETE confirmation for the whole
    batch — unlike the single-dataset flow's two-tier confirm (plain vs
    DELETE-typed depending on usage), since the frontend already lists
    used-by info per row before this is ever sent."""

    dataset_ids: list[str]
    confirm: str


class BulkDeleteDatasetsSkipped(BaseModel):
    dataset_id: str
    reason: str


class BulkDeleteDatasetsResult(BaseModel):
    deleted: list[str]
    skipped: list[BulkDeleteDatasetsSkipped]
