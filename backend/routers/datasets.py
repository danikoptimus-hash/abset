"""FRONTEND.md §3.2/§5.2: список, предпросмотр и загрузка датасетов
(Dataset.storage_path — CSV для source='upload'/'demo', parquet для
source='sql', см. abkit/dataset_files.py::read_dataset_file — как и все
текущие загрузки в app.py через st.file_uploader + pd.read_csv, теперь
dispatch по расширению файла).

Загрузка (POST) стримится на диск с лимитом ABKIT_MAX_UPLOAD_MB (.env.example).
experiment_name (не "experiment_id" буквально из FRONTEND.md §3.2) — весь
остальной API адресует эксперимент по имени (GET /experiments/{name} и т.д.,
решение R2), несогласованно было бы тут вдруг требовать UUID; опционален —
kind='pre_design' обычно загружается ДО того, как эксперимент существует
(визард шаг 1), тогда датасет создается с experiment_id=None и привязывается
позже design-джобой (DatasetRepo.attach_to_experiment, см. routers/design.py)."""

from __future__ import annotations

import os
import uuid as uuid_mod
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from abkit.auth.guards import CurrentUser
from abkit.dataset_files import read_dataset_file
from abkit.db.repositories import DatabaseConnectionRepo, DatasetRepo, ExperimentRepo, UserRepo
from abkit.db.store import DbExperimentStore
from backend.deps import get_current_user, get_job_runner, require_min_role
from backend.errors import APIError
from backend.jobs.runner import JobRunner
from backend.schemas.datasets import (
    BulkDeleteDatasetsRequest,
    BulkDeleteDatasetsResult,
    BulkDeleteDatasetsSkipped,
    DatasetFromSqlRequest,
    DatasetOut,
    DatasetPreview,
    DatasetUsageResponse,
    DeleteDatasetRequest,
    DemoDesignDatasetResponse,
    MetricBaselineRequest,
    MetricBaselineResponse,
    PaginatedDatasets,
    PatchDatasetRequest,
    PatchDatasetResponse,
)
from backend.schemas.design import JobAccepted

router = APIRouter(prefix="/datasets", tags=["datasets"])

_VALID_KINDS = ("pre_design", "post_analysis", "validation")


def _to_dataset_out(d, exp_name_by_id: dict, email_by_id: dict, connection_name_by_id: dict) -> DatasetOut:
    return DatasetOut(
        id=str(d.id), experiment_id=str(d.experiment_id) if d.experiment_id else None,
        experiment_name=exp_name_by_id.get(d.experiment_id),
        kind=d.kind, filename=d.filename, n_rows=d.n_rows, columns=d.columns,
        uploaded_by=str(d.uploaded_by) if d.uploaded_by else None,
        uploaded_by_email=email_by_id.get(d.uploaded_by) if d.uploaded_by else None,
        uploaded_at=d.uploaded_at, source=d.source,
        connection_id=str(d.connection_id) if d.connection_id else None,
        connection_name=connection_name_by_id.get(d.connection_id) if d.connection_id else None,
        sql_text=d.sql_text, fetched_at=d.fetched_at,
        source_schema=d.source_schema, source_table=d.source_table,
    )


@router.get("", response_model=PaginatedDatasets)
def list_datasets(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    q: str | None = Query(default=None, description="Live search over filename (UX package, Datasets §3)"),
    source: str | None = Query(default=None, description="Filter by source: upload|sql|demo"),
    user: CurrentUser = Depends(get_current_user),
) -> PaginatedDatasets:
    all_datasets = DatasetRepo().list_all()
    if q:
        q_lower = q.lower()
        all_datasets = [d for d in all_datasets if q_lower in d.filename.lower()]
    if source:
        all_datasets = [d for d in all_datasets if d.source == source]
    total = len(all_datasets)
    start = (page - 1) * page_size
    page_items = all_datasets[start : start + page_size]

    exp_name_by_id = {e.id: e.name for e in ExperimentRepo().list_all()}
    email_by_id = {u.id: u.email for u in UserRepo().list_all()}
    connection_name_by_id = {c.id: c.display_name for c in DatabaseConnectionRepo().list_all()}

    items = [_to_dataset_out(d, exp_name_by_id, email_by_id, connection_name_by_id) for d in page_items]
    return PaginatedDatasets(items=items, total=total, page=page, page_size=page_size)


def _max_upload_bytes() -> int:
    return int(os.environ.get("ABKIT_MAX_UPLOAD_MB", "400")) * 1024 * 1024


