"""Единая точка запуска мутирующих операций (design/analyze/validate/status
change/delete) — используется backend (FRONTEND.md, jobs manager) и в
перспективе CLI, чтобы guard-проверки (DOCKER.md §4.1) применялись независимо
от UI: Viewer не должен суметь вызвать мутацию даже прямым вызовом этих
функций, в обход спрятанных в UI кнопок (критерий готовности этапа D2,
DOCKER.md §12).

Этап D3 (DOCKER.md §6, §8 пункт 4): каждая функция здесь пишет структурированный
лог тайминга (INFO start/finish/duration_ms, ERROR с traceback при исключении)
и строку в audit_log (DOCKER.md §5) — на уровне сервисной функции, а не UI, чтобы
в перспективе CLI-действия тоже попадали в аудит. Эти функции осмысленны только
в серверном режиме (ABKIT_MODE=db) — в файловом режиме нет модели
пользователей/ролей, и app.py их не вызывает.
"""

from __future__ import annotations

import math
import time
import uuid as uuid_mod
from contextlib import contextmanager
from typing import Any

import pandas as pd

from abkit.access import require_experiment_edit_access
from abkit.analysis.results import AnalysisResults
from abkit.auth.guards import AuthError, CurrentUser, require_role
from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import Experiment
from abkit.logging_config import get_logger

log = get_logger("abkit.jobs")


def _get_experiment_row(name: str):
    from abkit import storage
    from abkit.db.repositories import ExperimentRepo

    exp_row = ExperimentRepo().get_by_name(name)
    if exp_row is None:
        raise storage.StorageError(f"Experiment '{name}' not found")
    return exp_row


# Item A2 (DB bloat package): every table an experiment delete touches,
# directly or via ON DELETE CASCADE (migrations 0001/0009/0014) — see
# migrations/versions/0001_initial.py for assignments/analysis_results,
# 0014_experiment_flow_images.py for that FK. datasets.experiment_id is
# ON DELETE SET NULL (migration 0012), not a delete — no dead tuples from
# THIS operation, so it's deliberately excluded here (cleanup-dev's own list
# below covers it, since cleanup-dev DOES delete dataset rows outright).
_EXPERIMENT_CASCADE_TABLES = [
    "experiments",
    "assignments",
    "analysis_results",
    "experiment_blocks",
    "experiment_tags",
    "experiment_datasets",
    "experiment_access",
    "experiment_flow_images",
]


def _vacuum_experiment_cascade_tables() -> None:
    """Best-effort, never raises — a single experiment delete is a small
    operation on its own, but this is the ONLY place that runs after every
    single one, so it's the natural hook to keep dead-tuple buildup from
    compounding silently across many deletes over time (the root cause of
    the 2+ GB `assignments` bloat that motivated this whole package was
    exactly that: no delete ever triggered a VACUUM, so autovacuum's own
    (much less aggressive, threshold-based) cadence was the only thing
    running — plain VACUUM here is cheap and immediate)."""
    try:
        from abkit.db.maintenance import vacuum_tables

        vacuum_tables(_EXPERIMENT_CASCADE_TABLES)
    except Exception:
        log.error("post_delete_vacuum_failed", exc_info=True)


@contextmanager
def _timed(action: str, **fields: Any):
    """INFO start/finish (с duration_ms) вокруг действия; ERROR с traceback,
    если действие бросило исключение (DOCKER.md §6.1)."""
    start = time.monotonic()
    log.info(f"{action}.start", **fields)
    try:
        yield
    except Exception:
        log.error(f"{action}.failed", exc_info=True, **fields)
        raise
    else:
        duration_ms = int((time.monotonic() - start) * 1000)
        log.info(f"{action}.finish", duration_ms=duration_ms, **fields)


