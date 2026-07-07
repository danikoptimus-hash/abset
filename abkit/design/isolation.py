"""Изоляция кандидатов от юзеров, занятых в других активных экспериментах."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pandas as pd

from abkit import storage

_ACTIVE_STATUSES = ("designed", "running")


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


def apply_isolation(
    data: pd.DataFrame,
    unit_col: str,
    experiments_dir: Path,
    mode: Literal["exclude", "warn", "off"] = "exclude",
    exclude_experiments: Literal["all_active"] | list[str] = "all_active",
    current_experiment_name: str | None = None,
) -> IsolationResult:
    """Исключает из кандидатов юзеров, занятых в других designed/running экспериментах.

    mode="off" — пропустить проверку. mode="warn" — посчитать пересечение, но не
    фильтровать (решение об исключении принимается вызывающей стороной, например CLI
    после подтверждения пользователем). mode="exclude" — молча исключить.
    """
    n_before = len(data)
    if mode == "off":
        return IsolationResult(
            candidates=data, n_before=n_before, n_excluded=0, n_available=n_before, mode=mode
        )

    active = _active_experiments(experiments_dir, exclude_experiments)
    if current_experiment_name:
        active.pop(current_experiment_name, None)

    occupied = _collect_occupied_units(active)
    candidate_units = set(data[unit_col])

    excluded_by_experiment: dict[str, int] = {}
    excluded_units: set = set()
    for name, units in occupied.items():
        overlap = candidate_units & units
        if overlap:
            excluded_by_experiment[name] = len(overlap)
            excluded_units |= overlap

    if mode == "exclude" and excluded_units:
        candidates = data[~data[unit_col].isin(excluded_units)]
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
