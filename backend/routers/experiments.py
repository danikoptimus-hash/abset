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
from abkit.db.repositories import AuditRepo, BlockRepo, DatasetRepo, ExperimentRepo, UserRepo
from abkit.db.store import DbExperimentStore
from backend.deps import get_current_user, get_job_runner, require_min_role
from backend.errors import APIError
from backend.jobs.runner import JobRunner
from backend.schemas.blocks import BlockIn, BlockOut
from backend.schemas.design import JobAccepted
from backend.schemas.experiments import (
    REPORT_FILENAMES,
    AnalyzeDemoRequest,
    AnalyzeRequest,
    AuditEntryOut,
    DeleteExperimentRequest,
    DeletionSummary,
    ExperimentDetail,
    ExperimentSummary,
    FileInfo,
    PaginatedAudit,
    PaginatedExperiments,
    PatchExperimentRequest,
    SampleInfo,
    StatusChangeRequest,
    ValidateRequest,
)

router = APIRouter(prefix="/experiments", tags=["experiments"])


def _artifact_dir(name: str) -> Path:
    return DbExperimentStore().data_dir / name


def _get_experiment_or_404(name: str):
    exp = ExperimentRepo().get_by_name(name)
    if exp is None:
        raise APIError(404, "not_found", f"Эксперимент '{name}' не найден")
    return exp


def _owner_email(owner_id) -> str | None:
    user = UserRepo().get_by_id(owner_id)
    return user.email if user else None


def _can_see_draft(exp, user: CurrentUser) -> bool:
    return user.role == "admin" or str(user.id) == str(exp.owner_id)


def _visible_or_404(exp, user: CurrentUser):
    """Draft виден только владельцу и admin (FRONTEND.md §1/§3.3) — для всех
    остальных ролей ведет себя как несуществующий эксперимент (404, не 403 —
    не раскрываем сам факт существования чужого черновика)."""
    if exp.publication_status == "draft" and not _can_see_draft(exp, user):
        raise APIError(404, "not_found", f"Эксперимент '{exp.name}' не найден")
    return exp


@router.get("", response_model=PaginatedExperiments)
def list_experiments(
    status: str | None = None,
    owner: str | None = None,
    pub: str | None = None,
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
) -> PaginatedExperiments:
    # Резолвим email владельца одним проходом по users вместо N+1 запроса
    # на эксперимент (FRONTEND.md §3.2: список фильтруется по owner).
    owner_email_by_id = {u.id: u.email for u in UserRepo().list_all()}

    all_exps = ExperimentRepo().list_all()
    all_exps = [e for e in all_exps if e.publication_status == "published" or _can_see_draft(e, user)]
    if status:
        all_exps = [e for e in all_exps if e.status == status]
    if pub:
        all_exps = [e for e in all_exps if e.publication_status == pub]
    if owner:
        needle = owner.lower()
        all_exps = [
            e for e in all_exps if needle in (owner_email_by_id.get(e.owner_id) or "").lower()
        ]
    if q:
        needle = q.lower()
        all_exps = [e for e in all_exps if needle in e.name.lower()]
    total = len(all_exps)
    start = (page - 1) * page_size
    page_items = all_exps[start : start + page_size]
    items = [
        ExperimentSummary(
            name=e.name, status=e.status, publication_status=e.publication_status,
            owner_email=owner_email_by_id.get(e.owner_id),
            created_at=e.created_at, started_at=e.started_at,
            completed_at=e.completed_at, archived_at=e.archived_at,
        )
        for e in page_items
    ]
    return PaginatedExperiments(items=items, total=total, page=page, page_size=page_size)


@router.get("/{name}", response_model=ExperimentDetail)
def get_experiment(name: str, user: CurrentUser = Depends(get_current_user)) -> ExperimentDetail:
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
    return ExperimentDetail(
        name=exp.name, status=exp.status, publication_status=exp.publication_status,
        owner_email=owner.email if owner else None, owner_name=owner.name if owner else None,
        config=exp.config, design_summary=exp.design_summary,
        created_at=exp.created_at, started_at=exp.started_at,
        completed_at=exp.completed_at, archived_at=exp.archived_at,
        available_reports=available_reports, files=files,
    )


