"""Сплитование юнитов по группам: simple, stratified, hash."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd


@dataclass
class SplitResult:
    group: pd.Series
    salt: str | None
    warnings: list[str] = field(default_factory=list)


def generate_salt() -> str:
    return secrets.token_hex(16)


def _allocate_sizes(n: int, groups: dict[str, float]) -> dict[str, int]:
    """Распределяет n юнитов по группам пропорционально groups (largest remainder)."""
    names = list(groups.keys())
    proportions = np.array([groups[k] for k in names], dtype=float)
    raw = proportions * n
    floor = np.floor(raw).astype(int)
    remainder = n - int(floor.sum())
    frac = raw - floor
    order = np.argsort(-frac, kind="stable")
    alloc = floor.copy()
    for i in order[:remainder]:
        alloc[i] += 1
    return {names[i]: int(alloc[i]) for i in range(len(names))}


def simple_split(unit_ids: pd.Series, groups: dict[str, float], seed: int) -> pd.Series:
    """Случайный сплит с фиксированным seed, доли групп — largest remainder."""
    n = len(unit_ids)
    rng = np.random.default_rng(seed)
    order = rng.permutation(n)
    sizes = _allocate_sizes(n, groups)
    labels = np.empty(n, dtype=object)
    pos = 0
    for name, size in sizes.items():
        labels[order[pos : pos + size]] = name
        pos += size
    return pd.Series(labels, index=unit_ids.index, name="group")


def stratified_split(
    unit_ids: pd.Series, strata: pd.Series, groups: dict[str, float], seed: int
) -> pd.Series:
    """Сплит внутри каждой страты отдельно (сохраняет пропорции групп по стратам)."""
    labels = pd.Series(index=unit_ids.index, dtype=object, name="group")
    rng = np.random.default_rng(seed)
    for stratum_value in sorted(strata.unique(), key=str):
        mask = strata == stratum_value
        sub_ids = unit_ids[mask]
        sub_seed = int(rng.integers(0, 2**32 - 1))
        sub_labels = simple_split(sub_ids, groups, seed=sub_seed)
        labels.loc[sub_ids.index] = sub_labels
    return labels


def _hash_fraction(salt: str, uid: str) -> float:
    digest = hashlib.sha256(f"{salt}:{uid}".encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big")
    return value / 2**64


def hash_split(unit_ids: pd.Series, groups: dict[str, float], salt: str) -> pd.Series:
    """Детерминированный сплит по sha256(salt + unit_id): не гарантирует баланс страт."""
    names = list(groups.keys())
    boundaries = np.cumsum([groups[name] for name in names])
    boundaries[-1] = 1.0  # защита от погрешности округления чисел с плавающей точкой

    fractions = unit_ids.astype(str).map(lambda uid: _hash_fraction(salt, uid))
    idx = np.searchsorted(boundaries, fractions.to_numpy(), side="right")
    idx = np.clip(idx, 0, len(names) - 1)
    return pd.Series(np.array(names, dtype=object)[idx], index=unit_ids.index, name="group")


def split(
    data: pd.DataFrame,
    unit_col: str,
    groups: dict[str, float],
    method: Literal["simple", "stratified", "hash"],
    seed: int | None = None,
    stratum: pd.Series | None = None,
    salt: str | None = None,
) -> SplitResult:
    """Единая точка входа для сплитования согласно DesignConfig.split_method."""
    unit_ids = data[unit_col]
    warnings: list[str] = []

    if method == "simple":
        group = simple_split(unit_ids, groups, seed=seed if seed is not None else 0)
        return SplitResult(group=group, salt=None, warnings=warnings)

    if method == "stratified":
        if stratum is None:
            raise ValueError("Для stratified-сплита нужна колонка stratum")
        group = stratified_split(unit_ids, stratum, groups, seed=seed if seed is not None else 0)
        return SplitResult(group=group, salt=None, warnings=warnings)

    if method == "hash":
        used_salt = salt or generate_salt()
        group = hash_split(unit_ids, groups, salt=used_salt)
        if stratum is not None and stratum.nunique() > 1:
            warnings.append(
                "hash-сплит не гарантирует баланс страт: рекомендуется проверить "
                "chi-square таблицы stratum x group после сплита"
            )
        return SplitResult(group=group, salt=used_salt, warnings=warnings)

    raise ValueError(f"Неизвестный метод сплитования: {method}")
