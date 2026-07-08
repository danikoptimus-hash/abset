"""Единая точка запуска мутирующих операций (design/analyze/validate/status
change/delete) — используется и Streamlit (app.py), и в перспективе CLI, чтобы
guard-проверки (DOCKER.md §4.1) применялись независимо от UI: Viewer не должен
суметь вызвать мутацию даже прямым вызовом этих функций, в обход спрятанных в
UI кнопок (критерий готовности этапа D2, DOCKER.md §12).

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

from abkit.analysis.results import AnalysisResults
from abkit.auth.guards import CurrentUser, require_owner_or_admin, require_role
from abkit.config import DesignConfig
from abkit.experiment import Experiment
from abkit.logging_config import get_logger

log = get_logger("abkit.jobs")


def _get_experiment_row(name: str):
    from abkit import storage
    from abkit.db.repositories import ExperimentRepo

    exp_row = ExperimentRepo().get_by_name(name)
    if exp_row is None:
        raise storage.StorageError(f"Эксперимент '{name}' не найден")
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
    current_user: CurrentUser, data: pd.DataFrame, config: DesignConfig, **kwargs: Any
):
    require_role(current_user, "editor")
    from abkit.validation.simulation import run_aa

    with _timed("validate_aa", user=current_user.email, experiment=config.name):
        report = run_aa(data, config, **kwargs)
    _audit(
        current_user, "validation.run",
        object_type="experiment", object_name=config.name, details={"kind": "aa"},
    )
    return report


def run_validate_ab(
    current_user: CurrentUser, data: pd.DataFrame, config: DesignConfig, **kwargs: Any
):
    require_role(current_user, "editor")
    from abkit.validation.simulation import run_ab

    with _timed("validate_ab", user=current_user.email, experiment=config.name):
        report = run_ab(data, config, **kwargs)
    _audit(
        current_user, "validation.run",
        object_type="experiment", object_name=config.name, details={"kind": "ab"},
    )
    return report


def run_update_status(current_user: CurrentUser, name: str, new_status: str) -> None:
    """Менять статус СВОИХ экспериментов может Editor, ЛЮБЫХ — Admin (DOCKER.md §4.1)."""
    from abkit.db.repositories import ExperimentRepo

    exp_row = _get_experiment_row(name)
    require_owner_or_admin(current_user, str(exp_row.owner_id))
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
    (владелец или admin), обе стороны переключения аудируются (FRONTEND.md
    §3.3: "оба направления в audit_log")."""
    from abkit.db.repositories import ExperimentRepo

    exp_row = _get_experiment_row(name)
    require_owner_or_admin(current_user, str(exp_row.owner_id))
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
    """Переименование — владелец или admin (та же политика, что у смены
    статуса); артефактная директория переименовывается вместе со строкой БД,
    чтобы experiment.path продолжал резолвиться по новому имени."""
    import shutil

    from abkit.db.repositories import ExperimentRepo
    from abkit.db.store import DbExperimentStore

    exp_row = _get_experiment_row(name)
    require_owner_or_admin(current_user, str(exp_row.owner_id))
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
    """Только для UI-подтверждения удаления (app.py) — сколько строк реально
    удалится каскадом, без самого удаления. Тот же guard, что и у самого
    удаления (require_owner_or_admin) — точные числа по чужому эксперименту
    тоже не должен видеть посторонний Editor/Viewer."""
    from abkit.db.repositories import AssignmentRepo, DatasetRepo, ResultRepo

    exp_row = _get_experiment_row(name)
    require_owner_or_admin(current_user, str(exp_row.owner_id))
    return {
        "assignments": AssignmentRepo().count_for_experiment(exp_row.id),
        "datasets": DatasetRepo().count_for_experiment(exp_row.id),
        "results": ResultRepo().count_for_experiment(exp_row.id),
    }


def run_delete_experiment(current_user: CurrentUser, name: str) -> None:
    """Удалять эксперимент может владелец ИЛИ Admin (require_owner_or_admin —
    та же политика, что у смены статуса). Раньше было Admin-only без
    исключений; изменено по явному запросу пользователя (UX-правка, чтобы
    Editor мог убирать за собой свои же эксперименты)."""
    exp_row = _get_experiment_row(name)
    require_owner_or_admin(current_user, str(exp_row.owner_id))

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
