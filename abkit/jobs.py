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
        dataset_id = DatasetRepo().create(
            kind=kind, filename=f"{name}.parquet", n_rows=result.n_rows, columns=result.columns,
            storage_path=str(dest_path), sha256=sha256, experiment_id=exp_uuid,
            uploaded_by=uuid_mod.UUID(current_user.id), source="sql", connection_id=conn_row.id,
            sql_text=sql, fetched_at=datetime.now(timezone.utc),
            source_schema=source_schema, source_table=source_table,
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
        DatasetRepo().update_after_refresh(ds.id, n_rows=result.n_rows, columns=result.columns, sha256=sha256)
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
