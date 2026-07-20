from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.schemas.tags import TagOut

REPORT_FILENAMES = ("design_report.html", "report.html")


class ExperimentSummary(BaseModel):
    name: str
    status: str
    publication_status: str
    owner_id: str | None
    owner_email: str | None
    owner_first_name: str | None
    owner_last_name: str | None
    # Computed server-side (abkit.access.is_owner_or_granted) — lets the list
    # show/hide the hover Edit/Delete buttons without an extra request per
    # row (UX package, section 5).
    can_edit: bool
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    archived_at: datetime | None
    tags: list[TagOut] = []
    # Item 5 (folders package) — null means Uncategorized, not "unknown".
    folder_id: str | None = None
    folder_name: str | None = None


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
    # Стабильный идентификатор — для permalink'а (кнопка Share). Эксперименты
    # адресуются ИМЕНЕМ (CLAUDE.md, "Известный техдолг"), а имя мутабельно:
    # ссылка вида /experiments/<name> ломается при переименовании. id тут —
    # НЕ начало миграции адресации на uuid (она осознанно отложена), а ровно
    # то, что нужно, чтобы отдать наружу ссылку, переживающую ренейм.
    id: str
    name: str
    status: str
    publication_status: str
    owner_id: str | None
    owner_email: str | None
    owner_first_name: str | None
    owner_last_name: str | None
    can_edit: bool
    # Header "Last modified by X Y N ago" (UX package, п.4) — самое свежее
    # из audit_log (статус/публикация/переименование/properties/analyze) и
    # experiment_blocks.updated_at/updated_by (блоки НЕ аудируются отдельно).
    last_modified_at: datetime | None
    last_modified_by_first_name: str | None
    last_modified_by_last_name: str | None
    last_modified_by_email: str | None
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
    tags: list[TagOut] = []
    folder_id: str | None = None
    folder_name: str | None = None


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


class DeletionSummary(BaseModel):
    assignments: int
    datasets: int
    results: int


class BulkDeleteRequest(BaseModel):
    names: list[str]
    confirm: str


class BulkDeleteSkipped(BaseModel):
    name: str
    reason: str


class BulkDeleteResult(BaseModel):
    deleted: list[str]
    skipped: list[BulkDeleteSkipped]


class ExperimentRef(BaseModel):
    """GET /experiments/by-id/{id} — минимум, нужный фронту, чтобы увести
    пользователя на канонический (именной) URL теста."""

    id: str
    name: str


class ImportExperimentResult(BaseModel):
    """POST /experiments/import. `renamed` — имя в архиве было занято, тест
    создан под `experiment_name` (ExperimentSummary.name адресует тест, так
    что фронту нужно именно новое имя, а не то, что лежало в архиве).
    `warnings` — непустой список означает "импорт УДАЛСЯ, но с оговорками"
    (обычно: датасет не нашелся, переанализ недоступен), а не провал."""

    experiment_name: str
    original_name: str
    renamed: bool
    warnings: list[str] = []


class AnalyzeRequest(BaseModel):
    dataset_id: str
    correction: str = "holm"
    date_col: str | None = None
    # Item 12 (external split) — required when the experiment's
    # config.split_source == "external": there's no assignments join, the
    # group comes from this column in the uploaded data, mapped (raw value
    # -> declared group name, or "exclude") via group_mapping. Both ignored
    # for the normal split_source="abkit" flow.
    group_column: str | None = None
    group_mapping: dict[str, str] | None = None
    # Item 3 (consolidated package, multi-select analysis methods): metric
    # name -> ORDERED list of method ids (e.g. "cuped_welch",
    # "mann_whitney" — see abkit/experiment.py::steps_for_method_id and its
    # frontend mirror, frontend/src/pages/experiment/methodOptions.ts). The
    # FIRST id is the designed/primary method (drives the verdict); any
    # remaining ids run as comparison methods — this replaces the old
    # single-method `methods: dict[str, str]` override AND the separate
    # `compare_methods: bool` flag/fixed alternative set: the comparison set
    # is now exactly whatever else the user multi-selected, per metric. A
    # metric absent from this dict keeps the type/config-based default
    # (single designed method, no extras).
    methods: dict[str, list[str]] | None = None
    # External split rework (§3): columns to break the effect down by, from
    # the analysis dataset's ACTUAL columns (external AND ABSet). None → the
    # design-declared strata only (unchanged behavior). Columns not among the
    # declared strata are "ad-hoc" segments, marked as such in the results.
    segment_columns: list[str] | None = None


class AnalyzeDemoRequest(BaseModel):
    effect: float = 0.03


class ValidateRequest(BaseModel):
    dataset_id: str
    # ge=100: fewer sims make FPR/power estimates too noisy to interpret —
    # the UI enforces this too (Validation.tsx), this is defense-in-depth
    # against direct API calls (UX-package, Validation п.3.4).
    n_sims: int = Field(default=2000, ge=100)
    compare_methods: bool = False
    effect: float = 0.05


class UserBrief(BaseModel):
    """Lightweight user shape for pickers (Properties modal Owners/Editors
    multiselects) — not the full admin-only UserAdminOut."""

    id: str
    email: str
    first_name: str
    last_name: str
    role: str


class ExperimentPropertiesOut(BaseModel):
    name: str
    owner: UserBrief | None
    owners: list[UserBrief]
    editors: list[UserBrief]
    visible_roles: list[str] | None
    # Prefill for the Properties modal's Tags field — saving tags is a
    # separate call (PUT /experiments/{name}/tags), not part of this
    # endpoint's own PUT, but this GET is the modal's one "load everything"
    # fetch, so the current set travels here too.
    tags: list[TagOut] = []


class UpdateExperimentPropertiesRequest(BaseModel):
    name: str
    owner_ids: list[str] = []
    editor_ids: list[str] = []
    visible_roles: list[str] | None = None
