"""Preprocess-шаги: обрезка и винзоризация выбросов, лог-преобразование."""

from __future__ import annotations

import numpy as np

from abkit.pipeline import MetricContext, Step


class RemoveOutliers(Step):
    """Удаляет наблюдения вне [lower_q, upper_q] квантилей объединенных данных.

    Порог считается на данных control+treatment вместе и применяется одинаково
    к обеим группам (не допускает утечки информации о группе в порог).
    """

    stage = "preprocess"

    def __init__(self, lower_q: float = 0.0, upper_q: float = 0.99):
        if not 0 <= lower_q < upper_q <= 1:
            raise ValueError("Requires 0 <= lower_q < upper_q <= 1")
        self.lower_q = lower_q
        self.upper_q = upper_q

    def apply(self, ctx: MetricContext) -> MetricContext:
        combined = ctx.values.dropna()
        lo = combined.quantile(self.lower_q) if self.lower_q > 0 else -np.inf
        hi = combined.quantile(self.upper_q) if self.upper_q < 1 else np.inf

        keep_mask = (ctx.values >= lo) & (ctx.values <= hi)
        removed_by_group = (
            (~keep_mask).groupby(ctx.group, observed=True).sum().astype(int).to_dict()
        )
        for name, n in removed_by_group.items():
            ctx.n_removed[name] = ctx.n_removed.get(name, 0) + int(n)

        ctx.values = ctx.values[keep_mask]
        ctx.group = ctx.group[keep_mask]
        if ctx.covariate is not None:
            ctx.covariate = ctx.covariate[keep_mask]
        if ctx.stratum is not None:
            ctx.stratum = ctx.stratum[keep_mask]
        return ctx


class Winsorize(Step):
    """Обрезает (clip) значения по квантилям объединенных данных вместо удаления."""

    stage = "preprocess"

    def __init__(self, lower_q: float = 0.0, upper_q: float = 0.99):
        if not 0 <= lower_q < upper_q <= 1:
            raise ValueError("Requires 0 <= lower_q < upper_q <= 1")
        self.lower_q = lower_q
        self.upper_q = upper_q

    def apply(self, ctx: MetricContext) -> MetricContext:
        combined = ctx.values.dropna()
        lo = combined.quantile(self.lower_q)
        hi = combined.quantile(self.upper_q)

        affected_mask = (ctx.values < lo) | (ctx.values > hi)
        affected_by_group = (
            affected_mask.groupby(ctx.group, observed=True).sum().astype(int).to_dict()
        )
        for name, n in affected_by_group.items():
            ctx.n_removed[name] = ctx.n_removed.get(name, 0) + int(n)

        ctx.values = ctx.values.clip(lower=lo, upper=hi)
        return ctx


class Log1p(Step):
    """log(1+x) преобразование метрики (для скошенных неотрицательных метрик)."""

    stage = "preprocess"

    def apply(self, ctx: MetricContext) -> MetricContext:
        if (ctx.values.dropna() < 0).any():
            raise ValueError("Log1p requires non-negative metric values")
        ctx.values = np.log1p(ctx.values)
        return ctx
