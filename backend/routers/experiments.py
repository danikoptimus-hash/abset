"""R2 (FRONTEND.md §3.2): read-only чтение экспериментов — тонкая обертка над
ExperimentRepo/AuditRepo/DbExperimentStore, без изменений в статистическом
ядре. design_summary никогда не заполняется в create_experiment (см.
abkit/db/store.py) — в ExperimentDetail поле честно прокидывается как None,
а не подделывается (то же решение, что и в
app.py::_render_experiment_detail_panel, которая берет данные MDE-таблицы
из уже отрендеренного design_report.html, а не пересобирает их)."""

from __future__ import annotations

import io
import uuid as uuid_mod
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from abkit.auth.guards import CurrentUser
from abkit.dataset_files import read_dataset_file
from abkit.db.repositories import AuditRepo, BlockRepo, DatasetRepo, ExperimentRepo, UserRepo
from abkit.db.store import DbExperimentStore
from backend.deps import get_current_user, get_job_runner, require_min_role
from backend.errors import APIError
from backend.jobs.runner import JobRunner
from backend.schemas.blocks import BlockIn, BlockOut
from backend.schemas.datasets import DatasetOut
from backend.schemas.design import JobAccepted
from backend.schemas.experiments import (
    REPORT_FILENAMES,
    AnalyzeDemoRequest,
    AnalyzeRequest,
    AuditEntryOut,
    BulkDeleteRequest,
    BulkDeleteResult,
    BulkDeleteSkipped,
    DeleteExperimentRequest,
    DeletionSummary,
    ExperimentDetail,
    ExperimentPropertiesOut,
    ExperimentSummary,
    FileInfo,
    PaginatedAudit,
    PaginatedExperiments,
    PatchExperimentRequest,
    SampleInfo,
    StatusChangeRequest,
    UpdateExperimentPropertiesRequest,
    UserBrief,
    ValidateRequest,
)
from backend.schemas.tags import SetExperimentTagsRequest, TagOut

router = APIRouter(prefix="/experiments", tags=["experiments"])


def _to_tag_out(t) -> TagOut:
    return TagOut(id=str(t.id), name=t.name, color=t.color)


def _artifact_dir(name: str) -> Path:
    return DbExperimentStore().data_dir / name


def _get_experiment_or_404(name: str):
    exp = ExperimentRepo().get_by_name(name)
    if exp is None:
        raise APIError(404, "not_found", f"Experiment '{name}' not found")
    return exp


def _visible_or_404(exp, user: CurrentUser):
    """Видимость (draft/visible_roles/experiment_access) — abkit/access.py, UX
    package + FRONTEND.md §1/§3.3. Для невидимых ведет себя как несуществующий
    эксперимент (404, не 403 — не раскрываем сам факт существования)."""
    from abkit.access import can_view_experiment

    if not can_view_experiment(user, exp):
        raise APIError(404, "not_found", f"Experiment '{exp.name}' not found")
    return exp


