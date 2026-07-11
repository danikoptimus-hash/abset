"""POST /design (FRONTEND.md §3.2/§4): запускает Experiment.design() в фоне
через JobRunner. isolation="warn": сначала считаем пересечение (apply_isolation
в режиме "только посчитать, не фильтровать") — если оно непустое и
confirmed=False, job завершается статусом requires_confirmation вместо
запуска полного дизайна; повторный вызов с confirmed=True (тот же конфиг)
продолжает."""

from __future__ import annotations

import uuid as uuid_mod
from typing import Any

import pandas as pd

from fastapi import APIRouter, Depends

from abkit.auth.guards import CurrentUser
from abkit.config import DesignConfig
from abkit.dataset_files import read_dataset_file
from abkit.db.repositories import DatasetRepo
from backend.deps import get_job_runner, require_min_role
from backend.errors import APIError
from backend.jobs import ProgressReporter, RequiresConfirmation
from backend.jobs.runner import JobRunner
from backend.schemas.design import DesignRequest, JobAccepted

router = APIRouter(prefix="/design", tags=["design"])


def _check_isolation_overlap(config: DesignConfig, data: pd.DataFrame, confirmed: bool) -> None:
    """Бросает RequiresConfirmation, если isolation="warn" нашел непустое
    пересечение с другими активными экспериментами, а пользователь еще не
    подтвердил продолжение. Для остальных режимов изоляции — no-op, решение
    принимает сам Experiment.design() как обычно."""
    if config.isolation != "warn" or confirmed:
        return

    from abkit.db.store import DbExperimentStore
    from abkit.design.isolation import apply_isolation

    store = DbExperimentStore()
    isolation_result = apply_isolation(
        data=data, unit_col=config.unit_col, experiments_dir=store.data_dir, mode="warn",
        exclude_experiments=config.exclude_experiments, current_experiment_name=config.name,
        store=store, selected_experiments=config.isolation_selected_experiments,
    )
    if isolation_result.excluded_by_experiment:
        raise RequiresConfirmation(
            {
                "overlap": sum(isolation_result.excluded_by_experiment.values()),
                "by_experiment": isolation_result.excluded_by_experiment,
            }
        )


@router.post("", response_model=JobAccepted, status_code=202)
def start_design(
    body: DesignRequest,
    user: CurrentUser = Depends(require_min_role("editor")),
    runner: JobRunner = Depends(get_job_runner),
) -> JobAccepted:
    config = body.config

    if config.split_source == "external":
        # Item 12: no dataset at all — the split happens in an outside
        # system (Firebase A/B Testing and similar), ABSet only stores the
        # declared groups/metrics for later analysis.
        def _run_external(reporter: ProgressReporter) -> dict[str, Any]:
            from abkit.jobs import run_design_external

            experiment = run_design_external(user, config, progress_callback=reporter.stage)
            return {"experiment_name": experiment.name}

        job = runner.submit("design", uuid_mod.UUID(user.id), _run_external)
        return JobAccepted(job_id=str(job.id))

    if not body.dataset_id:
        raise APIError(422, "validation_error", "dataset_id is required for split_source='abkit'")
    try:
        dataset_uuid = uuid_mod.UUID(body.dataset_id)
    except ValueError as e:
        raise APIError(422, "validation_error", "Invalid dataset id") from e

    dataset = DatasetRepo().get_by_id(dataset_uuid)
    if dataset is None:
        raise APIError(404, "not_found", f"Dataset '{body.dataset_id}' not found")

    confirmed = body.confirmed
    # unit_col как str: иначе числовой ID с ведущими нулями ("007123")
    # необратимо теряет их при авто-парсинге pandas в int64.
    data = read_dataset_file(dataset.storage_path, dtype={config.unit_col: str})

    def _run(reporter: ProgressReporter) -> dict[str, Any]:
        from abkit.db.repositories import ExperimentDatasetRepo, ExperimentRepo
        from abkit.jobs import run_design

        _check_isolation_overlap(config, data, confirmed)
        experiment = run_design(user, config, data, progress_callback=reporter.stage)
        exp_row = ExperimentRepo().get_by_name(experiment.name)
        if dataset.experiment_id is None:
            DatasetRepo().attach_to_experiment(dataset.id, exp_row.id)
        # DB3 (dataset-centric model): every experiment<->dataset use is
        # recorded here, not just the first/primary one attach_to_experiment
        # sets above — a dataset selected for design in more than one
        # experiment still needs a link row for each.
        ExperimentDatasetRepo().link(exp_row.id, dataset.id, kind="pre_design")
        return {"experiment_name": experiment.name}

    job = runner.submit("design", uuid_mod.UUID(user.id), _run)
    return JobAccepted(job_id=str(job.id))