def _stream_upload_to_disk(upload: UploadFile, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = _max_upload_bytes()
    total = 0
    with open(dest, "wb") as out:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                out.close()
                dest.unlink(missing_ok=True)
                max_mb = max_bytes // (1024 * 1024)
                raise APIError(413, "payload_too_large", f"File exceeds the {max_mb} MB limit")
            out.write(chunk)


@router.post("", response_model=DatasetOut, status_code=201)
def upload_dataset(
    # DB3 (dataset-centric model, CLAUDE.md): kind is no longer required at
    # creation time — it's recorded per-use in experiment_datasets when the
    # dataset is actually selected for design/analyze/validate. This column
    # is kept only as a legacy/first-use label (default 'pre_design', the
    # most common starting point — most standalone uploads are candidate
    # data for a design).
    kind: str = Form(default="pre_design"),
    experiment_name: str | None = Form(default=None),
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_min_role("editor")),
) -> DatasetOut:
    if kind not in _VALID_KINDS:
        raise APIError(422, "validation_error", f"kind must be one of {_VALID_KINDS}")

    experiment_id = None
    if experiment_name:
        exp = ExperimentRepo().get_by_name(experiment_name)
        if exp is None:
            raise APIError(404, "not_found", f"Experiment '{experiment_name}' not found")
        experiment_id = exp.id

    store = DbExperimentStore()
    dest_dir = (store.data_dir / experiment_name / "uploads") if experiment_name else (
        store.data_dir / "_uploads"
    )
    dest_path = dest_dir / f"{uuid_mod.uuid4().hex}_{file.filename}"
    _stream_upload_to_disk(file, dest_path)

    try:
        data = read_dataset_file(str(dest_path))
    except Exception as e:
        dest_path.unlink(missing_ok=True)
        raise APIError(422, "validation_error", f"Failed to read file: {e}") from e

    dataset_id = DatasetRepo().create(
        kind=kind, filename=file.filename, n_rows=len(data), columns=list(data.columns),
        storage_path=str(dest_path), sha256=DatasetRepo.compute_sha256(data),
        experiment_id=experiment_id, uploaded_by=uuid_mod.UUID(user.id), source="upload",
    )
    ds = DatasetRepo().get_by_id(dataset_id)
    exp_name_by_id = {experiment_id: experiment_name} if experiment_id else {}
    email_by_id = {uuid_mod.UUID(user.id): user.email}
    out = _to_dataset_out(ds, exp_name_by_id, email_by_id, {})
    out.dtypes = {col: str(dtype) for col, dtype in data.dtypes.items()}
    return out


