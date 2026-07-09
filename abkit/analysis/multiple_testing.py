"""Поправки на множественную проверку гипотез."""

from __future__ import annotations

import numpy as np


def bonferroni(p_values: list[float]) -> list[float]:
    m = len(p_values)
    return [min(p * m, 1.0) for p in p_values]


def holm(p_values: list[float]) -> list[float]:
    """Пошаговая поправка Холма-Бонферрони."""
    m = len(p_values)
    order = np.argsort(p_values, kind="stable")
    adjusted = np.empty(m)
    running_max = 0.0
    for rank, idx in enumerate(order):
        factor = m - rank
        val = min(p_values[idx] * factor, 1.0)
        running_max = max(running_max, val)
        adjusted[idx] = running_max
    return adjusted.tolist()


def benjamini_hochberg(p_values: list[float]) -> list[float]:
    """Поправка Бенджамини-Хохберга (контроль FDR)."""
    m = len(p_values)
    order = np.argsort(p_values, kind="stable")[::-1]  # от наибольшего p-value к наименьшему
    adjusted = np.empty(m)
    running_min = 1.0
    for rank, idx in enumerate(order):
        i = m - rank  # ранг при сортировке по возрастанию (1..m)
        val = min(p_values[idx] * m / i, 1.0)
        running_min = min(running_min, val)
        adjusted[idx] = running_min
    return adjusted.tolist()


_METHODS = {
    "bonferroni": bonferroni,
    "holm": holm,
    "bh": benjamini_hochberg,
    "benjamini-hochberg": benjamini_hochberg,
}


def adjust_p_values(p_values: list[float], method: str = "holm") -> list[float]:
    """Применяет поправку на множественность к списку p-value."""
    if method not in _METHODS:
        raise ValueError(
            f"Unknown correction method: '{method}'. Available: {', '.join(_METHODS)}"
        )
    if not p_values:
        return []
    return _METHODS[method](p_values)
