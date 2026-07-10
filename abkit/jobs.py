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

import time
import uuid as uuid_mod
from contextlib import contextmanager
from typing import Any

import pandas as pd

from abkit.access import require_experiment_edit_access
from abkit.analysis.results import AnalysisResults
from abkit.auth.guards import AuthError, CurrentUser, require_role
from abkit.config import DesignConfig
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
    эксперименты), затем расширено на experiment_access (UX-пакет)."""
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
    where the (now hand-edited) query actually comes from."""
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
    from abkit.db.repositories import ExperimentAccessRepo, ExperimentRepo

    exp_row = _get_experiment_row(name)
    require_experiment_edit_access(current_user, exp_row)

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

    _audit(
        current_user, "experiment.properties_change",
        object_type="experiment", object_id=str(exp_row.id), object_name=current_name,
        details={
            "owners": [str(u) for u in owner_uuids],
            "editors": [str(u) for u, access in grants if access == "editor"],
            "visible_roles": visible_roles,
        },
    )


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