@router.get("/{dataset_id}/preview", response_model=DatasetPreview)
def preview_dataset(
    dataset_id: str,
    rows: int = Query(default=20, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
) -> DatasetPreview:
    import uuid as uuid_mod

    import pandas as pd

    try:
        parsed_id = uuid_mod.UUID(dataset_id)
    except ValueError as e:
        raise APIError(422, "validation_error", "Invalid dataset id") from e

    ds = DatasetRepo().get_by_id(parsed_id)
    if ds is None:
        raise APIError(404, "not_found", f"Dataset '{dataset_id}' not found")
    try:
        preview_df = read_dataset_file(ds.storage_path, nrows=rows)
    except OSError as e:
        raise APIError(404, "not_found", "Dataset file is not available on disk") from e

    # NaN не валиден в JSON (json.dumps с allow_nan=True пишет литерал NaN,
    # который не парсится стандартными JS/JSON-клиентами) — заменяем на None.
    preview_df = preview_df.where(pd.notnull(preview_df), None)
    return DatasetPreview(
        filename=ds.filename, n_rows=ds.n_rows, columns=ds.columns,
        rows=preview_df.to_dict(orient="records"),
    )


def _next_demo_name() -> str:
    """Как _next_demo_name в app.py, но по ExperimentRepo (db-режим) вместо
    файлового реестра — "demo", "demo_2", "demo_3", ..."""
    existing = {e.name for e in ExperimentRepo().list_all()}
    name = "demo"
    suffix = 1
    while name in existing:
        suffix += 1
        name = f"demo_{suffix}"
    return name


@router.post("/demo-design", response_model=DemoDesignDatasetResponse, status_code=201)
def create_demo_design_dataset(
    user: CurrentUser = Depends(require_min_role("editor")),
) -> DemoDesignDatasetResponse:
    """Визард дизайна, шаг "Данные" -> кнопка "Демо-данные" (FRONTEND.md
    §5.2) — то же самое, что app.py::render_design_tab делает при клике
    "Загрузить демо-данные": generate_demo_design_data + make_demo_design_config,
    только тут данные сразу сохраняются как pre_design датасет (визарду нужен
    dataset_id, не сырой DataFrame в сессии — состояние визарда живет на
    фронте, не в серверной сессии)."""
    from abkit.demo_data import generate_demo_design_data, make_demo_design_config

    n_demo = 5000
    data = generate_demo_design_data(n_demo, seed=0)
    suggested_name = _next_demo_name()
    demo_config = make_demo_design_config(suggested_name, n_demo, seed=0)

    store = DbExperimentStore()
    dest_path = store.data_dir / "_uploads" / f"{uuid_mod.uuid4().hex}_demo_design.csv"
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(dest_path, index=False)

    dataset_id = DatasetRepo().create(
        kind="pre_design", filename="demo_design.csv", n_rows=len(data), columns=list(data.columns),
        storage_path=str(dest_path), sha256=DatasetRepo.compute_sha256(data),
        uploaded_by=uuid_mod.UUID(user.id), source="demo",
    )
    return DemoDesignDatasetResponse(
        dataset_id=str(dataset_id), suggested_config=demo_config.model_dump(mode="json")
    )


@router.post("/{dataset_id}/metric-baseline", response_model=MetricBaselineResponse)
def get_metric_baseline(
    dataset_id: str, body: MetricBaselineRequest, user: CurrentUser = Depends(get_current_user),
) -> MetricBaselineResponse:
    """Визард дизайна, шаг "Параметры", режим "абсолютный MDE" — live-пересчет
    в относительный MDE через baseline (среднее) метрики (FRONTEND.md §5.2).
    Читает ПОЛНЫЙ файл датасета (не urlPreview, который ограничен 500
    строками) — baseline должен быть точным, не оценкой по сэмплу."""
    from abkit.config import MetricConfig
    from abkit.experiment import compute_metric_baseline_mean

    try:
        parsed_id = uuid_mod.UUID(dataset_id)
    except ValueError as e:
        raise APIError(422, "validation_error", "Invalid dataset id") from e
    ds = DatasetRepo().get_by_id(parsed_id)
    if ds is None:
        raise APIError(404, "not_found", f"Dataset '{dataset_id}' not found")

    data = read_dataset_file(ds.storage_path)
    metric = MetricConfig(name=body.name, type=body.type, pre_col=body.pre_col, num=body.num, den=body.den)
    baseline_mean = compute_metric_baseline_mean(metric, data)
    return MetricBaselineResponse(baseline_mean=baseline_mean)


@router.post("/from-sql", response_model=JobAccepted, status_code=202)
def create_dataset_from_sql(
    body: DatasetFromSqlRequest,
    user: CurrentUser = Depends(require_min_role("editor")),
    runner: JobRunner = Depends(get_job_runner),
) -> JobAccepted:
    """DB2 (CLAUDE.md dataset-from-SQL feature): materializes a SELECT query
    against a saved connection to parquet, streamed chunk-by-chunk (not
    materializing the full result in memory) — same async job mechanism as
    design/analyze/validate, with progress ("Fetched N rows...")."""
    if body.kind not in _VALID_KINDS:
        raise APIError(422, "validation_error", f"kind must be one of {_VALID_KINDS}")

    def _run(reporter) -> dict:
        from abkit.jobs import run_create_dataset_from_sql

        return run_create_dataset_from_sql(
            user, connection_id=body.connection_id, sql=body.sql, name=body.name,
            kind=body.kind, experiment_id=body.experiment_id, progress_callback=reporter.stage,
            source_schema=body.source_schema, source_table=body.source_table,
        )

    job = runner.submit("dataset_from_sql", uuid_mod.UUID(user.id), _run)
    return JobAccepted(job_id=str(job.id))


@router.post("/{dataset_id}/refresh", response_model=JobAccepted, status_code=202)
def refresh_sql_dataset(
    dataset_id: str,
    user: CurrentUser = Depends(require_min_role("editor")),
    runner: JobRunner = Depends(get_job_runner),
) -> JobAccepted:
    """DB2: re-runs a source='sql' dataset's stored sql_text, overwriting
    its parquet file in place and bumping fetched_at."""
    def _run(reporter) -> dict:
        from abkit.jobs import run_refresh_sql_dataset

        return run_refresh_sql_dataset(user, dataset_id, progress_callback=reporter.stage)

    job = runner.submit("dataset_refresh", uuid_mod.UUID(user.id), _run)
    return JobAccepted(job_id=str(job.id))


@router.get("/{dataset_id}/usage", response_model=DatasetUsageResponse)
def get_dataset_usage(dataset_id: str, user: CurrentUser = Depends(get_current_user)) -> DatasetUsageResponse:
    """Which experiments use this dataset (UX package, Datasets §2.2) —
    the frontend calls this right before showing a Delete confirmation, to
    decide between the plain confirm Modal (unused) and the strict
    DELETE-typed one listing them (used)."""
    from abkit.jobs import get_dataset_usage as _get_dataset_usage

    return DatasetUsageResponse(experiments=_get_dataset_usage(user, dataset_id))


@router.delete("/{dataset_id}", status_code=204)
def delete_dataset(
    dataset_id: str, body: DeleteDatasetRequest, user: CurrentUser = Depends(get_current_user),
) -> None:
    """Owner (uploaded_by) or Admin. confirm="DELETE" is only enforced when
    the dataset is actually in use (abkit/jobs.py::run_delete_dataset raises
    DatasetInUseError otherwise, mapped to 400 by backend/errors.py) — an
    unused dataset deletes on a plain request, matching the two-tier
    confirmation the frontend shows (UX package, Datasets §2.2)."""
    from abkit.jobs import run_delete_dataset

    run_delete_dataset(user, dataset_id, confirm=body.confirm)


@router.post("/bulk-delete", response_model=BulkDeleteDatasetsResult)
def bulk_delete_datasets(
    body: BulkDeleteDatasetsRequest, user: CurrentUser = Depends(get_current_user),
) -> BulkDeleteDatasetsResult:
    """Bulk select + Delete on the Datasets list (mirrors
    /experiments/bulk-delete) — permission (owner-or-admin) is checked PER
    dataset on the server; rows the user can't delete are skipped, not
    silently dropped. One typed-DELETE confirmation covers the whole batch,
    including datasets in use by experiments (confirm="DELETE" always passed
    through to run_delete_dataset, same as the single-item flow's "used"
    branch) — the frontend has already shown their used-by info before this
    request is ever sent."""
    from abkit.auth.guards import AuthError
    from abkit.jobs import run_delete_dataset

    if body.confirm != "DELETE":
        raise APIError(400, "confirmation_required", "Type DELETE to confirm")

    deleted: list[str] = []
    skipped: list[BulkDeleteDatasetsSkipped] = []
    for dataset_id in body.dataset_ids:
        ds = DatasetRepo().get_by_id(uuid_mod.UUID(dataset_id))
        if ds is None:
            skipped.append(BulkDeleteDatasetsSkipped(dataset_id=dataset_id, reason="not found"))
            continue
        try:
            run_delete_dataset(user, dataset_id, confirm="DELETE")
            deleted.append(dataset_id)
        except AuthError:
            skipped.append(BulkDeleteDatasetsSkipped(dataset_id=dataset_id, reason="no permission"))
    return BulkDeleteDatasetsResult(deleted=deleted, skipped=skipped)


@router.patch("/{dataset_id}", response_model=PatchDatasetResponse)
def patch_dataset(
    dataset_id: str, body: PatchDatasetRequest,
    user: CurrentUser = Depends(get_current_user),
    runner: JobRunner = Depends(get_job_runner),
) -> PatchDatasetResponse:
    """Owner or Admin (UX package, Datasets §2.3). Edits `name` immediately;
    for source='sql', a changed connection_id/sql_text also submits a
    re-fetch job (same mechanism as Refresh) — job_id is set in the response
    only when that happened, so the frontend knows whether to poll."""
    from abkit.jobs import run_refresh_sql_dataset, run_update_dataset

    result = run_update_dataset(
        user, dataset_id, name=body.name, connection_id=body.connection_id, sql_text=body.sql_text,
        source_schema=body.source_schema, source_table=body.source_table,
    )

    job_id = None
    if result["needs_refetch"]:
        def _run(reporter) -> dict:
            return run_refresh_sql_dataset(user, dataset_id, progress_callback=reporter.stage)

        job = runner.submit("dataset_refresh", uuid_mod.UUID(user.id), _run)
        job_id = str(job.id)

    ds = DatasetRepo().get_by_id(uuid_mod.UUID(dataset_id))
    exp_name_by_id = {e.id: e.name for e in ExperimentRepo().list_all()}
    email_by_id = {u.id: u.email for u in UserRepo().list_all()}
    connection_name_by_id = {c.id: c.display_name for c in DatabaseConnectionRepo().list_all()}
    out = _to_dataset_out(ds, exp_name_by_id, email_by_id, connection_name_by_id)
    return PatchDatasetResponse(dataset=out, job_id=job_id)