@router.get("/{name}/reports/{report_name}", response_class=HTMLResponse)
def get_report(report_name: str, name: str, user: CurrentUser = Depends(get_current_user)) -> HTMLResponse:
    _get_experiment_or_404(name)
    if report_name not in REPORT_FILENAMES:
        raise APIError(404, "not_found", f"Отчет '{report_name}' не поддерживается")
    report_path = _artifact_dir(name) / report_name
    if not report_path.exists():
        raise APIError(404, "not_found", f"Отчет '{report_name}' еще не создан")
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
        raise APIError(404, "not_found", f"Файл '{filename}' не найден")
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
        raise APIError(404, "not_found", "Выборки для этого эксперимента не найдены")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for csv_path in csv_paths:
            zf.write(csv_path, arcname=csv_path.name)
    buffer.seek(0)
    return StreamingResponse(
        buffer, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}_samples.zip"'},
    )


@router.get("/{name}/results")
def get_results(name: str, user: CurrentUser = Depends(get_current_user)) -> dict:
    from abkit.db.repositories import ResultRepo

    exp = _get_experiment_or_404(name)
    result = ResultRepo().latest_for_experiment(exp.id)
    if result is None:
        raise APIError(404, "not_found", "Результаты анализа для этого эксперимента еще не готовы")
    return result.results


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


def _to_summary(exp) -> ExperimentSummary:
    owner = UserRepo().get_by_id(exp.owner_id)
    return ExperimentSummary(
        name=exp.name, status=exp.status, publication_status=exp.publication_status,
        owner_email=owner.email if owner else None,
        created_at=exp.created_at, started_at=exp.started_at,
        completed_at=exp.completed_at, archived_at=exp.archived_at,
    )


@router.post("/{name}/status", response_model=ExperimentSummary)
def change_status(
    name: str, body: StatusChangeRequest, user: CurrentUser = Depends(get_current_user),
) -> ExperimentSummary:
    from abkit.jobs import run_update_status

    run_update_status(user, name, body.to)
    return _to_summary(_get_experiment_or_404(name))


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
    return _to_summary(_get_experiment_or_404(current_name))


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
        raise APIError(400, "confirmation_required", "Для удаления нужно ввести подтверждение DELETE")
    run_delete_experiment(user, name)
    return {"ok": True}


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
    from abkit.auth.guards import require_owner_or_admin

    exp = _get_experiment_or_404(name)
    require_owner_or_admin(user, str(exp.owner_id))
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


def _load_dataset_df(dataset_id: str) -> pd.DataFrame:
    try:
        parsed_id = uuid_mod.UUID(dataset_id)
    except ValueError as e:
        raise APIError(422, "validation_error", "Некорректный идентификатор датасета") from e
    dataset = DatasetRepo().get_by_id(parsed_id)
    if dataset is None:
        raise APIError(404, "not_found", f"Датасет '{dataset_id}' не найден")
    return pd.read_csv(dataset.storage_path)


def _save_analysis(name: str, results) -> None:
    """report()+save_analysis_result — ПОСЛЕ этого GET /{name}/results
    (R2) возвращает настоящий результат, а не 404 (analysis_results иначе
    никогда не заполняется — save_analysis_result определен, но раньше нигде
    не вызывался, см. abkit/db/store.py).

    Персистится НЕ results.to_json() напрямую, а тот же payload + отдельный
    ключ "chart_data" (backend/chart_data.py) — данные для ECharts (R6,
    FRONTEND.md §5.2), посчитанные из results.context (raw_values/
    segment_results/daily_results), которого нет в to_json(). AnalysisResults.
    to_json() (ядро, abkit/analysis/results.py) не меняется — им по-прежнему
    пользуются CLI и Streamlit без каких-либо отличий."""
    import json

    from backend.chart_data import build_chart_data, sanitize_json_floats

    report_path = results.report()
    payload = json.loads(results.to_json())
    payload["chart_data"] = build_chart_data(results)
    payload = sanitize_json_floats(payload)
    DbExperimentStore().save_analysis_result(name, json.dumps(payload, ensure_ascii=False), report_path)


