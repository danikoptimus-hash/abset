"""Изоляция кандидатов от юзеров, занятых в других активных экспериментах."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

import pandas as pd

from abkit import storage
from abkit.idnorm import normalize_id_series

_ACTIVE_STATUSES = ("designed", "running")


class _OccupiedUnitsSource(Protocol):
    """Структурный протокол — единственное, что нужно apply_isolation() от
    хранилища в db-режиме (DOCKER.md §5: изоляция там — один SQL-запрос вместо
    чтения assignments.parquet каждого активного эксперимента). Файловый режим
    (дефолт) этот протокол не использует и ведет себя как раньше."""

    def occupied_units(
        self,
        exclude_experiments: Literal["all_active"] | list[str],
        current_experiment_name: str | None,
    ) -> dict[str, set]: ...

    def occupied_units_selected(
        self, selected_experiments: list[str], current_experiment_name: str | None
    ) -> dict[str, set]: ...


@dataclass
class IsolationResult:
    candidates: pd.DataFrame
    excluded_by_experiment: dict[str, int] = field(default_factory=dict)
    n_before: int = 0
    n_excluded: int = 0
    n_available: int = 0
    mode: str = "off"


def _active_experiments(
    experiments_dir: Path,
    exclude_experiments: Literal["all_active"] | list[str],
) -> dict[str, dict]:
    registry = storage.read_registry(experiments_dir)
    active = {
        name: entry for name, entry in registry.items() if entry["status"] in _ACTIVE_STATUSES
    }
    if exclude_experiments != "all_active":
        for name in exclude_experiments:
            active.pop(name, None)
    return active


def _collect_occupied_units(active: dict[str, dict]) -> dict[str, set]:
    """Для каждого активного эксперимента возвращает set unit_id из его assignments.parquet."""
    occupied: dict[str, set] = {}
    for name, entry in active.items():
        assignments_path = Path(entry["path"]) / "assignments.parquet"
        if not assignments_path.exists():
            continue
        units = pd.read_parquet(assignments_path, columns=["unit_id"])["unit_id"]
        occupied[name] = set(units)
    return occupied


def _active_experiments_selected(
    experiments_dir: Path, selected_experiments: list[str], current_experiment_name: str | None
) -> dict[str, dict]:
    """Как _active_experiments, но вместо "все активные КРОМЕ X" — "только эти
    конкретные X" (и все равно только среди активных статусов — выбор
    конкретного completed/archived эксперимента в UI и так невозможен, см.
    render_design_tab, но на бэкенде подстраховываемся тоже)."""
    registry = storage.read_registry(experiments_dir)
    return {
        name: entry
        for name, entry in registry.items()
        if name in selected_experiments
        and entry["status"] in _ACTIVE_STATUSES
        and name != current_experiment_name
    }


def apply_isolation(
    data: pd.DataFrame,
    unit_col: str,
    experiments_dir: Path,
    mode: Literal["exclude", "warn", "off", "exclude_selected"] = "exclude",
    exclude_experiments: Literal["all_active"] | list[str] = "all_active",
    current_experiment_name: str | None = None,
    store: _OccupiedUnitsSource | None = None,
    selected_experiments: list[str] | None = None,
) -> IsolationResult:
    """Исключает из кандидатов юзеров, занятых в других designed/running экспериментах.

    mode="off" — пропустить проверку. mode="warn" — посчитать пересечение, но не
    фильтровать (решение об исключении принимается вызывающей стороной, например CLI
    после подтверждения пользователем). mode="exclude" — молча исключить (из ВСЕХ
    активных, кроме перечисленных в exclude_experiments). mode="exclude_selected" —
    молча исключить, но только из юнитов, занятых экспериментами, явно перечисленными
    в selected_experiments (остальные активные эксперименты не учитываются).

    В любом режиме, кроме off, учитываются ТОЛЬКО эксперименты со статусами
    designed/running (_ACTIVE_STATUSES) — completed/archived никогда не
    блокируют кандидатов, ни в файловом, ни в db-режиме.

    store: в db-режиме (ABKIT_MODE=db) передается DbExperimentStore — тогда
    список занятых unit_id получается одним SQL-запросом (store.occupied_units/
    occupied_units_selected) вместо чтения assignments.parquet каждого
    активного эксперимента по отдельности. По умолчанию (store=None) поведение
    файлового режима не меняется.
    """
    n_before = len(data)
    if mode == "off":
        return IsolationResult(
            candidates=data, n_before=n_before, n_excluded=0, n_available=n_before, mode=mode
        )

    if mode == "exclude_selected":
        selected = selected_experiments or []
        if store is not None:
            occupied = store.occupied_units_selected(selected, current_experiment_name)
        else:
            active = _active_experiments_selected(experiments_dir, selected, current_experiment_name)
            occupied = _collect_occupied_units(active)
    elif store is not None:
        occupied = store.occupied_units(exclude_experiments, current_experiment_name)
    else:
        active = _active_experiments(experiments_dir, exclude_experiments)
        if current_experiment_name:
            active.pop(current_experiment_name, None)
        occupied = _collect_occupied_units(active)

    # unit_id — идентификатор, не число: приводим обе стороны к str перед
    # сравнением, иначе dtype-рассинхрон (эта функция vs. другой эксперимент
    # / другой источник данных) молча даст пустое пересечение вместо ошибки.
    occupied = {name: set(normalize_id_series(pd.Series(list(units)))) for name, units in occupied.items()}
    normalized_col = normalize_id_series(data[unit_col])
    candidate_units = set(normalized_col)

    excluded_by_experiment: dict[str, int] = {}
    excluded_units: set = set()
    for name, units in occupied.items():
        overlap = candidate_units & units
        if overlap:
            excluded_by_experiment[name] = len(overlap)
            excluded_units |= overlap

    if mode in ("exclude", "exclude_selected") and excluded_units:
        candidates = data[~normalized_col.isin(excluded_units)]
    else:
        candidates = data

    return IsolationResult(
        candidates=candidates,
        excluded_by_experiment=excluded_by_experiment,
        n_before=n_before,
        n_excluded=n_before - len(candidates),
        n_available=len(candidates),
        mode=mode,
    )