@router.get("", response_model=PaginatedExperiments)
def list_experiments(
    status: str | None = None,
    owner: str | None = None,
    pub: str | None = None,
    q: str | None = None,
    tag: list[str] | None = Query(default=None, description="Tag id(s) — AND logic across multiple"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
) -> PaginatedExperiments:
    from abkit.access import can_view_experiment, is_owner_or_granted
    from abkit.db.repositories import ExperimentAccessRepo, ExperimentTagRepo

    # Резолвим владельца одним проходом по users вместо N+1 запроса на
    # эксперимент (FRONTEND.md §3.2: список фильтруется по owner, плюс avatar
    # инициалы нужны на каждую строку). Та же логика для experiment_access —
    # один запрос вместо одного на строку.
    user_by_id = {u.id: u for u in UserRepo().list_all()}
    access_experiment_ids = ExperimentAccessRepo().experiment_ids_for_user(uuid_mod.UUID(user.id))
    can_edit_role = user.role in ("editor", "admin")

    all_exps = ExperimentRepo().list_all()
    all_exps = [e for e in all_exps if can_view_experiment(user, e, access_experiment_ids)]
    if status:
        all_exps = [e for e in all_exps if e.status == status]
    if pub:
        all_exps = [e for e in all_exps if e.publication_status == pub]
    if owner:
        needle = owner.lower()
        all_exps = [
            e for e in all_exps
            if needle in (getattr(user_by_id.get(e.owner_id), "email", "") or "").lower()
        ]
    if q:
        # Datasets follow-up (Tags §3.5): live search matches by experiment
        # name OR by any of its tag names — a single `q` box covers both,
        # no separate "search tags" field.
        needle = q.lower()
        tags_by_exp_all = ExperimentTagRepo().list_for_experiments([e.id for e in all_exps])
        all_exps = [
            e for e in all_exps
            if needle in e.name.lower() or any(needle in t.name.lower() for t in tags_by_exp_all.get(e.id, []))
        ]
    if tag:
        # AND logic across multiple selected tags (UX package, Tags §3.5) —
        # an experiment must carry EVERY selected tag, not just one of them.
        wanted_tag_ids = {uuid_mod.UUID(t) for t in tag}
        tags_by_exp_all = ExperimentTagRepo().list_for_experiments([e.id for e in all_exps])
        all_exps = [
            e for e in all_exps
            if wanted_tag_ids.issubset({t.id for t in tags_by_exp_all.get(e.id, [])})
        ]
    total = len(all_exps)
    start = (page - 1) * page_size
    page_items = all_exps[start : start + page_size]
    tags_by_exp = ExperimentTagRepo().list_for_experiments([e.id for e in page_items])
    items = [
        ExperimentSummary(
            name=e.name, status=e.status, publication_status=e.publication_status,
            owner_id=str(e.owner_id) if e.owner_id else None,
            owner_email=getattr(user_by_id.get(e.owner_id), "email", None),
            owner_first_name=getattr(user_by_id.get(e.owner_id), "first_name", None),
            owner_last_name=getattr(user_by_id.get(e.owner_id), "last_name", None),
            can_edit=can_edit_role and is_owner_or_granted(user, e, access_experiment_ids),
            created_at=e.created_at, started_at=e.started_at,
            completed_at=e.completed_at, archived_at=e.archived_at,
            tags=[_to_tag_out(t) for t in tags_by_exp.get(e.id, [])],
        )
        for e in page_items
    ]
    return PaginatedExperiments(items=items, total=total, page=page, page_size=page_size)


@router.post("/bulk-delete", response_model=BulkDeleteResult)
def bulk_delete_experiments(
    body: BulkDeleteRequest, user: CurrentUser = Depends(require_min_role("editor")),
) -> BulkDeleteResult:
    """Bulk select + Delete on the experiments list (UX package, list п.E) —
    any selected rows go in, but permission is checked PER experiment on the
    server (п.E.5): rows the user can't edit are skipped, not silently
    dropped or (worse) deleted anyway. Loops the existing single-experiment
    delete path so each one gets its own audit_log entry, same as deleting
    them one at a time."""
    from abkit.auth.guards import AuthError
    from abkit.jobs import run_delete_experiment

    if body.confirm != "DELETE":
        raise APIError(400, "confirmation_required", "Type DELETE to confirm")

    deleted: list[str] = []
    skipped: list[BulkDeleteSkipped] = []
    for name in body.names:
        exp = ExperimentRepo().get_by_name(name)
        if exp is None:
            skipped.append(BulkDeleteSkipped(name=name, reason="not found"))
            continue
        try:
            run_delete_experiment(user, name)
            deleted.append(name)
        except AuthError:
            skipped.append(BulkDeleteSkipped(name=name, reason="no permission"))
    return BulkDeleteResult(deleted=deleted, skipped=skipped)


def _get_last_modified(exp) -> tuple:
    """Самое свежее из audit_log (status/publication/rename/properties/
    analyze — все аудируются, см. abkit/jobs.py::_audit) и
    experiment_blocks.updated_at/updated_by (блоки НЕ аудируются отдельно,
    только эти две колонки трассируют правку). Фильтр по object_name, как
    и GET /{name}/audit — после переименования старые записи под прежним
    именем этим запросом не видны (то же существующее ограничение)."""
    from abkit.db.repositories import AuditRepo, BlockRepo

    candidates: list[tuple] = []

    recent_audit = AuditRepo().list_recent(limit=1, object_name=exp.name)
    if recent_audit:
        candidates.append((recent_audit[0].ts, recent_audit[0].user_id))

    blocks = BlockRepo().list_for_experiment(exp.id)
    edited_blocks = [b for b in blocks if b.updated_by is not None]
    if edited_blocks:
        latest_block = max(edited_blocks, key=lambda b: b.updated_at)
        candidates.append((latest_block.updated_at, latest_block.updated_by))

    if not candidates:
        return (None, None)
    return max(candidates, key=lambda c: c[0])


@router.get("/{name}", response_model=ExperimentDetail)
def get_experiment(name: str, user: CurrentUser = Depends(get_current_user)) -> ExperimentDetail:
    from abkit.access import is_owner_or_granted
    from abkit.db.repositories import ExperimentTagRepo

    exp = _visible_or_404(_get_experiment_or_404(name), user)
    owner = UserRepo().get_by_id(exp.owner_id)
    path = _artifact_dir(name)
    available_reports = [r for r in REPORT_FILENAMES if (path / r).exists()]
    files = (
        [
            FileInfo(path=str(p.relative_to(path)), size_kb=round(p.stat().st_size / 1024, 1))
            for p in sorted(path.rglob("*"))
            if p.is_file()
        ]
        if path.exists()
        else []
    )
    last_modified_at, last_modified_by_id = _get_last_modified(exp)
    last_modified_user = UserRepo().get_by_id(last_modified_by_id) if last_modified_by_id else None
    return ExperimentDetail(
        name=exp.name, status=exp.status, publication_status=exp.publication_status,
        owner_id=str(exp.owner_id) if exp.owner_id else None,
        owner_email=owner.email if owner else None,
        owner_first_name=owner.first_name if owner else None,
        owner_last_name=owner.last_name if owner else None,
        can_edit=user.role in ("editor", "admin") and is_owner_or_granted(user, exp),
        last_modified_at=last_modified_at,
        last_modified_by_first_name=last_modified_user.first_name if last_modified_user else None,
        last_modified_by_last_name=last_modified_user.last_name if last_modified_user else None,
        last_modified_by_email=last_modified_user.email if last_modified_user else None,
        config=exp.config, design_summary=exp.design_summary,
        created_at=exp.created_at, started_at=exp.started_at,
        completed_at=exp.completed_at, archived_at=exp.archived_at,
        available_reports=available_reports, files=files,
        tags=[_to_tag_out(t) for t in ExperimentTagRepo().list_for_experiment(exp.id)],
    )


@router.put("/{name}/tags", response_model=list[TagOut])
def put_experiment_tags(
    name: str, body: SetExperimentTagsRequest, user: CurrentUser = Depends(get_current_user),
) -> list[TagOut]:
    """Edit Properties modal's Tags field (UX package, Tags §3.3) — same
    edit-access gate as the rest of Properties (owner/access-editor/Admin,
    enforced in abkit/jobs.py::run_set_experiment_tags). Always a full
    replace: the frontend sends the complete desired tag list."""
    from abkit.jobs import run_set_experiment_tags

    tags = run_set_experiment_tags(user, name, body.tag_ids)
    return [_to_tag_out(t) for t in tags]


@router.get("/{name}/reports/{report_name}", response_class=HTMLResponse)
def get_report(report_name: str, name: str, user: CurrentUser = Depends(get_current_user)) -> HTMLResponse:
    _get_experiment_or_404(name)
    if report_name not in REPORT_FILENAMES:
        raise APIError(404, "not_found", f"Report '{report_name}' is not supported")
    report_path = _artifact_dir(name) / report_name
    if not report_path.exists():
        raise APIError(404, "not_found", f"Report '{report_name}' has not been created yet")
    return HTMLResponse(content=report_path.read_text(encoding="utf-8"))


@router.get("/{name}/samples", response_model=list[SampleInfo])
def list_samples(name: str, user: CurrentUser = Depends(get_current_user)) -> list[SampleInfo]:
    import pandas as pd

    _get_experiment_or_404(name)
    samples_dir = _artifact_dir(name) / "samples"
    csv_paths = sorted(samples_dir.glob("*.csv")) if samples_dir.exists() else []
    return [
        SampleInfo(
            filename=p.name, n_rows=len(pd.read_csv(p)), size_kb=round(p.stat().st_size / 1024, 1)
        )
        for p in csv_paths
    ]


@router.get("/{name}/samples/{filename}")
def download_sample(name: str, filename: str, user: CurrentUser = Depends(get_current_user)) -> Response:
    _get_experiment_or_404(name)
    csv_path = _artifact_dir(name) / "samples" / filename
    if csv_path.suffix != ".csv" or not csv_path.exists():
        raise APIError(404, "not_found", f"File '{filename}' not found")
    return Response(
        content=csv_path.read_bytes(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{name}/samples.zip")
def download_samples_zip(name: str, user: CurrentUser = Depends(get_current_user)) -> StreamingResponse:
    _get_experiment_or_404(name)
    samples_dir = _artifact_dir(name) / "samples"
    csv_paths = sorted(samples_dir.glob("*.csv")) if samples_dir.exists() else []
    if not csv_paths:
        raise APIError(404, "not_found", "No samples found for this experiment")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for csv_path in csv_paths:
            zf.write(csv_path, arcname=csv_path.name)
    buffer.seek(0)
    return StreamingResponse(
        buffer, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}_samples.zip"'},
    )


@router.get("/{name}/design-dataset", response_model=DatasetOut)
def get_design_dataset(name: str, user: CurrentUser = Depends(get_current_user)) -> DatasetOut:
    """Pre-design dataset auto-attached to this experiment (Validation tab
    auto-datasource, UX package Validation п.C.1) — the same data used to
    design it, if design went through a dataset upload (wizard/API) and it
    still exists. 404 if none (older/imported experiments, п.C.4) — frontend
    falls back to manual upload."""
    exp = _visible_or_404(_get_experiment_or_404(name), user)
    pre_design = [d for d in DatasetRepo().list_for_experiment(exp.id) if d.kind == "pre_design"]
    if not pre_design:
        raise APIError(404, "not_found", f"No stored design data for experiment '{name}'")
    latest = max(pre_design, key=lambda d: d.uploaded_at)
    email_by_id = {u.id: u.email for u in UserRepo().list_all()}
    return DatasetOut(
        id=str(latest.id), experiment_id=str(exp.id), experiment_name=name,
        kind=latest.kind, filename=latest.filename, n_rows=latest.n_rows, columns=latest.columns,
        uploaded_by_email=email_by_id.get(latest.uploaded_by), uploaded_at=latest.uploaded_at,
    )


@router.get("/{name}/results")
def get_results(name: str, user: CurrentUser = Depends(get_current_user)) -> dict:
    """results.results as-is (ядро AnalysisResults.to_json() + chart_data,
    см. _save_analysis) плюс "run_meta" — не часть ядрового формата, только
    для строки "Analyzed N ago with dataset X (run #K)" на вкладке Results
    (UX package, п.3). run_number = порядковый номер ЭТОГО прогона среди всех
    прогонов эксперимента; для latest_for_experiment() он всегда равен
    текущему count_for_experiment() (это же и есть последний прогон)."""
    from abkit.db.repositories import ResultRepo

    exp = _get_experiment_or_404(name)
    result = ResultRepo().latest_for_experiment(exp.id)
    if result is None:
        raise APIError(404, "not_found", "Analysis results for this experiment are not ready yet")

    return {
        **result.results,
        "run_meta": {
            "created_at": result.created_at.isoformat(),
            # Frozen at analyze time (migration 0009) — survives the dataset
            # itself being deleted later, unlike a live DatasetRepo lookup.
            "dataset_filename": result.dataset_filename,
            "run_number": ResultRepo().count_for_experiment(exp.id),
        },
    }


@router.get("/{name}/audit", response_model=PaginatedAudit)
def get_experiment_audit(
    name: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
) -> PaginatedAudit:
    _get_experiment_or_404(name)
    repo = AuditRepo()
    offset = (page - 1) * page_size
    entries = repo.list_recent(limit=page_size, offset=offset, object_name=name)
    total = repo.count(object_name=name)
    items = [
        AuditEntryOut(
            id=e.id, ts=e.ts, user_email=e.user_email, action=e.action,
            object_type=e.object_type, object_id=e.object_id, object_name=e.object_name,
            details=e.details,
        )
        for e in entries
    ]
    return PaginatedAudit(items=items, total=total, page=page, page_size=page_size)


def _to_summary(exp, user: CurrentUser) -> ExperimentSummary:
    from abkit.access import is_owner_or_granted

    owner = UserRepo().get_by_id(exp.owner_id)
    return ExperimentSummary(
        name=exp.name, status=exp.status, publication_status=exp.publication_status,
        owner_id=str(exp.owner_id) if exp.owner_id else None,
        owner_email=owner.email if owner else None,
        owner_first_name=owner.first_name if owner else None,
        owner_last_name=owner.last_name if owner else None,
        can_edit=user.role in ("editor", "admin") and is_owner_or_granted(user, exp),
        created_at=exp.created_at, started_at=exp.started_at,
        completed_at=exp.completed_at, archived_at=exp.archived_at,
    )


@router.post("/{name}/status", response_model=ExperimentSummary)
def change_status(
    name: str, body: StatusChangeRequest, user: CurrentUser = Depends(get_current_user),
) -> ExperimentSummary:
    from abkit.jobs import run_update_status

    run_update_status(user, name, body.to)
    return _to_summary(_get_experiment_or_404(name), user)


@router.patch("/{name}", response_model=ExperimentSummary)
def patch_experiment(
    name: str, body: PatchExperimentRequest, user: CurrentUser = Depends(get_current_user),
) -> ExperimentSummary:
    from abkit.db.repositories import RepoError
    from abkit.jobs import run_rename_experiment, run_set_publication_status

    current_name = name
    if body.name and body.name != name:
        try:
            run_rename_experiment(user, current_name, body.name)
        except RepoError as e:
            raise APIError(409, "already_exists", str(e)) from e
        current_name = body.name
    if body.publication_status:
        run_set_publication_status(user, current_name, body.publication_status)
    return _to_summary(_get_experiment_or_404(current_name), user)


@router.get("/{name}/deletion-summary", response_model=DeletionSummary)
def get_deletion_summary(name: str, user: CurrentUser = Depends(get_current_user)) -> DeletionSummary:
    """Реальные числа для модалки подтверждения удаления (FRONTEND.md §5.2:
    "Будут удалены: назначения (N), датасеты (M), результаты (K)") — то же
    самое, что abkit.jobs.run_delete_experiment пишет в audit_log, но здесь
    только для превью, без самого удаления."""
    from abkit.jobs import get_experiment_deletion_summary

    summary = get_experiment_deletion_summary(user, name)
    return DeletionSummary(**summary)


@router.delete("/{name}")
def delete_experiment(
    name: str, body: DeleteExperimentRequest, user: CurrentUser = Depends(get_current_user),
) -> dict[str, bool]:
    from abkit.jobs import run_delete_experiment

    if body.confirm != "DELETE":
        raise APIError(400, "confirmation_required", "Type DELETE to confirm")
    run_delete_experiment(user, name)
    return {"ok": True}


def _to_user_brief(u) -> UserBrief:
    return UserBrief(id=str(u.id), email=u.email, first_name=u.first_name, last_name=u.last_name, role=u.role)


@router.get("/{name}/properties", response_model=ExperimentPropertiesOut)
def get_properties(name: str, user: CurrentUser = Depends(get_current_user)) -> ExperimentPropertiesOut:
    """Edit Properties modal (UX package, like Superset's dashboard Properties)
    — same edit-access gate as saving it, so only owners/access-editors/admin
    can even open the form (matches the "..." menu / hover Edit button being
    shown only to them, FRONTEND.md UX package sections 3 and 5)."""
    from abkit.access import require_experiment_edit_access
    from abkit.db.repositories import ExperimentAccessRepo, ExperimentTagRepo

    exp = _get_experiment_or_404(name)
    require_experiment_edit_access(user, exp)

    owner = UserRepo().get_by_id(exp.owner_id)
    access_rows = ExperimentAccessRepo().list_for_experiment(exp.id)
    user_by_id = {u.id: u for u in UserRepo().list_all()}
    owners = [_to_user_brief(user_by_id[r.user_id]) for r in access_rows if r.access == "owner" and r.user_id in user_by_id]
    editors = [_to_user_brief(user_by_id[r.user_id]) for r in access_rows if r.access == "editor" and r.user_id in user_by_id]
    return ExperimentPropertiesOut(
        name=exp.name, owner=_to_user_brief(owner) if owner else None,
        owners=owners, editors=editors, visible_roles=exp.visible_roles,
        tags=[_to_tag_out(t) for t in ExperimentTagRepo().list_for_experiment(exp.id)],
    )


@router.put("/{name}/properties", response_model=ExperimentPropertiesOut)
def put_properties(
    name: str, body: UpdateExperimentPropertiesRequest, user: CurrentUser = Depends(get_current_user),
) -> ExperimentPropertiesOut:
    from abkit.db.repositories import RepoError
    from abkit.jobs import run_update_experiment_properties

    try:
        run_update_experiment_properties(
            user, name, new_name=body.name, owner_ids=body.owner_ids,
            editor_ids=body.editor_ids, visible_roles=body.visible_roles,
        )
    except RepoError as e:
        raise APIError(409, "already_exists", str(e)) from e
    return get_properties(body.name, user)


@router.get("/{name}/blocks", response_model=list[BlockOut])
def get_blocks(name: str, user: CurrentUser = Depends(get_current_user)) -> list[BlockOut]:
    exp = _visible_or_404(_get_experiment_or_404(name), user)
    blocks = BlockRepo().list_for_experiment(exp.id)
    return [
        BlockOut(
            id=str(b.id), kind=b.kind, title=b.title, content_md=b.content_md,
            position=b.position, updated_at=b.updated_at,
        )
        for b in blocks
    ]


@router.put("/{name}/blocks", response_model=list[BlockOut])
def put_blocks(
    name: str, body: list[BlockIn], user: CurrentUser = Depends(get_current_user),
) -> list[BlockOut]:
    from abkit.access import require_experiment_edit_access

    exp = _get_experiment_or_404(name)
    require_experiment_edit_access(user, exp)
    blocks = BlockRepo().upsert_many(
        exp.id, [b.model_dump() for b in body], updated_by=uuid_mod.UUID(user.id)
    )
    return [
        BlockOut(
            id=str(b.id), kind=b.kind, title=b.title, content_md=b.content_md,
            position=b.position, updated_at=b.updated_at,
        )
        for b in blocks
    ]


def _load_dataset_df(dataset_id: str, unit_col: str | None = None) -> pd.DataFrame:
    try:
        parsed_id = uuid_mod.UUID(dataset_id)
    except ValueError as e:
        raise APIError(422, "validation_error", "Invalid dataset id") from e
    dataset = DatasetRepo().get_by_id(parsed_id)
    if dataset is None:
        raise APIError(404, "not_found", f"Dataset '{dataset_id}' not found")
    # unit_col как str: иначе числовой ID с ведущими нулями ("007123")
    # необратимо теряет их при авто-парсинге pandas в int64 (CSV-датасеты —
    # parquet, source='sql', уже хранит dtype как есть).
    dtype = {unit_col: str} if unit_col else None
    return read_dataset_file(dataset.storage_path, dtype=dtype)


def _save_analysis(
    name: str, results, *, dataset_id: uuid_mod.UUID | None = None, created_by: uuid_mod.UUID | None = None,
) -> None:
    """report()+save_analysis_result — ПОСЛЕ этого GET /{name}/results
    (R2) возвращает настоящий результат, а не 404 (analysis_results иначе
    никогда не заполняется — save_analysis_result определен, но раньше нигде
    не вызывался, см. abkit/db/store.py).

    Персистится НЕ results.to_json() напрямую, а тот же payload + отдельный
    ключ "chart_data" (backend/chart_data.py) — данные для ECharts (R6,
    FRONTEND.md §5.2), посчитанные из results.context (raw_values/
    segment_results/daily_results), которого нет в to_json(). AnalysisResults.
    to_json() (ядро, abkit/analysis/results.py) не меняется — им по-прежнему
    пользуется CLI без каких-либо отличий.

    dataset_id/created_by — для "Analyzed N ago with dataset X (run #K)" на
    вкладке Results (UX package, п.3); None для demo-анализа (нет
    загруженного датасета, только сгенерированные данные). Имя файла
    датасета замораживается здесь же (dataset_filename, миграция 0009) —
    результат остается самодостаточным, даже если сам датасет потом удалят."""
    import json

    from abkit.db.repositories import DatasetRepo
    from backend.chart_data import build_chart_data, sanitize_json_floats

    dataset_filename = None
    if dataset_id is not None:
        ds = DatasetRepo().get_by_id(dataset_id)
        dataset_filename = ds.filename if ds else None

    report_path = results.report()
    payload = json.loads(results.to_json())
    payload["chart_data"] = build_chart_data(results)
    payload = sanitize_json_floats(payload)
    DbExperimentStore().save_analysis_result(
        name, json.dumps(payload, ensure_ascii=False), report_path,
        dataset_id=dataset_id, dataset_filename=dataset_filename, created_by=created_by,
    )


@router.post("/{name}/analyze", response_model=JobAccepted, status_code=202)
def start_analyze(
    name: str, body: AnalyzeRequest,
    user: CurrentUser = Depends(require_min_role("editor")),
    runner: JobRunner = Depends(get_job_runner),
) -> JobAccepted:
    exp = _visible_or_404(_get_experiment_or_404(name), user)
    from abkit.experiment import Experiment

    unit_col = Experiment.load(name).config.unit_col
    data = _load_dataset_df(body.dataset_id, unit_col=unit_col)

    def _run(reporter) -> dict[str, Any]:
        from abkit.db.repositories import ExperimentDatasetRepo
        from abkit.experiment import Experiment
        from abkit.jobs import run_analyze

        experiment = Experiment.load(name)
        results = run_analyze(
            user, experiment, data, correction=body.correction,
            compare_methods=body.compare_methods, date_col=body.date_col,
            progress_callback=reporter.stage,
        )
        _save_analysis(
            name, results,
            dataset_id=uuid_mod.UUID(body.dataset_id), created_by=uuid_mod.UUID(user.id),
        )
        # DB3 (dataset-centric model): record this dataset as used for
        # analysis by this experiment — a dataset may be reused across
        # experiments/kinds, so this is a link, not a single-owner field.
        ExperimentDatasetRepo().link(exp.id, uuid_mod.UUID(body.dataset_id), kind="post_analysis")
        return {"experiment_name": name}

    job = runner.submit("analyze", uuid_mod.UUID(user.id), _run)
    return JobAccepted(job_id=str(job.id))


@router.post("/{name}/demo-post-data", response_model=DatasetOut, status_code=201)
def create_demo_post_data(
    name: str, body: AnalyzeDemoRequest, user: CurrentUser = Depends(require_min_role("editor")),
) -> DatasetOut:
    """"Generate demo post-period data" on the Analysis tab (UX package,
    item B) — only PREPARES a post_analysis dataset (same shape as a real
    upload, synchronous — generation is fast, no job needed), it does NOT
    run analysis. The explicit "Run analysis" button then calls the regular
    POST /{name}/analyze with this dataset_id, same as for an uploaded file.
    Was previously a single job that generated data and ran analysis in one
    step (POST /{name}/analyze/demo) — split so the user can see/confirm the
    prepared data and current options before committing to a run."""
    from abkit.demo_data import generate_demo_post_data_for_config
    from abkit.experiment import Experiment

    exp = _visible_or_404(_get_experiment_or_404(name), user)
    experiment = Experiment.load(name)
    data = generate_demo_post_data_for_config(experiment.config, experiment.assignments, effect=body.effect)

    store = DbExperimentStore()
    dest_dir = store.data_dir / name / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{uuid_mod.uuid4().hex}_demo_post_data.csv"
    data.to_csv(dest_path, index=False)

    dataset_id = DatasetRepo().create(
        kind="post_analysis", filename="demo_post_data.csv", n_rows=len(data), columns=list(data.columns),
        storage_path=str(dest_path), sha256=DatasetRepo.compute_sha256(data),
        experiment_id=exp.id, uploaded_by=uuid_mod.UUID(user.id), source="demo",
    )
    from abkit.db.repositories import ExperimentDatasetRepo

    ExperimentDatasetRepo().link(exp.id, dataset_id, kind="post_analysis")
    ds = DatasetRepo().get_by_id(dataset_id)
    return DatasetOut(
        id=str(ds.id), experiment_id=str(exp.id), experiment_name=name,
        kind=ds.kind, filename=ds.filename, n_rows=ds.n_rows, columns=ds.columns,
        uploaded_by_email=user.email, uploaded_at=ds.uploaded_at, source=ds.source,
    )


@router.post("/{name}/validate", response_model=JobAccepted, status_code=202)
def start_validate(
    name: str, body: ValidateRequest,
    user: CurrentUser = Depends(require_min_role("editor")),
    runner: JobRunner = Depends(get_job_runner),
) -> JobAccepted:
    import dataclasses

    from abkit.experiment import Experiment

    exp = _visible_or_404(_get_experiment_or_404(name), user)
    unit_col = Experiment.load(name).config.unit_col
    data = _load_dataset_df(body.dataset_id, unit_col=unit_col)
    used_dataset = DatasetRepo().get_by_id(uuid_mod.UUID(body.dataset_id))

    def _run(reporter) -> dict[str, Any]:
        from abkit.db.repositories import ExperimentDatasetRepo
        from abkit.experiment import Experiment
        from abkit.jobs import run_validate_aa, run_validate_ab

        experiment = Experiment.load(name)
        reporter.stage("A/A validation...")
        # show_progress=False: run_aa/run_ab по умолчанию рисуют rich progress
        # bar в консоль — в фоновом потоке backend'а (нет реального терминала)
        # это на Windows падает с "'charmap' codec can't encode characters"
        # (rich использует дефолтную cp1252-консоль для non-TTY вывода); прогресс
        # и так идет через reporter.counts() -> job.progress (GET /jobs/{id}).
        aa_report = run_validate_aa(
            user, data, experiment.config, n_sims=body.n_sims,
            compare_methods=body.compare_methods, progress_callback=reporter.counts,
            show_progress=False, dataset_id=body.dataset_id,
        )
        reporter.stage("A/B validation...")
        ab_report = run_validate_ab(
            user, data, experiment.config, n_sims=body.n_sims, effect=body.effect,
            compare_methods=body.compare_methods, progress_callback=reporter.counts,
            show_progress=False, dataset_id=body.dataset_id,
        )
        ExperimentDatasetRepo().link(exp.id, uuid_mod.UUID(body.dataset_id), kind="validation")
        return {
            "aa": {"methods": [dataclasses.asdict(m) for m in aa_report.methods]},
            "ab": {"methods": [dataclasses.asdict(m) for m in ab_report.methods]},
            # UX package, Validation п.C.5: which dataset this ran on.
            "dataset_id": body.dataset_id,
            "dataset_filename": used_dataset.filename if used_dataset else None,
        }

    job = runner.submit("validate", uuid_mod.UUID(user.id), _run)
    return JobAccepted(job_id=str(job.id))