@router.post("/{name}/analyze", response_model=JobAccepted, status_code=202)
def start_analyze(
    name: str, body: AnalyzeRequest,
    user: CurrentUser = Depends(require_min_role("editor")),
    runner: JobRunner = Depends(get_job_runner),
) -> JobAccepted:
    _get_experiment_or_404(name)
    data = _load_dataset_df(body.dataset_id)

    def _run(reporter) -> dict[str, Any]:
        from abkit.experiment import Experiment
        from abkit.jobs import run_analyze

        experiment = Experiment.load(name)
        results = run_analyze(
            user, experiment, data, correction=body.correction,
            compare_methods=body.compare_methods, date_col=body.date_col,
            progress_callback=reporter.stage,
        )
        _save_analysis(name, results)
        return {"experiment_name": name}

    job = runner.submit("analyze", uuid_mod.UUID(user.id), _run)
    return JobAccepted(job_id=str(job.id))


@router.post("/{name}/analyze/demo", response_model=JobAccepted, status_code=202)
def start_analyze_demo(
    name: str, body: AnalyzeDemoRequest,
    user: CurrentUser = Depends(require_min_role("editor")),
    runner: JobRunner = Depends(get_job_runner),
) -> JobAccepted:
    _get_experiment_or_404(name)

    def _run(reporter) -> dict[str, Any]:
        from abkit.demo_data import generate_demo_post_data_for_config
        from abkit.experiment import Experiment
        from abkit.jobs import run_analyze

        experiment = Experiment.load(name)
        reporter.stage("Генерируем демо пост-данные...")
        data = generate_demo_post_data_for_config(
            experiment.config, experiment.assignments, effect=body.effect
        )
        results = run_analyze(user, experiment, data, progress_callback=reporter.stage)
        _save_analysis(name, results)
        return {"experiment_name": name}

    job = runner.submit("analyze_demo", uuid_mod.UUID(user.id), _run)
    return JobAccepted(job_id=str(job.id))


@router.post("/{name}/validate", response_model=JobAccepted, status_code=202)
def start_validate(
    name: str, body: ValidateRequest,
    user: CurrentUser = Depends(require_min_role("editor")),
    runner: JobRunner = Depends(get_job_runner),
) -> JobAccepted:
    import dataclasses

    _get_experiment_or_404(name)
    data = _load_dataset_df(body.dataset_id)

    def _run(reporter) -> dict[str, Any]:
        from abkit.experiment import Experiment
        from abkit.jobs import run_validate_aa, run_validate_ab

        experiment = Experiment.load(name)
        reporter.stage("A/A валидация...")
        # show_progress=False: run_aa/run_ab по умолчанию рисуют rich progress
        # bar в консоль — в фоновом потоке backend'а (нет реального терминала)
        # это на Windows падает с "'charmap' codec can't encode characters"
        # (rich использует дефолтную cp1252-консоль для non-TTY вывода); прогресс
        # и так идет через reporter.counts() -> job.progress (GET /jobs/{id}).
        aa_report = run_validate_aa(
            user, data, experiment.config, n_sims=body.n_sims,
            compare_methods=body.compare_methods, progress_callback=reporter.counts,
            show_progress=False,
        )
        reporter.stage("A/B валидация...")
        ab_report = run_validate_ab(
            user, data, experiment.config, n_sims=body.n_sims, effect=body.effect,
            compare_methods=body.compare_methods, progress_callback=reporter.counts,
            show_progress=False,
        )
        return {
            "aa": {"methods": [dataclasses.asdict(m) for m in aa_report.methods]},
            "ab": {"methods": [dataclasses.asdict(m) for m in ab_report.methods]},
        }

    job = runner.submit("validate", uuid_mod.UUID(user.id), _run)
    return JobAccepted(job_id=str(job.id))
