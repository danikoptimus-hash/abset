"""FRONTEND.md §3.2/§5.2: список, предпросмотр и загрузка датасетов
(Dataset.storage_path — CSV, как и все текущие загрузки в app.py через
st.file_uploader + pd.read_csv).

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

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from abkit.auth.guards import CurrentUser
from abkit.db.repositories import DatasetRepo, ExperimentRepo, UserRepo
from abkit.db.store import DbExperimentStore
from backend.deps import get_current_user, require_min_role
from backend.errors import APIError
from backend.schemas.datasets import DatasetOut, DatasetPreview, PaginatedDatasets

router = APIRouter(prefix="/datasets", tags=["datasets"])

_VALID_KINDS = ("pre_design", "post_analysis", "validation")


def _to_dataset_out(d, exp_name_by_id: dict, email_by_id: dict) -> DatasetOut:
    return DatasetOut(
        id=str(d.id), experiment_id=str(d.experiment_id) if d.experiment_id else None,
        experiment_name=exp_name_by_id.get(d.experiment_id),
        kind=d.kind, filename=d.filename, n_rows=d.n_rows, columns=d.columns,
        uploaded_by_email=email_by_id.get(d.uploaded_by) if d.uploaded_by else None,
        uploaded_at=d.uploaded_at,
    )


@router.get("", response_model=PaginatedDatasets)
def list_datasets(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
) -> PaginatedDatasets:
    all_datasets = DatasetRepo().list_all()
    total = len(all_datasets)
    start = (page - 1) * page_size
    page_items = all_datasets[start : start + page_size]

    exp_name_by_id = {e.id: e.name for e in ExperimentRepo().list_all()}
    email_by_id = {u.id: u.email for u in UserRepo().list_all()}

    items = [_to_dataset_out(d, exp_name_by_id, email_by_id) for d in page_items]
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
                raise APIError(413, "payload_too_large", f"Файл превышает лимит {max_mb} МБ")
            out.write(chunk)


@router.post("", response_model=DatasetOut, status_code=201)
def upload_dataset(
    kind: str = Form(...),
    experiment_name: str | None = Form(default=None),
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_min_role("editor")),
) -> DatasetOut:
    if kind not in _VALID_KINDS:
        raise APIError(422, "validation_error", f"kind должен быть одним из {_VALID_KINDS}")

    experiment_id = None
    if experiment_name:
        exp = ExperimentRepo().get_by_name(experiment_name)
        if exp is None:
            raise APIError(404, "not_found", f"Эксперимент '{experiment_name}' не найден")
        experiment_id = exp.id

    store = DbExperimentStore()
    dest_dir = (store.data_dir / experiment_name / "uploads") if experiment_name else (
        store.data_dir / "_uploads"
    )
    dest_path = dest_dir / f"{uuid_mod.uuid4().hex}_{file.filename}"
    _stream_upload_to_disk(file, dest_path)

    try:
        data = pd.read_csv(dest_path)
    except Exception as e:
        dest_path.unlink(missing_ok=True)
        raise APIError(422, "validation_error", f"Не удалось прочитать CSV: {e}") from e

    dataset_id = DatasetRepo().create(
        kind=kind, filename=file.filename, n_rows=len(data), columns=list(data.columns),
        storage_path=str(dest_path), sha256=DatasetRepo.compute_sha256(data),
        experiment_id=experiment_id, uploaded_by=uuid_mod.UUID(user.id),
    )
    ds = DatasetRepo().get_by_id(dataset_id)
    exp_name_by_id = {experiment_id: experiment_name} if experiment_id else {}
    email_by_id = {uuid_mod.UUID(user.id): user.email}
    out = _to_dataset_out(ds, exp_name_by_id, email_by_id)
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
        raise APIError(422, "validation_error", "Некорректный идентификатор датасета") from e

    ds = DatasetRepo().get_by_id(parsed_id)
    if ds is None:
        raise APIError(404, "not_found", f"Датасет '{dataset_id}' не найден")
    try:
        preview_df = pd.read_csv(ds.storage_path, nrows=rows)
    except OSError as e:
        raise APIError(404, "not_found", "Файл датасета недоступен на диске") from e

    # NaN не валиден в JSON (json.dumps с allow_nan=True пишет литерал NaN,
    # который не парсится стандартными JS/JSON-клиентами) — заменяем на None.
    preview_df = preview_df.where(pd.notnull(preview_df), None)
    return DatasetPreview(
        filename=ds.filename, n_rows=ds.n_rows, columns=ds.columns,
        rows=preview_df.to_dict(orient="records"),
    )