def _audit(
    current_user: CurrentUser | None,
    action: str,
    *,
    object_type: str | None = None,
    object_id: str | None = None,
    object_name: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    from abkit.db.repositories import AuditRepo

    AuditRepo().log(
        action=action,
        user_id=uuid_mod.UUID(current_user.id) if current_user is not None else None,
        user_email=current_user.email if current_user is not None else None,
        object_type=object_type,
        object_id=object_id,
        object_name=object_name,
        details=details,
    )


def run_design(
    current_user: CurrentUser, config: DesignConfig, data: pd.DataFrame, **kwargs: Any
) -> Experiment:
    """Создавать эксперименты может Editor+ (DOCKER.md §4.1)."""
    require_role(current_user, "editor")
    with _timed("design", user=current_user.email, experiment=config.name, n_rows=len(data)):
        experiment = Experiment.design(config, data, owner_id=current_user.id, **kwargs)
    exp_row = _get_experiment_row(config.name)
    _audit(
        current_user, "experiment.create",
        object_type="experiment", object_id=str(exp_row.id), object_name=config.name,
    )
    return experiment


def preview_sample_size(
    current_user: CurrentUser,
    data: pd.DataFrame,
    *,
    unit_col: str,
    group_names: list[str],
    metrics: list[MetricConfig],
    alpha: float,
    power_: float,
    mde: float | None,
    isolation_mode: str,
    exclude_experiments: Any,
    isolation_selected_experiments: list[str],
    experiment_name: str | None,
) -> dict[str, Any]:
    """Design wizard, sample-size-first flow (CLAUDE.md item 3): 'Calculate
    sample size' — no experiment is created, just isolation (real, against
    other active experiments) + per-metric power calc against the given
    dataset, with an EQUAL split across group_names standing in for the
    real (not yet decided) proportions — exact for the equal-default
    proportions shown right after, since an equal split always gives the
    power formulas a treatment/control ratio of 1 regardless of how many
    groups there are. Editor+ (same bar as run_design — this reads real
    data and other experiments' assignments, not a public computation)."""
    require_role(current_user, "editor")
    from abkit import storage
    from abkit.design import isolation as isolation_mod
    from abkit.experiment import compute_power_results, infer_control_name
    from abkit.experiment_store import get_experiment_store

    if len(group_names) < 2:
        raise storage.StorageError("At least two group names are required")

    experiments_dir = storage.get_experiments_dir()
    store = get_experiment_store(experiments_dir)
    isolation_store = store if hasattr(store, "occupied_units") else None
    isolation_result = isolation_mod.apply_isolation(
        data=data,
        unit_col=unit_col,
        experiments_dir=experiments_dir,
        mode=isolation_mode,
        exclude_experiments=exclude_experiments,
        current_experiment_name=experiment_name,
        store=isolation_store,
        selected_experiments=isolation_selected_experiments,
    )
    eligible_n = isolation_result.n_available

    if mde is None or eligible_n == 0 or not metrics:
        return {"eligible_n": eligible_n, "required_n_per_group": None, "per_metric": []}

    equal_prop = 1.0 / len(group_names)
    groups = {name: equal_prop for name in group_names}
    control_name = infer_control_name(groups)
    config = DesignConfig(
        name=experiment_name or "__preview__",
        unit_col=unit_col,
        groups=groups,
        metrics=metrics,
        alpha=alpha,
        power=power_,
        mde=mde,
        split_method="simple",
    )
    power_results = compute_power_results(config, isolation_result.candidates, control_name)

    per_metric: list[dict[str, Any]] = []
    primary_ns: list[int] = []
    for metric in metrics:
        pr = power_results.get(metric.name)
        n_req = None
        metric_warnings: list[str] = []
        if pr is not None:
            n_req = math.ceil(pr.sample_size_per_group) if pr.sample_size_per_group is not None else None
            metric_warnings = pr.warnings
            if metric.role == "primary" and n_req is not None:
                primary_ns.append(n_req)
        per_metric.append(
            {
                "metric": metric.name,
                "baseline_mean": pr.baseline_mean if pr is not None else None,
                "required_n_per_group": n_req,
                "warnings": metric_warnings,
            }
        )

    return {
        "eligible_n": eligible_n,
        "required_n_per_group": max(primary_ns) if primary_ns else None,
        "per_metric": per_metric,
    }


def preview_strata_power(
    current_user: CurrentUser,
    data: pd.DataFrame,
    *,
    unit_col: str,
    groups: dict[str, float],
    metrics: list[MetricConfig],
    strata: list[str],
    alpha: float,
    power_: float,
    isolation_mode: str,
    exclude_experiments: Any,
    isolation_selected_experiments: list[str],
    experiment_name: str | None,
    categorical_columns: list[str] | None = None,
) -> dict[str, Any]:
    """Item 2 (strata power check, wizard Parameters step): after the user
    has calculated a sample size AND set real group proportions — per
    stratum-dimension (each column alone, plus their combination) achievable
    MDE at those ACTUAL proportions. Editor+ (same bar as
    preview_sample_size — reads real data and other experiments'
    assignments)."""
    require_role(current_user, "editor")
    from abkit import storage
    from abkit.design import isolation as isolation_mod
    from abkit.experiment import compute_power_results, compute_strata_power_rows, infer_control_name
    from abkit.experiment_store import get_experiment_store

    if len(groups) < 2:
        raise storage.StorageError("At least two groups are required")
    if not strata:
        return {"eligible_n": 0, "dimensions": {}}

    experiments_dir = storage.get_experiments_dir()
    store = get_experiment_store(experiments_dir)
    isolation_store = store if hasattr(store, "occupied_units") else None
    isolation_result = isolation_mod.apply_isolation(
        data=data,
        unit_col=unit_col,
        experiments_dir=experiments_dir,
        mode=isolation_mode,
        exclude_experiments=exclude_experiments,
        current_experiment_name=experiment_name,
        store=isolation_store,
        selected_experiments=isolation_selected_experiments,
    )
    eligible_n = isolation_result.n_available
    primary_metrics = [m for m in metrics if m.role == "primary"]
    if eligible_n == 0 or not primary_metrics:
        return {"eligible_n": eligible_n, "dimensions": {}}

    control_name = infer_control_name(groups)
    # Achievable MDE at the CURRENT proportions (mde=None, sample_size=None
    # -> compute_power_results' "achievable at n_control_available" branch)
    # — the baseline every per-stratum MDE is compared against (2x rule).
    overall_config = DesignConfig(
        name=experiment_name or "__strata_power_preview__",
        unit_col=unit_col, groups=groups, metrics=metrics,
        alpha=alpha, power=power_, split_method="simple",
    )
    overall_results = compute_power_results(overall_config, isolation_result.candidates, control_name)
    overall_mde_rel = {
        name: r.mde_rel for name, r in overall_results.items() if r.mde_rel is not None
    }

    dimensions = compute_strata_power_rows(
        isolation_result.candidates, control_name, groups, primary_metrics, strata,
        overall_mde_rel, alpha=alpha, power_target=power_,
        categorical_cols=frozenset(categorical_columns or []),
    )
    return {
        "eligible_n": eligible_n,
        "dimensions": {
            label: [
                {
                    "stratum": r.stratum, "treatment_group": r.treatment_group, "metric": r.metric,
                    "n_control": r.n_control, "n_treatment": r.n_treatment,
                    "mde_rel": r.mde_rel, "mde_rel_cuped": r.mde_rel_cuped, "status": r.status,
                }
                for r in rows
            ]
            for label, rows in dimensions.items()
        },
    }


def run_design_external(
    current_user: CurrentUser, config: DesignConfig, **kwargs: Any
) -> Experiment:
    """External split (item 12) — same Editor+ gate as run_design, no data
    involved: the split happens in an outside system (Firebase A/B Testing
    and similar), ABSet only stores the declared groups/metrics for later
    analysis."""
    require_role(current_user, "editor")
    with _timed("design_external", user=current_user.email, experiment=config.name):
        experiment = Experiment.design_external(config, owner_id=current_user.id, **kwargs)
    exp_row = _get_experiment_row(config.name)
    _audit(
        current_user, "experiment.create",
        object_type="experiment", object_id=str(exp_row.id), object_name=config.name,
        details={"split_source": "external"},
    )
    return experiment


def run_redesign(
    current_user: CurrentUser, config: DesignConfig, data: pd.DataFrame, **kwargs: Any
) -> Experiment:
    """Redesign (5-part package pt.3) — owner/access-editor/admin only (a
    stricter gate than run_design's "any editor", since this mutates an
    EXISTING experiment rather than creating a new one — same policy as
    run_update_status/run_rename_experiment). Only while status=='designed'
    (pt.3.4): once running, the old split has already produced observations
    that a new split would silently invalidate — archive + new experiment
    is the only path from there. Replaces assignments/config in place (same
    row, config.name is the target) and drops analysis_results run against
    the discarded split (pt.3.3) — they describe a randomization that no
    longer exists."""
    from abkit.db.repositories import ResultRepo

    exp_row = _get_experiment_row(config.name)
    require_experiment_edit_access(current_user, exp_row)
    if exp_row.status != "designed":
        raise AuthError("Only experiments in 'designed' status can be redesigned")

    old_config = exp_row.config
    deleted_results = ResultRepo().count_for_experiment(exp_row.id)

    with _timed("redesign", user=current_user.email, experiment=config.name, n_rows=len(data)):
        experiment = Experiment.design(
            config, data, owner_id=current_user.id, is_redesign=True, **kwargs
        )
        ResultRepo().delete_for_experiment(exp_row.id)

    _audit(
        current_user, "experiment.redesign",
        object_type="experiment", object_id=str(exp_row.id), object_name=config.name,
        details={
            "config_before": old_config,
            "config_after": config.model_dump(mode="json"),
            "deleted_analysis_results": deleted_results,
        },
    )
    return experiment


def run_analyze(
    current_user: CurrentUser, experiment: Experiment, data: pd.DataFrame, **kwargs: Any
) -> AnalysisResults:
    """Запускать Analyze может Editor+ — на любом эксперименте, не только своем
    (DOCKER.md §4.1: у этого права нет разделения "свои/чужие", в отличие от
    смены статуса)."""
    require_role(current_user, "editor")
    with _timed("analyze", user=current_user.email, experiment=experiment.name, n_rows=len(data)):
        results = experiment.analyze(data, **kwargs)
    _audit(current_user, "analysis.run", object_type="experiment", object_name=experiment.name)
    return results


def run_validate_aa(
    current_user: CurrentUser, data: pd.DataFrame, config: DesignConfig,
    *, dataset_id: str | None = None, **kwargs: Any,
):
    """dataset_id — какие данные валидировались (UX package, Validation п.C.5:
    "зафиксировать dataset_id, на котором она гонялась"), только для
    audit_log — не влияет на симуляцию."""
    require_role(current_user, "editor")
    from abkit.validation.simulation import run_aa

    with _timed("validate_aa", user=current_user.email, experiment=config.name):
        report = run_aa(data, config, **kwargs)
    _audit(
        current_user, "validation.run",
        object_type="experiment", object_name=config.name,
        details={"kind": "aa", "dataset_id": dataset_id},
    )
    return report


def run_validate_ab(
    current_user: CurrentUser, data: pd.DataFrame, config: DesignConfig,
    *, dataset_id: str | None = None, **kwargs: Any,
):
    require_role(current_user, "editor")
    from abkit.validation.simulation import run_ab

    with _timed("validate_ab", user=current_user.email, experiment=config.name):
        report = run_ab(data, config, **kwargs)
    _audit(
        current_user, "validation.run",
        object_type="experiment", object_name=config.name,
        details={"kind": "ab", "dataset_id": dataset_id},
    )
    return report


def run_update_status(current_user: CurrentUser, name: str, new_status: str) -> None:
    """Менять статус может owner, editor из experiment_access или Admin —
    CLAUDE.md "Permissions model"."""
    from abkit.db.repositories import ExperimentRepo

    exp_row = _get_experiment_row(name)
    require_experiment_edit_access(current_user, exp_row)
    old_status = exp_row.status
    with _timed("update_status", user=current_user.email, experiment=name, new_status=new_status):
        ExperimentRepo().update_status(name, new_status)
    _audit(
        current_user, "experiment.status_change",
        object_type="experiment", object_id=str(exp_row.id), object_name=name,
        details={"from": old_status, "to": new_status},
    )


def run_set_publication_status(current_user: CurrentUser, name: str, publication_status: str) -> None:
    """draft<->published — то же право, что смена операционного статуса
    (owner/access-editor/admin), обе стороны переключения аудируются
    (FRONTEND.md §3.3: "оба направления в audit_log")."""
    from abkit.db.repositories import ExperimentRepo

    exp_row = _get_experiment_row(name)
    require_experiment_edit_access(current_user, exp_row)
    old_status = exp_row.publication_status
    with _timed(
        "set_publication_status", user=current_user.email, experiment=name,
        publication_status=publication_status,
    ):
        ExperimentRepo().update_publication_status(name, publication_status)
    _audit(
        current_user, "experiment.publication_status_change",
        object_type="experiment", object_id=str(exp_row.id), object_name=name,
        details={"from": old_status, "to": publication_status},
    )


def run_rename_experiment(current_user: CurrentUser, name: str, new_name: str) -> None:
    """Переименование — та же политика, что у смены статуса (owner/access-editor/
    admin); артефактная директория переименовывается вместе со строкой БД,
    чтобы experiment.path продолжал резолвиться по новому имени."""
    import shutil

    from abkit.db.repositories import ExperimentRepo
    from abkit.db.store import DbExperimentStore

    exp_row = _get_experiment_row(name)
    require_experiment_edit_access(current_user, exp_row)
    with _timed("rename_experiment", user=current_user.email, experiment=name, new_name=new_name):
        ExperimentRepo().rename(name, new_name)
        store = DbExperimentStore()
        old_dir = store.data_dir / name
        if old_dir.exists():
            shutil.move(str(old_dir), str(store.data_dir / new_name))
    _audit(
        current_user, "experiment.rename",
        object_type="experiment", object_id=str(exp_row.id), object_name=new_name,
        details={"from": name, "to": new_name},
    )


def get_experiment_deletion_summary(current_user: CurrentUser, name: str) -> dict[str, int]:
    """Только для UI-подтверждения удаления — сколько строк реально удалится
    каскадом, без самого удаления. Тот же guard, что и у самого удаления
    (require_experiment_edit_access) — точные числа по чужому эксперименту
    тоже не должен видеть посторонний Editor/Viewer."""
    from abkit.db.repositories import AssignmentRepo, DatasetRepo, ResultRepo

    exp_row = _get_experiment_row(name)
    require_experiment_edit_access(current_user, exp_row)
    return {
        "assignments": AssignmentRepo().count_for_experiment(exp_row.id),
        "datasets": DatasetRepo().count_for_experiment(exp_row.id),
        "results": ResultRepo().count_for_experiment(exp_row.id),
    }


def run_delete_experiment(current_user: CurrentUser, name: str) -> None:
    """Удалять эксперимент может owner, editor из experiment_access, или Admin
    (require_experiment_edit_access — та же политика, что у смены статуса).
    Раньше было Admin-only без исключений; изменено по явному запросу
    пользователя (UX-правка, чтобы Editor мог убирать за собой свои же
    эксперименты), затем расширено на experiment_access (UX-пакет).

    Датасеты — самостоятельные сущности (CLAUDE.md, "правило") — НЕ
    удаляются вместе с экспериментом: `datasets.experiment_id` теперь
    ON DELETE SET NULL (миграция 0012, было CASCADE), так что их файлы на
    диске трогать здесь нельзя (была ошибка ровно в эту сторону — снимался
    файл, хотя строка датасета переживала бы правильный каскад). Только
    experiment_datasets (линк-таблица использования) каскадно чистится —
    это связь, не сам датасет."""
    exp_row = _get_experiment_row(name)
    require_experiment_edit_access(current_user, exp_row)

    import shutil

    from abkit.db.repositories import ExperimentRepo

    # счетчики нужны ДО удаления (после — каскад их уже снес) — только для
    # audit_log details, чтобы в журнале было видно, сколько именно данных
    # было удалено вместе с экспериментом.
    deletion_summary = get_experiment_deletion_summary(current_user, name)

    from abkit.db.store import DbExperimentStore

    exp_id = str(exp_row.id)
    with _timed("delete_experiment", user=current_user.email, experiment=name):
        ExperimentRepo().delete(name)
        artifact_dir = DbExperimentStore().data_dir / name
        if artifact_dir.exists():
            shutil.rmtree(artifact_dir)
    _audit(
        current_user, "experiment.delete", object_type="experiment", object_id=exp_id, object_name=name,
        details=deletion_summary,
    )
    _vacuum_experiment_cascade_tables()


class DatasetInUseError(Exception):
    """Raised by run_delete_dataset when the dataset is used by at least one
    experiment and the caller didn't pass confirm="DELETE" — UX package,
    Datasets п.2.2: unused datasets get a plain confirm Modal, used ones get
    a strict Modal listing the experiments and requiring typed DELETE."""

    def __init__(self, experiment_names: list[str]) -> None:
        self.experiment_names = experiment_names
        super().__init__(f"Dataset is used by experiments: {', '.join(experiment_names)}")


def _experiment_names_using_dataset(ds) -> list[str]:
    from abkit.db.repositories import ExperimentDatasetRepo, ExperimentRepo

    exp_ids = set(ExperimentDatasetRepo().experiments_using_dataset(ds.id))
    if ds.experiment_id:
        exp_ids.add(ds.experiment_id)
    if not exp_ids:
        return []
    experiments = ExperimentRepo().list_all()
    return sorted(e.name for e in experiments if e.id in exp_ids)


def get_dataset_usage(current_user: CurrentUser, dataset_id: str) -> list[str]:
    """Which experiments use this dataset (via experiment_datasets, the
    DB3 "actual use" link table, OR the legacy single-owner experiment_id) —
    read-only, drives which Delete confirmation Modal the frontend shows."""
    require_role(current_user, "viewer")
    from abkit import storage
    from abkit.db.repositories import DatasetRepo

    ds = DatasetRepo().get_by_id(uuid_mod.UUID(dataset_id))
    if ds is None:
        raise storage.StorageError(f"Dataset '{dataset_id}' not found")
    return _experiment_names_using_dataset(ds)


def run_delete_dataset(current_user: CurrentUser, dataset_id: str, confirm: str | None = None) -> None:
    """Owner (uploaded_by) or Admin — datasets have no owner_id-based access
    grant system like experiments do, just a single uploader. Deleting a
    dataset that's in use requires confirm="DELETE" (checked here, not just
    in the router, so direct service-function calls stay safe too — DOCKER.md
    §12's "Viewer can't mutate even via direct function call" discipline,
    extended to this confirmation requirement). Deleting a dataset does NOT
    break existing analysis results referencing it (analysis_results.dataset_id
    is ON DELETE SET NULL, migration 0009) — results.json is self-sufficient,
    results stay renderable, only the live "which dataset" lookup goes null."""
    require_role(current_user, "viewer")
    from pathlib import Path

    from abkit import storage
    from abkit.db.repositories import DatasetRepo

    ds = DatasetRepo().get_by_id(uuid_mod.UUID(dataset_id))
    if ds is None:
        raise storage.StorageError(f"Dataset '{dataset_id}' not found")

    is_owner = ds.uploaded_by is not None and str(ds.uploaded_by) == str(current_user.id)
    if current_user.role != "admin" and not is_owner:
        raise AuthError("You can only delete your own datasets (or contact an Admin)")

    experiment_names = _experiment_names_using_dataset(ds)
    if experiment_names and confirm != "DELETE":
        raise DatasetInUseError(experiment_names)

    with _timed("delete_dataset", user=current_user.email, dataset=ds.filename):
        DatasetRepo().delete(ds.id)
        path = Path(ds.storage_path)
        if path.exists():
            path.unlink()
    _audit(
        current_user, "dataset.delete", object_type="dataset", object_id=str(ds.id), object_name=ds.filename,
        details={"used_by_experiments": experiment_names} if experiment_names else None,
    )


def run_update_dataset(
    current_user: CurrentUser,
    dataset_id: str,
    *,
    name: str | None = None,
    connection_id: str | None = None,
    sql_text: str | None = None,
    source_schema: str | None = None,
    source_table: str | None = None,
    column_renames: dict[str, str] | None = None,
    categorical_columns: list[str] | None = None,
) -> dict[str, Any]:
    """PATCH /datasets/{id} (UX package, Datasets п.2.3) — owner or admin,
    same rule as delete. `name` (-> Dataset.filename) applies to any source.
    `connection_id`/`sql_text` only apply to source='sql' — if either
    actually changes, the DB row is updated here (synchronously) but the
    re-fetch itself is NOT run here: the caller (router) submits it as a
    background job reusing run_refresh_sql_dataset, which reads sql_text/
    connection_id fresh off the row — already the new values by the time it
    runs, so there's no separate "apply edited SQL" code path to keep in
    sync. Returns {"needs_refetch": bool} so the router knows whether to
    submit that job.

    source_schema/source_table (Datasets follow-up: persist source schema/
    table) travel ONLY alongside a sql_text change, and only when the caller
    (EditDatasetModal) has confirmed the current SQL box still exactly
    matches what that schema/table selection would generate — otherwise it
    omits them, which this function treats as "clear them", not "leave
    unchanged": a stale source_schema/source_table would be a lie about
    where the (now hand-edited) query actually comes from.

    column_renames (item 1, upload rename confirmation): {old_name:
    new_name} for source='upload' only — applied synchronously here (not a
    background job, matching upload_dataset() itself, which is also
    synchronous) by re-reading the CSV, renaming columns, and
    re-materializing to parquet (upload_dataset's original file stays a raw
    CSV; a rename re-lands the data as parquet, same storage format DB2's
    SQL datasets already use — see abkit/dataset_files.py's extension
    dispatch, which then just works unchanged). renamed_columns on the row
    is {new_name: ORIGINAL_name} — resolved transitively through any prior
    rename, so renaming twice (a -> b, then b -> c) still records
    {c: a}, not {c: b}."""
    require_role(current_user, "viewer")
    from abkit import storage
    from abkit.db.repositories import DatasetRepo

    ds = DatasetRepo().get_by_id(uuid_mod.UUID(dataset_id))
    if ds is None:
        raise storage.StorageError(f"Dataset '{dataset_id}' not found")

    is_owner = ds.uploaded_by is not None and str(ds.uploaded_by) == str(current_user.id)
    if current_user.role != "admin" and not is_owner:
        raise AuthError("You can only edit your own datasets (or contact an Admin)")

    changes: dict[str, Any] = {}
    needs_refetch = False

    if name is not None and name.strip() and name.strip() != ds.filename:
        DatasetRepo().rename(ds.id, name.strip())
        changes["filename"] = {"old": ds.filename, "new": name.strip()}

    if connection_id is not None or sql_text is not None:
        if ds.source != "sql":
            raise storage.StorageError(f"Dataset '{ds.filename}' was not created from SQL — cannot edit connection/SQL")
        new_connection_id = uuid_mod.UUID(connection_id) if connection_id else ds.connection_id
        new_sql = sql_text if sql_text is not None else ds.sql_text
        if new_connection_id != ds.connection_id or new_sql != ds.sql_text:
            DatasetRepo().update_sql_source(
                ds.id, connection_id=new_connection_id, sql_text=new_sql,
                source_schema=source_schema, source_table=source_table,
            )
            changes["sql_text"] = {"old": ds.sql_text, "new": new_sql}
            needs_refetch = True

    if column_renames:
        if ds.source != "upload":
            raise storage.StorageError(
                f"Dataset '{ds.filename}' was not created via file upload — column renaming only applies to uploads"
            )
        import re
        from pathlib import Path

        from abkit.dataset_files import read_dataset_file

        current_columns = list(ds.columns)
        unknown = [c for c in column_renames if c not in current_columns]
        if unknown:
            raise storage.StorageError(f"Unknown column(s) to rename: {', '.join(unknown)}")

        old_to_new = {c: column_renames.get(c, c).strip() for c in current_columns}
        for new in old_to_new.values():
            if not new:
                raise storage.StorageError("Column names cannot be empty")
            if re.search(r"[,\"'\\\n\r\t]", new):
                raise storage.StorageError(
                    f"Column name '{new}' contains a character that isn't allowed (, \" ' \\ or a newline/tab)"
                )
        new_names = list(old_to_new.values())
        if len(set(new_names)) != len(new_names):
            raise storage.StorageError("Column names must be unique")

        if new_names != current_columns:
            data = read_dataset_file(ds.storage_path)
            data = data.rename(columns=old_to_new)

            dest_path = Path(ds.storage_path).with_suffix(".parquet")
            tmp_path = dest_path.with_name(dest_path.name + ".tmp")
            data.to_parquet(tmp_path, index=False)
            tmp_path.replace(dest_path)
            old_path = Path(ds.storage_path)
            if old_path != dest_path and old_path.exists():
                old_path.unlink()

            # Resolve transitively through any prior rename, so a second
            # rename (b -> c, after an earlier a -> b) still records the
            # true original {c: a}, not the intermediate {c: b}.
            prior = dict(ds.renamed_columns or {})
            merged: dict[str, str] = {}
            for old, new in old_to_new.items():
                original = prior.get(old, old)
                if new != original:
                    merged[new] = original

            DatasetRepo().apply_column_renames(
                ds.id, columns=new_names, renamed_columns=merged or None,
                storage_path=str(dest_path), n_rows=len(data),
                sha256=DatasetRepo.compute_sha256(data),
            )
            changes["columns"] = {"old": current_columns, "new": new_names}

    # Part 2: persist the user's categorical choices (after any rename, so the
    # list is in the new column names). For a source='sql' edit that triggers a
    # re-fetch, this becomes the "previous" flags the refresh reconcile keeps —
    # so a just-flagged surviving column stays flagged.
    if categorical_columns is not None:
        DatasetRepo().set_categorical_columns(ds.id, categorical_columns)
        changes["categorical_columns"] = {"old": ds.categorical_columns, "new": categorical_columns}

    if changes:
        _audit(
            current_user, "dataset.update", object_type="dataset", object_id=str(ds.id),
            object_name=ds.filename, details=changes,
        )
    return {"needs_refetch": needs_refetch}


def run_update_experiment_properties(
    current_user: CurrentUser,
    name: str,
    *,
    new_name: str,
    owner_ids: list[str],
    editor_ids: list[str],
    visible_roles: list[str] | None,
) -> None:
    """Edit Properties modal (UX package, like Superset's dashboard Properties):
    name, additional owners/editors (experiment_access), visible_roles. Same
    edit-access gate as other owner-gated actions — see CLAUDE.md 'Permissions
    model'. owner_ids/editor_ids always carry the FULL desired list (the modal
    replaces, not appends); the original owner_id is implicit and never stored
    in experiment_access even if present in owner_ids."""
    from abkit.db.repositories import ExperimentAccessRepo, ExperimentRepo, UserRepo

    exp_row = _get_experiment_row(name)
    require_experiment_edit_access(current_user, exp_row)

    user_repo = UserRepo()

    def _emails(user_ids: set[uuid_mod.UUID]) -> list[str]:
        emails = []
        for uid in user_ids:
            user = user_repo.get_by_id(uid)
            emails.append(user.email if user else str(uid))
        return sorted(emails)

    old_grants = ExperimentAccessRepo().list_for_experiment(exp_row.id)
    old_owner_emails = _emails({g.user_id for g in old_grants if g.access == "owner"})
    old_editor_emails = _emails({g.user_id for g in old_grants if g.access == "editor"})
    old_visible_roles = exp_row.visible_roles

    current_name = name
    if new_name != name:
        run_rename_experiment(current_user, name, new_name)
        exp_row = _get_experiment_row(new_name)
        current_name = new_name

    grants: list[tuple[uuid_mod.UUID, str]] = []
    owner_uuids: set[uuid_mod.UUID] = set()
    for uid in owner_ids:
        parsed = uuid_mod.UUID(uid)
        if parsed == exp_row.owner_id:
            continue
        owner_uuids.add(parsed)
        grants.append((parsed, "owner"))
    for uid in editor_ids:
        parsed = uuid_mod.UUID(uid)
        if parsed == exp_row.owner_id or parsed in owner_uuids:
            continue
        grants.append((parsed, "editor"))

    with _timed("update_experiment_properties", user=current_user.email, experiment=current_name):
        ExperimentAccessRepo().set_for_experiment(exp_row.id, grants)
        ExperimentRepo().update_visible_roles(current_name, visible_roles)

    new_owner_emails = _emails(owner_uuids)
    new_editor_emails = _emails({u for u, access in grants if access == "editor"})
    details: dict[str, Any] = {}
    if old_owner_emails != new_owner_emails:
        details["owners"] = {"from": old_owner_emails, "to": new_owner_emails}
    if old_editor_emails != new_editor_emails:
        details["editors"] = {"from": old_editor_emails, "to": new_editor_emails}
    if old_visible_roles != visible_roles:
        details["visible_roles"] = {"from": old_visible_roles, "to": visible_roles}

    _audit(
        current_user, "experiment.properties_change",
        object_type="experiment", object_id=str(exp_row.id), object_name=current_name,
        details=details or None,
    )


def run_update_experiment_blocks(
    current_user: CurrentUser, name: str, blocks: list[dict[str, Any]],
) -> list:
    """PUT /experiments/{name}/blocks (Hypothesis/Conclusions/Decision, plus
    any custom blocks) — same edit-access gate as rename/properties. Blocks
    used to be intentionally left out of audit_log (see the now-stale
    comment this replaced in backend/routers/experiments.py — 'блоки НЕ
    аудируются отдельно'); item 4 of the audit-details package asks for at
    least the block kind, so a thin entry is written listing which kinds
    actually changed content (title/content_md), not full diffs — block text
    can be long-form markdown, unlike the short from/to pairs used elsewhere."""
    from abkit.db.repositories import BlockRepo

    exp_row = _get_experiment_row(name)
    require_experiment_edit_access(current_user, exp_row)

    old_by_id = {b.id: b for b in BlockRepo().list_for_experiment(exp_row.id)}
    result = BlockRepo().upsert_many(
        exp_row.id, blocks, updated_by=uuid_mod.UUID(current_user.id)
    )

    changed_kinds: set[str] = set()
    for new_block in result:
        old_block = old_by_id.get(new_block.id)
        if old_block is None:
            changed_kinds.add(new_block.kind)
        elif old_block.title != new_block.title or old_block.content_md != new_block.content_md:
            changed_kinds.add(new_block.kind)

    if changed_kinds:
        _audit(
            current_user, "experiment.blocks_change",
            object_type="experiment", object_id=str(exp_row.id), object_name=name,
            details={"kinds": sorted(changed_kinds)},
        )
    return result


def _connection_spec(conn_row):
    from abkit.db_connections.crypto import decrypt_password
    from abkit.db_connections.engines import ConnectionSpec

    return ConnectionSpec(
        engine=conn_row.engine, host=conn_row.host, port=conn_row.port, database=conn_row.database,
        username=conn_row.username, password=decrypt_password(conn_row.password_encrypted),
        ssl=conn_row.ssl, extra_params=conn_row.extra_params,
    )


def run_create_dataset_from_sql(
    current_user: CurrentUser,
    *,
    connection_id: str,
    sql: str,
    name: str,
    kind: str,
    experiment_id: str | None = None,
    source_schema: str | None = None,
    source_table: str | None = None,
    categorical_columns: list[str] | None = None,
    progress_callback: Any = None,
) -> dict[str, Any]:
    """POST /datasets/from-sql (DB2, CLAUDE.md dataset-from-SQL feature) —
    materializes a SELECT query result to parquet (streamed, see
    abkit.db_connections.sql_dataset) and registers it as a normal dataset,
    source='sql'. Editor+, same right as an uploaded dataset.

    source_schema/source_table (Datasets follow-up: persist source schema/
    table) are recorded as-is — the caller (CreateDatasetModal) only sends
    them when `sql` still exactly matches what that schema/table selection
    generates, so by the time they get here they're already trustworthy;
    None just means "no cascade selection was used (or it no longer
    applies)"."""
    require_role(current_user, "editor")
    from datetime import datetime, timezone

    from abkit import storage
    from abkit.db.repositories import DatabaseConnectionRepo, DatasetRepo
    from abkit.db.store import DbExperimentStore
    from abkit.db_connections.sql_dataset import execute_select_to_parquet

    conn_row = DatabaseConnectionRepo().get_by_id(uuid_mod.UUID(connection_id))
    if conn_row is None:
        raise storage.StorageError(f"Database connection '{connection_id}' not found")

    store = DbExperimentStore()
    dest_path = store.data_dir / "_uploads" / f"{uuid_mod.uuid4().hex}_{name}.parquet"

    def _progress(n_rows: int) -> None:
        if progress_callback is not None:
            progress_callback(f"Fetched {n_rows} rows...")

    with _timed(
        "create_dataset_from_sql", user=current_user.email, connection=conn_row.display_name,
    ):
        result = execute_select_to_parquet(
            _connection_spec(conn_row), sql, dest_path, progress_callback=_progress
        )
        sha256 = DatasetRepo.compute_sha256_from_file(str(dest_path))
        exp_uuid = uuid_mod.UUID(experiment_id) if experiment_id else None
        # Part 2: use the user's explicit list if given, else the heuristic
        # default computed from the just-fetched parquet.
        if categorical_columns is None:
            import pandas as pd

            from abkit.dataset_categorical import default_categorical_columns

            categorical_columns = default_categorical_columns(pd.read_parquet(dest_path))
        dataset_id = DatasetRepo().create(
            kind=kind, filename=f"{name}.parquet", n_rows=result.n_rows, columns=result.columns,
            storage_path=str(dest_path), sha256=sha256, experiment_id=exp_uuid,
            uploaded_by=uuid_mod.UUID(current_user.id), source="sql", connection_id=conn_row.id,
            sql_text=sql, fetched_at=datetime.now(timezone.utc),
            source_schema=source_schema, source_table=source_table,
            categorical_columns=categorical_columns,
        )
    _audit(
        current_user, "dataset.create_from_sql",
        object_type="dataset", object_id=str(dataset_id), object_name=name,
        details={
            "connection": conn_row.display_name, "n_rows": result.n_rows, "truncated": result.truncated,
        },
    )
    return {"dataset_id": str(dataset_id), "n_rows": result.n_rows, "truncated": result.truncated}


def run_refresh_sql_dataset(
    current_user: CurrentUser, dataset_id: str, progress_callback: Any = None,
) -> dict[str, Any]:
    """POST /datasets/{id}/refresh (DB2) — re-runs the dataset's stored
    sql_text against its connection. Editor+ (same right as creating a
    dataset). Fetches into a fresh temp file first and only swaps it into
    the live storage_path once the fetch fully succeeds — a mid-stream
    failure (connection dropped, source table gone partway through) must
    leave the existing snapshot untouched (UX package, Datasets п.1.4), not
    a truncated/partial parquet from writing in place."""
    require_role(current_user, "editor")
    import uuid as _uuid
    from pathlib import Path

    from abkit import storage
    from abkit.db.repositories import DatabaseConnectionRepo, DatasetRepo
    from abkit.db_connections.sql_dataset import execute_select_to_parquet

    ds = DatasetRepo().get_by_id(uuid_mod.UUID(dataset_id))
    if ds is None:
        raise storage.StorageError(f"Dataset '{dataset_id}' not found")
    if ds.source != "sql" or not ds.sql_text or ds.connection_id is None:
        raise storage.StorageError(f"Dataset '{ds.filename}' was not created from SQL — cannot refresh")

    conn_row = DatabaseConnectionRepo().get_by_id(ds.connection_id)
    if conn_row is None:
        raise storage.StorageError("The database connection for this dataset no longer exists")

    def _progress(n_rows: int) -> None:
        if progress_callback is not None:
            progress_callback(f"Fetched {n_rows} rows...")

    live_path = Path(ds.storage_path)
    tmp_path = live_path.with_name(f".refresh_{_uuid.uuid4().hex}_{live_path.name}")

    with _timed("refresh_sql_dataset", user=current_user.email, dataset=ds.filename):
        try:
            result = execute_select_to_parquet(
                _connection_spec(conn_row), ds.sql_text, tmp_path, progress_callback=_progress
            )
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        tmp_path.replace(live_path)
        sha256 = DatasetRepo.compute_sha256_from_file(ds.storage_path)
        # Part 2: reconcile categorical flags — surviving columns keep the
        # user's flag, new columns get the heuristic, vanished columns drop out.
        import pandas as pd

        from abkit.dataset_categorical import reconcile_categorical_columns

        reconciled = reconcile_categorical_columns(
            ds.columns, ds.categorical_columns, pd.read_parquet(ds.storage_path)
        )
        DatasetRepo().update_after_refresh(
            ds.id, n_rows=result.n_rows, columns=result.columns, sha256=sha256,
            categorical_columns=reconciled,
        )
    _audit(
        current_user, "dataset.refresh",
        object_type="dataset", object_id=str(ds.id), object_name=ds.filename,
        details={"n_rows": result.n_rows, "truncated": result.truncated},
    )
    return {"dataset_id": str(ds.id), "n_rows": result.n_rows, "truncated": result.truncated}


# Two accounts every Playwright e2e spec's loginViaUi()/helpers.ts hardcodes
# as the credentials to log in with — must survive any cleanup sweep, or
# every e2e run breaks immediately at login. Everything these accounts
# CREATE (experiments/datasets/connections) is still swept — only the
# accounts themselves are protected.
_PROTECTED_E2E_FIXTURE_EMAILS = {"admin@e2e.test", "viewer@e2e.test"}


def run_cleanup_dev(*, dry_run: bool = False, min_age_hours: int = 1) -> dict[str, list[str]]:
    """`abkit-admin cleanup-dev` (CLAUDE.md, "Правило: гигиена dev-артефактов")
    — a trusted CLI-only sweep, same "no HTTP/CurrentUser context" pattern as
    create-admin/import-legacy: deletes/deactivates things that shouldn't be
    sitting on a shared stack.

    Root cause this exists for: local Playwright runs against the live
    docker-compose stack (instead of a one-shot environment — see
    playwright.config.ts) left 173 experiments / 247 datasets / 10 connections
    / 73 stray user accounts behind across a handful of sessions before this
    was caught. Fixing the e2e isolation (playwright.config.ts) prevents new
    debris from THAT source going forward; this command is the safety net for
    everything else (manual debugging on the live stack, or e2e isolation
    regressing again unnoticed) — meant to run automatically at the end of
    every work package (CLAUDE.md), not just on demand.

    Matches, for experiments/datasets/connections: name/filename/display_name
    starts with `_dev_` (any age — the whole point of that prefix is "safe to
    remove"), OR the owning/uploading/creating user's email ends with
    `@e2e.test` AND the row is older than `min_age_hours` (age guard: don't
    delete something from an e2e run that might still be mid-flight). For
    users: any `%@e2e.test` account older than `min_age_hours`, except the two
    protected fixtures above — deactivated (`set_active(False)`), not deleted:
    there is no user-delete function, same constraint as the Admin Users page.

    Never touches anything owned by an email outside `@e2e.test` and not
    literally prefixed `_dev_` — real user data (e.g. admin@abkit.local's own
    experiments/datasets) is invisible to this sweep by construction, not by
    an exclusion list.

    Datasets are swept BEFORE experiments deliberately: `datasets.experiment_id`
    is `ON DELETE CASCADE` from experiments, so deleting an experiment first
    would silently cascade away any dataset still linked through that legacy
    field WITHOUT going through this function's own dataset loop — meaning its
    storage file would never get unlinked, leaking an orphaned parquet/csv on
    every run. Sweeping datasets first means each one is always removed
    through DatasetRepo().delete() + an explicit unlink, never via cascade.
    """
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    from abkit.db.repositories import DatabaseConnectionRepo, DatasetRepo, ExperimentRepo, UserRepo
    from abkit.db.store import DbExperimentStore

    cutoff = datetime.now(timezone.utc) - timedelta(hours=min_age_hours)
    users = UserRepo().list_all()
    email_by_id = {u.id: u.email for u in users}

    def _matches(name: str, owner_email: str | None, created_at) -> bool:
        if name.startswith("_dev_"):
            return True
        return bool(owner_email) and owner_email.endswith("@e2e.test") and created_at is not None and created_at < cutoff

    removed: dict[str, list[str]] = {"experiments": [], "datasets": [], "connections": [], "users_deactivated": []}

    for ds in DatasetRepo().list_all():
        owner_email = email_by_id.get(ds.uploaded_by) if ds.uploaded_by else None
        if _matches(ds.filename, owner_email, ds.uploaded_at):
            removed["datasets"].append(ds.filename)
            if not dry_run:
                DatasetRepo().delete(ds.id)
                path = Path(ds.storage_path)
                if path.exists():
                    path.unlink()
                _audit(None, "dev_cleanup.dataset_delete", object_type="dataset", object_name=ds.filename)

    for exp in ExperimentRepo().list_all():
        if _matches(exp.name, email_by_id.get(exp.owner_id), exp.created_at):
            removed["experiments"].append(exp.name)
            if not dry_run:
                import shutil

                ExperimentRepo().delete(exp.name)
                artifact_dir = DbExperimentStore().data_dir / exp.name
                if artifact_dir.exists():
                    shutil.rmtree(artifact_dir)
                _audit(None, "dev_cleanup.experiment_delete", object_type="experiment", object_name=exp.name)

    for conn in DatabaseConnectionRepo().list_all():
        owner_email = email_by_id.get(conn.created_by) if conn.created_by else None
        if _matches(conn.display_name, owner_email, conn.created_at):
            removed["connections"].append(conn.display_name)
            if not dry_run:
                DatabaseConnectionRepo().delete(conn.id)
                _audit(None, "dev_cleanup.connection_delete", object_type="database_connection", object_name=conn.display_name)

    for u in users:
        if u.email in _PROTECTED_E2E_FIXTURE_EMAILS or not u.is_active:
            continue
        if u.email.endswith("@e2e.test") and u.created_at is not None and u.created_at < cutoff:
            removed["users_deactivated"].append(u.email)
            if not dry_run:
                UserRepo().set_active(u.id, False)
                _audit(None, "dev_cleanup.user_deactivate", object_type="user", object_name=u.email)

    # Item A2 (DB bloat package): a real cleanup-dev run is exactly the kind
    # of bulk-delete that built up the 2+ GB assignments bloat this package
    # exists to fix — VACUUM whatever it actually touched, not just leave it
    # to autovacuum's own (much less aggressive) schedule.
    if not dry_run and any(removed.values()):
        try:
            from abkit.db.maintenance import vacuum_tables

            vacuum_tables(["datasets", "database_connections", "users", *_EXPERIMENT_CASCADE_TABLES])
        except Exception:
            log.error("post_cleanup_dev_vacuum_failed", exc_info=True)

    return removed


def run_create_tag(current_user: CurrentUser, name: str) -> Any:
    """POST /tags — "create on the fly" from the Edit Properties modal's Tags
    field (Select mode="tags"). Editor+ (same bar as creating other
    entities); TagRepo.get_or_create() means typing a name that already
    exists (case-insensitively — CITEXT) reuses it instead of erroring, so
    the UI never has to reconcile "create" vs "select existing" itself."""
    require_role(current_user, "editor")
    from abkit import storage
    from abkit.db.repositories import TagRepo

    name = name.strip()
    if not name:
        raise storage.StorageError("Tag name cannot be empty")
    tag = TagRepo().get_or_create(name, created_by=uuid_mod.UUID(current_user.id))
    _audit(current_user, "tag.create", object_type="tag", object_id=str(tag.id), object_name=tag.name)
    return tag


def search_tags(current_user: CurrentUser, q: str | None) -> Any:
    """GET /tags?q= — typeahead, used both by the Properties modal's Tags
    field and the experiments list's tag filter. Viewer+ (read-only,
    everyone who can see the list should be able to filter it by tag)."""
    require_role(current_user, "viewer")
    from abkit.db.repositories import TagRepo

    return TagRepo().search(q)


def run_set_experiment_tags(current_user: CurrentUser, name: str, tag_ids: list[str]) -> Any:
    """PUT /experiments/{name}/tags — same edit-access gate as renaming or
    editing the Hypothesis/Conclusions/Decision blocks (owner/access-editor/
    Admin, CLAUDE.md "Permissions model"). Always a full replace — the
    caller sends the complete desired tag list, not a delta."""
    from abkit.db.repositories import ExperimentTagRepo

    exp_row = _get_experiment_row(name)
    require_experiment_edit_access(current_user, exp_row)

    old_names = {t.name for t in ExperimentTagRepo().list_for_experiment(exp_row.id)}
    parsed_ids = [uuid_mod.UUID(t) for t in tag_ids]
    ExperimentTagRepo().set_for_experiment(exp_row.id, parsed_ids)
    tags = ExperimentTagRepo().list_for_experiment(exp_row.id)
    new_names = {t.name for t in tags}
    _audit(
        current_user, "experiment.tags_change", object_type="experiment", object_id=str(exp_row.id),
        object_name=name,
        details={
            "added": sorted(new_names - old_names),
            "removed": sorted(old_names - new_names),
        },
    )
    return tags


def get_tag_usage_count(current_user: CurrentUser, tag_id: str) -> int:
    """How many experiments currently have this tag — the frontend fetches
    this BEFORE showing the delete-tag confirmation (UX package, Tags
    §3.2), so the count is visible up front rather than discovered after
    confirming."""
    require_role(current_user, "viewer")
    from abkit.db.repositories import ExperimentTagRepo

    return ExperimentTagRepo().count_for_tag(uuid_mod.UUID(tag_id))


def run_delete_tag(current_user: CurrentUser, tag_id: str) -> int:
    """DELETE /tags/{id} — Admin-only (UX package, Tags §3.2). Detaches the
    tag from every experiment via ON DELETE CASCADE on experiment_tags, not
    a separate step. Returns how many experiments were affected, for the
    frontend's post-delete confirmation message."""
    require_role(current_user, "admin")
    from abkit import storage
    from abkit.db.repositories import ExperimentTagRepo, TagRepo

    parsed_id = uuid_mod.UUID(tag_id)
    tag = TagRepo().get_by_id(parsed_id)
    if tag is None:
        raise storage.StorageError(f"Tag '{tag_id}' not found")
    affected = ExperimentTagRepo().count_for_tag(parsed_id)
    TagRepo().delete(parsed_id)
    _audit(
        current_user, "tag.delete", object_type="tag", object_id=tag_id, object_name=tag.name,
        details={"affected_experiments": affected},
    )
    return affected


class TagNameConflictError(Exception):
    """Raised by run_rename_tag when the requested new name collides
    (case-insensitively — Tag.name is CITEXT) with a DIFFERENT existing tag.
    Renaming doesn't silently fail or auto-merge on this — it surfaces the
    existing tag's id/name so the frontend can offer Merge as an explicit
    follow-up (tag management page, /settings/tags §2.1)."""

    def __init__(self, existing_tag_id: str, existing_tag_name: str) -> None:
        self.existing_tag_id = existing_tag_id
        self.existing_tag_name = existing_tag_name
        super().__init__(f"A tag named '{existing_tag_name}' already exists")


def list_tags_admin(current_user: CurrentUser, q: str | None) -> Any:
    """GET /tags/admin — Admin-only (tag management page). Unlike
    search_tags (typeahead, viewer+, name only — used by the Properties
    modal and the experiments list's tag filter), this returns every tag's
    usage count and creator, which only an admin needs to see in order to
    decide what to rename/merge/delete."""
    require_role(current_user, "admin")
    from abkit.db.repositories import TagRepo

    return TagRepo().list_all_with_counts(q)


def run_rename_tag(current_user: CurrentUser, tag_id: str, new_name: str) -> Any:
    """PATCH /tags/{id} — Admin-only (tag management page §2.1)."""
    require_role(current_user, "admin")
    from abkit import storage
    from abkit.db.repositories import TagRepo

    new_name = new_name.strip()
    if not new_name:
        raise storage.StorageError("Tag name cannot be empty")

    parsed_id = uuid_mod.UUID(tag_id)
    tag = TagRepo().get_by_id(parsed_id)
    if tag is None:
        raise storage.StorageError(f"Tag '{tag_id}' not found")

    existing = TagRepo().find_by_name(new_name)
    if existing is not None and existing.id != parsed_id:
        raise TagNameConflictError(str(existing.id), existing.name)

    old_name = tag.name
    renamed = TagRepo().rename(parsed_id, new_name)
    _audit(
        current_user, "tag.rename", object_type="tag", object_id=tag_id, object_name=renamed.name,
        details={"old_name": old_name, "new_name": renamed.name},
    )
    return renamed


def run_merge_tag(current_user: CurrentUser, tag_id: str, target_id: str) -> int:
    """POST /tags/{id}/merge — Admin-only (tag management page §2.3).
    Reassigns every experiment carrying `tag_id` onto `target_id` and
    deletes `tag_id` (TagRepo.merge does both in one transaction). Returns
    how many experiments were affected, for the frontend's confirmation
    message."""
    require_role(current_user, "admin")
    from abkit import storage
    from abkit.db.repositories import TagRepo

    parsed_source = uuid_mod.UUID(tag_id)
    parsed_target = uuid_mod.UUID(target_id)
    if parsed_source == parsed_target:
        raise storage.StorageError("Cannot merge a tag into itself")

    source = TagRepo().get_by_id(parsed_source)
    target = TagRepo().get_by_id(parsed_target)
    if source is None or target is None:
        raise storage.StorageError("Tag not found")

    affected = TagRepo().merge(parsed_source, parsed_target)
    _audit(
        current_user, "tag.merge", object_type="tag", object_id=str(target.id), object_name=target.name,
        details={"source_tag": source.name, "target_tag": target.name, "affected_experiments": affected},
    )
    return affected


def run_create_folder(current_user: CurrentUser, name: str) -> Any:
    """POST /folders — editor+ (CLAUDE.md 'Permissions model', folders row).
    Unlike tags' get-or-create, a duplicate name is a user error, not
    silently reused — folders are containers someone deliberately organizes
    into, see abkit/db/models.py::Folder."""
    require_role(current_user, "editor")
    from abkit import storage
    from abkit.db.repositories import FolderRepo

    name = name.strip()
    if not name:
        raise storage.StorageError("Folder name cannot be empty")
    if FolderRepo().find_by_name(name) is not None:
        raise FolderNameConflictError(name)

    folder = FolderRepo().create(name, created_by=uuid_mod.UUID(current_user.id))
    _audit(current_user, "folder.create", object_type="folder", object_id=str(folder.id), object_name=folder.name)
    return folder


class FolderNameConflictError(Exception):
    """Raised by run_create_folder/run_rename_folder on an exact-name
    collision — unlike TagNameConflictError there's no merge follow-up
    offered (folders don't merge), so the frontend just surfaces this as a
    plain error (backend/errors.py maps it to 400)."""

    def __init__(self, name: str) -> None:
        super().__init__(f"A folder named '{name}' already exists")


def list_folders(current_user: CurrentUser) -> Any:
    """GET /folders — viewer+. Counts reflect only experiments the CURRENT
    user can see (abkit/access.py::can_view_experiment), matching the list
    page itself — a draft experiment hidden from this user shouldn't inflate
    a folder's count with something they can't open to explain. Returns
    (folders_with_counts, uncategorized_count, total_visible_count)."""
    require_role(current_user, "viewer")
    from abkit.access import can_view_experiment
    from abkit.db.repositories import ExperimentAccessRepo, ExperimentRepo, FolderRepo

    access_experiment_ids = ExperimentAccessRepo().experiment_ids_for_user(uuid_mod.UUID(current_user.id))
    visible = [
        e for e in ExperimentRepo().list_all()
        if can_view_experiment(current_user, e, access_experiment_ids)
    ]
    counts: dict[uuid_mod.UUID | None, int] = {}
    for e in visible:
        counts[e.folder_id] = counts.get(e.folder_id, 0) + 1

    folders = FolderRepo().list_all()
    folders_with_counts = [(f, counts.get(f.id, 0)) for f in folders]
    return folders_with_counts, counts.get(None, 0), len(visible)


def _require_folder_owner_or_admin(current_user: CurrentUser, folder) -> None:
    """Deleting/renaming a folder — its creator or an Admin only (CLAUDE.md
    folders row: narrower than the editor+ needed to CREATE one, since this
    changes/removes something someone else built)."""
    if current_user.role == "admin":
        return
    if folder.created_by is not None and str(folder.created_by) == str(current_user.id):
        return
    raise AuthError("Only the folder's creator or an Admin can rename or delete it")


def run_rename_folder(current_user: CurrentUser, folder_id: str, new_name: str) -> Any:
    """PATCH /folders/{id} — creator or admin (see _require_folder_owner_or_admin)."""
    from abkit import storage
    from abkit.db.repositories import FolderRepo

    parsed_id = uuid_mod.UUID(folder_id)
    folder = FolderRepo().get_by_id(parsed_id)
    if folder is None:
        raise storage.StorageError(f"Folder '{folder_id}' not found")
    _require_folder_owner_or_admin(current_user, folder)

    new_name = new_name.strip()
    if not new_name:
        raise storage.StorageError("Folder name cannot be empty")
    existing = FolderRepo().find_by_name(new_name)
    if existing is not None and existing.id != parsed_id:
        raise FolderNameConflictError(new_name)

    old_name = folder.name
    folder = FolderRepo().rename(parsed_id, new_name)
    _audit(
        current_user, "folder.rename", object_type="folder", object_id=folder_id, object_name=new_name,
        details={"from": old_name, "to": new_name},
    )
    return folder


def run_delete_folder(current_user: CurrentUser, folder_id: str) -> int:
    """DELETE /folders/{id} — creator or admin. Experiments in the folder
    are NOT deleted — folder_id is ON DELETE SET NULL (migration 0017), they
    move to Uncategorized. Returns how many, for the confirmation dialog."""
    from abkit import storage
    from abkit.db.repositories import ExperimentRepo, FolderRepo

    parsed_id = uuid_mod.UUID(folder_id)
    folder = FolderRepo().get_by_id(parsed_id)
    if folder is None:
        raise storage.StorageError(f"Folder '{folder_id}' not found")
    _require_folder_owner_or_admin(current_user, folder)

    affected = ExperimentRepo().count_in_folder(parsed_id)
    FolderRepo().delete(parsed_id)
    _audit(
        current_user, "folder.delete", object_type="folder", object_id=folder_id, object_name=folder.name,
        details={"affected_experiments": affected},
    )
    return affected


def run_move_experiment_to_folder(current_user: CurrentUser, name: str, folder_id: str | None) -> Any:
    """PUT /experiments/{name}/folder — same edit-access gate as rename/
    tags/blocks (owner/access-editor/admin of THIS experiment, CLAUDE.md
    'Permissions model') — moving a test is a property of the test, not of
    the folder, so run_delete_folder's narrower creator-or-admin rule
    doesn't apply here. folder_id=None files it back under Uncategorized."""
    from abkit import storage
    from abkit.db.repositories import ExperimentRepo, FolderRepo

    exp_row = _get_experiment_row(name)
    require_experiment_edit_access(current_user, exp_row)

    parsed_folder_id = uuid_mod.UUID(folder_id) if folder_id else None
    new_folder = FolderRepo().get_by_id(parsed_folder_id) if parsed_folder_id else None
    if parsed_folder_id is not None and new_folder is None:
        raise storage.StorageError(f"Folder '{folder_id}' not found")
    old_folder = FolderRepo().get_by_id(exp_row.folder_id) if exp_row.folder_id else None

    ExperimentRepo().set_folder(exp_row.id, parsed_folder_id)
    _audit(
        current_user, "experiment.folder_change", object_type="experiment", object_id=str(exp_row.id),
        object_name=name,
        details={
            "from": old_folder.name if old_folder else None,
            "to": new_folder.name if new_folder else None,
        },
    )
    return {"folder_id": str(parsed_folder_id) if parsed_folder_id else None}


def run_upload_flow_image(
    current_user: CurrentUser, name: str, group_name: str, flow_title: str, raw: bytes
) -> Any:
    """Stage 4 (CLAUDE.md, variant flow images) — same edit-access gate as
    Redesign/blocks/tags (owner/access-editor/Admin). Content-sniffed and
    re-saved through Pillow (abkit/flow_images.py) rather than trusted by
    filename/Content-Type; FlowImageError propagates as-is, the router maps
    it to a 400."""
    from abkit.db.repositories import FlowImageRepo
    from abkit.db.store import DbExperimentStore
    from abkit.flow_images import MAX_IMAGES_PER_GROUP, FlowImageError, validate_and_resave

    exp_row = _get_experiment_row(name)
    require_experiment_edit_access(current_user, exp_row)

    existing_count = FlowImageRepo().count_for_group(exp_row.id, group_name)
    if existing_count >= MAX_IMAGES_PER_GROUP:
        raise FlowImageError(f"Group '{group_name}' already has the maximum of {MAX_IMAGES_PER_GROUP} images")

    dest_stem = DbExperimentStore().data_dir / name / "flow_images" / uuid_mod.uuid4().hex
    with _timed("upload_flow_image", user=current_user.email, experiment=name, group=group_name):
        dest_path = validate_and_resave(raw, dest_stem)
        image = FlowImageRepo().create(
            experiment_id=exp_row.id, group_name=group_name, flow_title=flow_title,
            file_path=str(dest_path), uploaded_by=uuid_mod.UUID(current_user.id),
        )
    _audit(
        current_user, "flow_image.upload", object_type="experiment", object_id=str(exp_row.id),
        object_name=name, details={"group_name": group_name, "image_id": str(image.id)},
    )
    return image


def run_delete_flow_image(current_user: CurrentUser, name: str, image_id: str) -> None:
    """DELETE /experiments/{name}/flow-images/{id} — same edit-access gate as
    upload. Unlinks the file too, unlike run_delete_dataset's SET NULL-based
    "never touch the file" discipline — flow images are part of the test,
    not an independent entity (see ExperimentFlowImage's docstring)."""
    from pathlib import Path

    from abkit import storage
    from abkit.db.repositories import FlowImageRepo

    exp_row = _get_experiment_row(name)
    require_experiment_edit_access(current_user, exp_row)

    parsed_id = uuid_mod.UUID(image_id)
    image = FlowImageRepo().get_by_id(parsed_id)
    if image is None or image.experiment_id != exp_row.id:
        raise storage.StorageError(f"Flow image '{image_id}' not found")

    with _timed("delete_flow_image", user=current_user.email, experiment=name):
        file_path = FlowImageRepo().delete(parsed_id)
        if file_path:
            Path(file_path).unlink(missing_ok=True)
    _audit(
        current_user, "flow_image.delete", object_type="experiment", object_id=str(exp_row.id),
        object_name=name, details={"group_name": image.group_name, "image_id": image_id},
    )


def run_set_flow_image_group_order(
    current_user: CurrentUser, name: str, group_name: str, flow_title: str, image_ids: list[str]
) -> Any:
    """PUT /experiments/{name}/flow-images/order — final-submit reconciliation
    for one wizard column (abkit/db/repositories.py::FlowImageRepo.set_group_order):
    sets flow_title on every surviving image, position from image_ids' order,
    deletes (DB row + file) any of the group's existing images the caller
    didn't include — the wizard's thumbnail delete/reorder actions are
    deferred to this one call at Redesign submit, not applied live."""
    from pathlib import Path

    from abkit.db.repositories import FlowImageRepo

    exp_row = _get_experiment_row(name)
    require_experiment_edit_access(current_user, exp_row)

    parsed_ids = [uuid_mod.UUID(i) for i in image_ids]
    with _timed("set_flow_image_group_order", user=current_user.email, experiment=name, group=group_name):
        deleted_paths = FlowImageRepo().set_group_order(exp_row.id, group_name, flow_title, parsed_ids)
        for file_path in deleted_paths:
            Path(file_path).unlink(missing_ok=True)
        all_images = FlowImageRepo().list_for_experiment(exp_row.id)
        images = [i for i in all_images if i.group_name == group_name]
        _regenerate_design_report(exp_row, all_images)
    _audit(
        current_user, "flow_image.reorder", object_type="experiment", object_id=str(exp_row.id),
        object_name=name, details={"group_name": group_name, "n_images": len(images)},
    )
    return images


def _regenerate_design_report(exp_row, all_images) -> None:
    """Called at the end of the wizard's final per-group flow-image save
    step (run_set_flow_image_group_order) — design_report.html is written
    once at design/redesign time (abkit/experiment.py::Experiment.design()),
    BEFORE flow images ever exist (they're uploaded in a separate step right
    after, see Step4Review.tsx::saveFlowImages), so the report needs
    patching once images actually land. A full re-render isn't possible
    here: render_design_report() needs the DesignReport object (power/SRM/
    isolation results), which only exists transiently mid-Experiment.design()
    and isn't reloadable via Experiment.load() — so this splices just the
    flow-images section's freshly-rendered HTML into the already-saved file
    in place of the anchor comment templates/design_report.html.j2 leaves
    for exactly this purpose (see that file's comment). Best-effort: a
    failure here (e.g. the experiment's on-disk directory got removed out
    of band) shouldn't fail the image save itself, which already succeeded
    in the DB."""
    try:
        from abkit.db.store import DbExperimentStore
        from abkit.viz.report import render_flow_images_section

        grouped: dict[str, list[dict[str, Any]]] = {}
        for img in all_images:
            grouped.setdefault(img.group_name, []).append(
                {"flow_title": img.flow_title, "file_path": img.file_path}
            )
        report_path = DbExperimentStore().data_dir / exp_row.name / "design_report.html"
        if not report_path.exists():
            return
        html = report_path.read_text(encoding="utf-8")
        report_path.write_text(render_flow_images_section(html, grouped), encoding="utf-8")
    except Exception:
        log.error("regenerate_design_report.failed", exc_info=True, experiment=exp_row.name)


# --------------------------------------------------------------------------
# Экспорт/импорт эксперимента zip-архивом (пакет export/import)
#
# Чтение/запись самого архива — abkit/exchange.py (чистые байты <-> структуры,
# без БД). Здесь — оркестрация: репозитории, права, audit_log.
# --------------------------------------------------------------------------


class DatasetNameMatchConfirmationRequired(Exception):
    """Импорт: датасет из архива не нашелся по sha256 содержимого, но нашелся
    по ИМЕНИ — то есть в этом экземпляре лежит файл с тем же именем и ДРУГИМ
    содержимым. Молча слинковать его нельзя (анализ поехал бы по данным,
    которых экспортер не видел), молча пропустить — тоже (пользователь ждет
    рабочий тест). Поэтому — тот же паттерн, что у DatasetInUseError:
    исключение с деталями, роутер маппит в 400 confirmation_required, фронт
    показывает список и переспрашивает.

    Бросается ДО создания эксперимента (см. run_import_experiment) — повторный
    вызов с confirm_dataset_names=True не должен натыкаться на полусозданный
    объект от первой попытки."""

    def __init__(self, dataset_names: list[str]) -> None:
        self.dataset_names = dataset_names
        super().__init__(
            "These datasets match an existing dataset by name but not by content: "
            + ", ".join(dataset_names)
        )


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _parse_dt(value):
    """ISO-строка из архива -> datetime; None/мусор -> None (импорт не должен
    падать из-за неразобранной даты — она не несущая)."""
    from datetime import datetime

    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _experiment_dataset_refs(exp_row) -> list[dict[str, Any]]:
    """Датасеты эксперимента -> ссылки для архива (имя + sha256 содержимого).

    Объединяет оба источника связи: experiment_datasets (актуальный,
    many-to-many, с kind) и legacy datasets.experiment_id ("primary/first-use",
    CLAUDE.md) — иначе тест, у которого связь есть только в старом поле,
    экспортировался бы без единой ссылки на данные."""
    from abkit.db.repositories import DatasetRepo, ExperimentDatasetRepo

    kind_by_dataset: dict[uuid_mod.UUID, str] = {}
    for link in ExperimentDatasetRepo().list_for_experiment(exp_row.id):
        kind_by_dataset.setdefault(link.dataset_id, link.kind)
    for ds in DatasetRepo().list_for_experiment(exp_row.id):
        kind_by_dataset.setdefault(ds.id, ds.kind)

    refs: list[dict[str, Any]] = []
    for dataset_id, kind in kind_by_dataset.items():
        ds = DatasetRepo().get_by_id(dataset_id)
        if ds is None:
            continue
        refs.append(
            {
                "filename": ds.filename,
                "sha256": ds.sha256,
                "kind": kind,
                "source": ds.source,
                "n_rows": ds.n_rows,
                "columns": ds.columns,
            }
        )
    return refs


def run_export_experiment(
    current_user: CurrentUser, name: str, *, include_datasets: bool = False
) -> tuple[str, bytes]:
    """GET /experiments/{name}/export -> (filename, zip-байты).

    Права: Editor+ на любой ВИДИМЫЙ ему тест — то же правило и та же его
    реализация, что у Analyze/Validate (CLAUDE.md, "Permissions model"):
    роль проверяется здесь, видимость — гейтом `_visible_or_404` в роутере, до
    вызова. Владения/гранта не требуется: экспорт — чтение, а прочитать этот
    тест пользователь и так может.
    """
    require_role(current_user, "editor")

    import abkit
    from abkit.dataset_files import read_dataset_file
    from abkit.db.repositories import (
        AssignmentRepo,
        BlockRepo,
        DatasetRepo,
        ExperimentTagRepo,
        ResultRepo,
    )
    from abkit.db.store import DbExperimentStore
    from abkit.exchange import (
        EXPORT_FORMAT_VERSION,
        REPORT_FILENAMES,
        dataframe_to_parquet_bytes,
        write_archive,
    )

    exp_row = _get_experiment_row(name)

    with _timed("export_experiment", user=current_user.email, experiment=name):
        blocks = BlockRepo().list_for_experiment(exp_row.id)
        tags = ExperimentTagRepo().list_for_experiment(exp_row.id)
        dataset_refs = _experiment_dataset_refs(exp_row)

        # split_source живет внутри config (DesignConfig) — отдельным полем
        # рядом НЕ дублируем: "external-split declaration" из ТЗ — это оно и
        # есть, а два источника одной правды разъезжаются.
        config = exp_row.config or {}
        is_external_split = config.get("split_source") == "external"

        assignments = None
        if not is_external_split:
            assignments = AssignmentRepo().load(exp_row.id)

        experiment_payload = {
            "name": exp_row.name,
            "config": config,
            "design_summary": exp_row.design_summary,
            "status": exp_row.status,
            "publication_status": exp_row.publication_status,
            "visible_roles": exp_row.visible_roles,
            "created_at": exp_row.created_at,
            "started_at": exp_row.started_at,
            "completed_at": exp_row.completed_at,
            "archived_at": exp_row.archived_at,
            "tags": [t.name for t in tags],
            "blocks": [
                {
                    "kind": b.kind,
                    "title": b.title,
                    "content_md": b.content_md,
                    "position": b.position,
                }
                for b in blocks
            ],
            "datasets": dataset_refs,
        }

        analysis_results = [
            {
                "results": r.results,
                "dataset_filename": r.dataset_filename,
                "created_at": r.created_at,
            }
            for r in ResultRepo().list_for_experiment(exp_row.id)
        ]

        artifact_dir = DbExperimentStore().data_dir / exp_row.name
        reports: dict[str, bytes] = {}
        for report_name in REPORT_FILENAMES:
            report_path = artifact_dir / report_name
            if report_path.exists():
                reports[report_name] = report_path.read_bytes()

        dataset_snapshots: dict[str, bytes] = {}
        if include_datasets:
            for ref in dataset_refs:
                ds = next(
                    (
                        d
                        for d in DatasetRepo().list_all()
                        if d.sha256 == ref["sha256"] and d.filename == ref["filename"]
                    ),
                    None,
                )
                if ds is None:
                    continue
                try:
                    frame = read_dataset_file(ds.storage_path)
                except OSError:
                    # Файл датасета исчез с диска — не повод завалить весь
                    # экспорт: ссылка (имя + sha256) в архиве остается, просто
                    # без снапшота.
                    log.warning(
                        "export_experiment.dataset_snapshot_missing",
                        experiment=name,
                        dataset=ds.filename,
                    )
                    continue
                dataset_snapshots[ref["sha256"]] = dataframe_to_parquet_bytes(frame)

        manifest = {
            "format_version": EXPORT_FORMAT_VERSION,
            "app_version": abkit.__version__,
            "exported_at": _utcnow(),
            "exported_by": current_user.email,
            "experiment_name": exp_row.name,
            "includes_dataset_snapshots": bool(dataset_snapshots),
        }

        raw = write_archive(
            manifest=manifest,
            experiment=experiment_payload,
            assignments=assignments,
            analysis_results=analysis_results,
            reports=reports,
            dataset_snapshots=dataset_snapshots,
        )

    _audit(
        current_user,
        "experiment.export",
        object_type="experiment",
        object_id=str(exp_row.id),
        object_name=name,
        details={
            "include_datasets": include_datasets,
            "dataset_snapshots": len(dataset_snapshots),
            "size_bytes": len(raw),
        },
    )
    return f"{exp_row.name}_export.zip", raw


def _unique_experiment_name(original: str) -> str:
    """Имя свободно -> как есть; занято -> "<name> (imported)"; занято и оно ->
    "<name> (imported 2)" и далее. Имя эксперимента уникально на уровне БД и
    служит адресом (CLAUDE.md, "Известный техдолг"), так что импорт обязан
    развести коллизию сам, а не упасть."""
    from abkit.db.repositories import ExperimentRepo

    repo = ExperimentRepo()
    if repo.get_by_name(original) is None:
        return original
    candidate = f"{original} (imported)"
    if repo.get_by_name(candidate) is None:
        return candidate
    suffix = 2
    while repo.get_by_name(f"{original} (imported {suffix})") is not None:
        suffix += 1
    return f"{original} (imported {suffix})"


def _plan_dataset_links(contents, confirm_dataset_names: bool) -> tuple[list[dict[str, Any]], list[str]]:
    """Решает, что делать с каждой ссылкой на датасет, НЕ трогая БД на запись.

    Порядок разрешения (ТЗ): sha256 -> имя (с подтверждением) -> снапшот из
    архива -> предупреждение. Возвращает (план, warnings); бросает
    DatasetNameMatchConfirmationRequired, если нужен ответ пользователя.
    Вызывается ДО создания эксперимента — отказ на этом шаге не должен
    оставлять за собой полусозданный тест."""
    from abkit.db.repositories import DatasetRepo

    existing = DatasetRepo().list_all()
    by_sha = {d.sha256: d for d in existing}

    plan: list[dict[str, Any]] = []
    warnings: list[str] = []
    pending_name_matches: list[str] = []

    for ref in contents.experiment.get("datasets", []) or []:
        filename = ref.get("filename") or "(unnamed)"
        sha256 = ref.get("sha256")
        kind = ref.get("kind") or "pre_design"

        matched = by_sha.get(sha256) if sha256 else None
        if matched is not None:
            plan.append({"action": "link", "dataset_id": matched.id, "kind": kind})
            continue

        name_match = next((d for d in existing if d.filename == filename), None)
        if name_match is not None:
            if not confirm_dataset_names:
                pending_name_matches.append(filename)
                continue
            plan.append({"action": "link", "dataset_id": name_match.id, "kind": kind})
            warnings.append(
                f"Dataset '{filename}' was linked by name — its contents differ from the "
                f"exported dataset, so analysis results may not reproduce exactly."
            )
            continue

        snapshot = contents.dataset_snapshots.get(sha256) if sha256 else None
        if snapshot is not None:
            plan.append({"action": "create", "ref": ref, "snapshot": snapshot, "kind": kind})
            continue

        warnings.append(
            f"Dataset '{filename}' was not found in this instance and the archive carries "
            f"no snapshot of it — re-analysis is unavailable until it is relinked."
        )

    if pending_name_matches:
        raise DatasetNameMatchConfirmationRequired(pending_name_matches)

    return plan, warnings


def _create_dataset_from_snapshot(current_user: CurrentUser, ref: dict[str, Any], snapshot: bytes, kind: str):
    """Снапшот из архива -> новый датасет, владелец — импортирующий.

    Файл на диске ВСЕГДА пишется с расширением .parquet, даже если
    ref["filename"] == "data.csv": read_dataset_file (abkit/dataset_files.py)
    выбирает парсер ПО РАСШИРЕНИЮ storage_path, так что parquet-байты под
    именем .csv молча уехали бы в pd.read_csv. Отображаемое имя
    (datasets.filename) при этом сохраняем исходным — оно к чтению файла
    отношения не имеет."""
    import io
    from pathlib import Path

    from abkit.db.repositories import DatasetRepo
    from abkit.db.store import DbExperimentStore

    frame = pd.read_parquet(io.BytesIO(snapshot))
    filename = ref.get("filename") or "imported.parquet"
    stem = Path(filename).stem or "imported"
    dest_dir = DbExperimentStore().data_dir / "_uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{uuid_mod.uuid4().hex}_{stem}.parquet"
    frame.to_parquet(dest_path, index=False)

    return DatasetRepo().create(
        kind=kind,
        filename=filename,
        n_rows=len(frame),
        columns=list(frame.columns),
        storage_path=str(dest_path),
        # Считаем заново по фактически записанным данным, а не копируем
        # ref["sha256"]: sha должна описывать то, что реально лежит на диске.
        # На честном round-trip'е это одно и то же значение.
        sha256=DatasetRepo.compute_sha256(frame),
        uploaded_by=uuid_mod.UUID(current_user.id),
        source="upload",
    )


def _restore_blocks(experiment_id: uuid_mod.UUID, blocks: list[dict[str, Any]], updated_by) -> None:
    """Блоки из архива поверх дефолтных.

    ExperimentRepo.create() уже создала hypothesis/conclusion/decision, а
    BlockRepo.upsert_many() создает НОВУЮ строку, если id не передан — без
    сопоставления по kind импорт получил бы по два блока каждого вида."""
    from abkit.db.repositories import BlockRepo

    existing_by_kind = {b.kind: b for b in BlockRepo().list_for_experiment(experiment_id)}
    payload: list[dict[str, Any]] = []
    for position, block in enumerate(blocks):
        kind = block.get("kind") or "custom"
        match = existing_by_kind.get(kind) if kind != "custom" else None
        payload.append(
            {
                "id": str(match.id) if match is not None else None,
                "kind": kind,
                "title": block.get("title") or "",
                "content_md": block.get("content_md") or "",
                "position": block.get("position", position),
            }
        )
    if payload:
        BlockRepo().upsert_many(experiment_id, payload, updated_by=updated_by)


def run_import_experiment(
    current_user: CurrentUser, raw: bytes, *, confirm_dataset_names: bool = False
) -> dict[str, Any]:
    """POST /experiments/import — Editor+. Всегда создает НОВЫЙ эксперимент
    (никогда не перезаписывает существующий): publication=draft, владелец —
    импортирующий, конфликт имени -> "<name> (imported)".

    Восстанавливается: config, блоки, теги, назначения, прогоны анализа,
    отчеты. Операционный статус и даты жизненного цикла переносятся как есть,
    а created_at — специально ИЗ АРХИВА, а не now(): design_report.html
    копируется в архиве побайтово и уже содержит исходную дату в шапке, так
    что "created" в UI и в отчете иначе разъехались бы."""
    require_role(current_user, "editor")

    from abkit.db.repositories import (
        AssignmentRepo,
        ExperimentDatasetRepo,
        ExperimentRepo,
        ExperimentTagRepo,
        ResultRepo,
        TagRepo,
    )
    from abkit.db.store import DbExperimentStore
    from abkit.exchange import read_archive

    contents = read_archive(raw)
    payload = contents.experiment

    original_name = payload.get("name")
    if not original_name or not isinstance(original_name, str):
        from abkit import storage

        raise storage.StorageError("experiment.json has no valid 'name'")

    status = payload.get("status") or "designed"
    if status not in ("designed", "running", "completed", "archived"):
        status = "designed"

    # До первой записи в БД: план по датасетам может потребовать подтверждения,
    # и тогда ничего создано быть не должно.
    plan, warnings = _plan_dataset_links(contents, confirm_dataset_names)

    name = _unique_experiment_name(original_name)
    importer_id = uuid_mod.UUID(current_user.id)

    with _timed("import_experiment", user=current_user.email, experiment=name):
        exp_row = ExperimentRepo().create(
            name=name,
            owner_id=importer_id,
            status=status,
            config=payload.get("config") or {},
            design_summary=payload.get("design_summary"),
            # Всегда draft, каким бы ни был экспортированный тест: импортер не
            # должен нечаянно опубликовать чужой тест самим фактом импорта.
            publication_status="draft",
            created_at=_parse_dt(payload.get("created_at")),
            started_at=_parse_dt(payload.get("started_at")),
            completed_at=_parse_dt(payload.get("completed_at")),
            archived_at=_parse_dt(payload.get("archived_at")),
        )

        visible_roles = payload.get("visible_roles")
        if visible_roles:
            ExperimentRepo().update_visible_roles(name, visible_roles)

        if contents.assignments is not None and not contents.assignments.empty:
            AssignmentRepo().bulk_insert(exp_row.id, contents.assignments)

        _restore_blocks(exp_row.id, payload.get("blocks") or [], importer_id)

        tag_names = [t for t in (payload.get("tags") or []) if isinstance(t, str) and t.strip()]
        if tag_names:
            tag_ids = [
                TagRepo().get_or_create(t.strip(), created_by=importer_id).id for t in tag_names
            ]
            ExperimentTagRepo().set_for_experiment(exp_row.id, tag_ids)

        artifact_dir = DbExperimentStore().data_dir / name
        artifact_dir.mkdir(parents=True, exist_ok=True)
        for report_name, report_bytes in contents.reports.items():
            (artifact_dir / report_name).write_bytes(report_bytes)

        for item in plan:
            if item["action"] == "link":
                dataset_id = item["dataset_id"]
            else:
                dataset_id = _create_dataset_from_snapshot(
                    current_user, item["ref"], item["snapshot"], item["kind"]
                )
            ExperimentDatasetRepo().link(exp_row.id, dataset_id, item["kind"])

        report_path = str(artifact_dir / "report.html")
        for run in contents.analysis_results:
            results = run.get("results")
            if not isinstance(results, dict):
                continue
            ResultRepo().create(
                experiment_id=exp_row.id,
                results=results,
                report_path=report_path,
                dataset_filename=run.get("dataset_filename"),
                created_by=importer_id,
            )

    _audit(
        current_user,
        "experiment.import",
        object_type="experiment",
        object_id=str(exp_row.id),
        object_name=name,
        details={
            "original_name": original_name,
            "renamed": name != original_name,
            "source_app_version": contents.manifest.get("app_version"),
            "format_version": contents.manifest.get("format_version"),
            "warnings": len(warnings),
        },
    )

    return {
        "experiment_name": name,
        "original_name": original_name,
        "renamed": name != original_name,
        "warnings": warnings,
    }
