"""Статистические критерии сравнения групп (test-шаги пайплайна)."""

from __future__ import annotations

import os
from typing import Literal

import numpy as np
from scipy import stats as sp_stats
from statsmodels.stats.proportion import proportions_ztest

from abkit.analysis.results import TestResult
from abkit.design.power import delta_method_variance
from abkit.pipeline import MetricContext, Step, method_display_name


def _welch_df(var_control: float, n_control: int, var_treat: float, n_treat: int) -> float:
    se_control = var_control / n_control
    se_treat = var_treat / n_treat
    numerator = (se_control + se_treat) ** 2
    denominator = se_control**2 / (n_control - 1) + se_treat**2 / (n_treat - 1)
    return numerator / denominator


class WelchTTest(Step):
    """Двухвыборочный t-тест Уэлча (неравные дисперсии) для continuous-метрик."""

    stage = "test"

    def apply(self, ctx: MetricContext) -> MetricContext:
        control_vals = ctx.values[ctx.group == ctx.control_name].dropna()
        treat_vals = ctx.values[ctx.group == ctx.treatment_name].dropna()
        n_control, n_treat = len(control_vals), len(treat_vals)
        if n_control < 2 or n_treat < 2:
            raise ValueError("Not enough observations for Welch t-test (need at least 2 per group)")

        mean_control, mean_treat = float(control_vals.mean()), float(treat_vals.mean())
        var_control, var_treat = float(control_vals.var(ddof=1)), float(treat_vals.var(ddof=1))

        effect_abs = mean_treat - mean_control
        effect_rel = effect_abs / mean_control if mean_control != 0 else float("nan")

        _stat, p_value = sp_stats.ttest_ind(treat_vals, control_vals, equal_var=False)

        se = np.sqrt(var_control / n_control + var_treat / n_treat)
        df = _welch_df(var_control, n_control, var_treat, n_treat)
        t_crit = sp_stats.t.ppf(1 - ctx.alpha / 2, df)

        ci_abs = (effect_abs - t_crit * se, effect_abs + t_crit * se)
        se_rel = se / abs(mean_control) if mean_control != 0 else float("nan")
        ci_rel = (effect_rel - t_crit * se_rel, effect_rel + t_crit * se_rel)

        ctx.result = TestResult(
            metric=ctx.metric_name,
            method=method_display_name(ctx, "Welch t-test"),
            effect_abs=float(effect_abs),
            effect_rel=float(effect_rel),
            ci_abs=(float(ci_abs[0]), float(ci_abs[1])),
            ci_rel=(float(ci_rel[0]), float(ci_rel[1])),
            p_value=float(p_value),
            p_value_adjusted=None,
            n={ctx.control_name: n_control, ctx.treatment_name: n_treat},
            n_removed=dict(ctx.n_removed),
            variance_reduction=ctx.variance_reduction,
            cuped_rho=ctx.cuped_rho,
            warnings=list(ctx.warnings),
            is_designed_method=ctx.is_designed_method,
            treatment_group=ctx.treatment_name,
            role=ctx.role,
        )
        return ctx


def _hodges_lehmann_shift(
    control: np.ndarray, treat: np.ndarray, alpha: float
) -> tuple[float, tuple[float, float]]:
    """HL-сдвиг (медиана попарных разностей treat-control) + приближенный ДИ."""
    n_control, n_treat = len(control), len(treat)
    true_n_pairs = n_control * n_treat
    max_pairs = 5_000_000

    if true_n_pairs > max_pairs:
        rng = np.random.default_rng(0)
        idx_treat = rng.integers(0, n_treat, size=max_pairs)
        idx_control = rng.integers(0, n_control, size=max_pairs)
        diffs = np.sort(treat[idx_treat] - control[idx_control])
    else:
        diffs = np.sort(np.subtract.outer(treat, control).ravel())

    n_diffs = len(diffs)
    hl_estimate = float(np.median(diffs))

    mean_u = true_n_pairs / 2
    var_u = n_control * n_treat * (n_control + n_treat + 1) / 12
    z = sp_stats.norm.ppf(1 - alpha / 2)
    c = z * np.sqrt(var_u)

    frac_lower = (mean_u - c) / true_n_pairs
    frac_upper = (mean_u + c) / true_n_pairs
    lower_idx = int(np.clip(np.floor(frac_lower * n_diffs), 0, n_diffs - 1))
    upper_idx = int(np.clip(np.ceil(frac_upper * n_diffs) - 1, 0, n_diffs - 1))
    return hl_estimate, (float(diffs[lower_idx]), float(diffs[upper_idx]))


class MannWhitney(Step):
    """Критерий Манна-Уитни; оценка эффекта — сдвиг Ходжеса-Лемана (не разность средних)."""

    stage = "test"

    def apply(self, ctx: MetricContext) -> MetricContext:
        control_vals = ctx.values[ctx.group == ctx.control_name].dropna().to_numpy()
        treat_vals = ctx.values[ctx.group == ctx.treatment_name].dropna().to_numpy()
        n_control, n_treat = len(control_vals), len(treat_vals)
        if n_control < 1 or n_treat < 1:
            raise ValueError("Not enough observations for Mann-Whitney")

        _stat, p_value = sp_stats.mannwhitneyu(treat_vals, control_vals, alternative="two-sided")
        hl_estimate, ci_abs = _hodges_lehmann_shift(control_vals, treat_vals, ctx.alpha)

        mean_control = float(control_vals.mean())
        effect_rel = hl_estimate / mean_control if mean_control != 0 else float("nan")
        ci_rel = (
            (ci_abs[0] / mean_control, ci_abs[1] / mean_control)
            if mean_control != 0
            else (float("nan"), float("nan"))
        )

        ctx.result = TestResult(
            metric=ctx.metric_name,
            method=method_display_name(ctx, "Mann-Whitney (Hodges-Lehmann)"),
            effect_abs=float(hl_estimate),
            effect_rel=float(effect_rel),
            ci_abs=(float(ci_abs[0]), float(ci_abs[1])),
            ci_rel=(float(ci_rel[0]), float(ci_rel[1])),
            p_value=float(p_value),
            p_value_adjusted=None,
            n={ctx.control_name: n_control, ctx.treatment_name: n_treat},
            n_removed=dict(ctx.n_removed),
            variance_reduction=ctx.variance_reduction,
            cuped_rho=ctx.cuped_rho,
            warnings=list(ctx.warnings)
            + ["Effect estimate is the Hodges-Lehmann median shift, not a difference of means"],
            is_designed_method=ctx.is_designed_method,
            treatment_group=ctx.treatment_name,
            role=ctx.role,
        )
        return ctx


def _bootstrap_p_value(boot_diffs: np.ndarray) -> float:
    p_left = float(np.mean(boot_diffs <= 0))
    p_right = float(np.mean(boot_diffs >= 0))
    return min(1.0, 2 * min(p_left, p_right))


def _bca_ci(
    boot_diffs: np.ndarray,
    observed: float,
    control_vals: np.ndarray,
    treat_vals: np.ndarray,
    alpha: float,
) -> tuple[float, float]:
    n_control, n_treat = len(control_vals), len(treat_vals)
    mean_control, mean_treat = control_vals.mean(), treat_vals.mean()

    jack_control = (control_vals.sum() - control_vals) / (n_control - 1)
    theta_jack_control = mean_treat - jack_control
    jack_treat = (treat_vals.sum() - treat_vals) / (n_treat - 1)
    theta_jack_treat = jack_treat - mean_control

    theta_jack = np.concatenate([theta_jack_control, theta_jack_treat])
    theta_bar = theta_jack.mean()

    num = np.sum((theta_bar - theta_jack) ** 3)
    den = 6 * (np.sum((theta_bar - theta_jack) ** 2) ** 1.5)
    a = num / den if den != 0 else 0.0

    eps = 1e-6
    p0 = np.clip(np.mean(boot_diffs < observed), eps, 1 - eps)
    z0 = sp_stats.norm.ppf(p0)

    z_lo = sp_stats.norm.ppf(alpha / 2)
    z_hi = sp_stats.norm.ppf(1 - alpha / 2)

    def _adjust(z_val: float) -> float:
        denom = 1 - a * (z0 + z_val)
        if denom == 0:
            denom = eps
        return z0 + (z0 + z_val) / denom

    alpha_lo = np.clip(sp_stats.norm.cdf(_adjust(z_lo)), eps, 1 - eps)
    alpha_hi = np.clip(sp_stats.norm.cdf(_adjust(z_hi)), eps, 1 - eps)

    lo, hi = np.percentile(boot_diffs, [alpha_lo * 100, alpha_hi * 100])
    return float(lo), float(hi)


def _default_bootstrap_batch_size() -> int:
    return int(os.environ.get("ABKIT_BOOTSTRAP_BATCH", "500"))


class Bootstrap(Step):
    """Векторизованный bootstrap разности средних (percentile или BCa).

    Ресэмплы генерируются и агрегируются батчами (ABKIT_BOOTSTRAP_BATCH,
    по умолчанию 500 итераций), а не единой матрицей n_boot x n_units:
    при большом числе юзеров на группу (сотни тысяч) полная матрица индексов
    ресэмплинга занимает гигабайты (n_boot * n_units * 8 байт на int64-
    индексы, и еще столько же на fancy-indexed значения) и валит процесс
    OOM — на 140k юзеров/группу и n_boot=10000 пик памяти без батчинга
    ~45 GB. Батчинг ограничивает пик O(batch_size * n_units) вместо
    O(n_boot * n_units); финальный boot_diffs (размера n_boot) — единственное,
    что нужно для p-value и CI, и он тривиально мал."""

    stage = "test"

    def __init__(
        self,
        n_boot: int = 10_000,
        method: Literal["bca", "percentile"] = "bca",
        seed: int | None = None,
        batch_size: int | None = None,
    ):
        if method not in ("bca", "percentile"):
            raise ValueError("method must be 'bca' or 'percentile'")
        self.n_boot = n_boot
        self.method = method
        self.seed = seed
        self.batch_size = batch_size

    def apply(self, ctx: MetricContext) -> MetricContext:
        control_vals = ctx.values[ctx.group == ctx.control_name].dropna().to_numpy()
        treat_vals = ctx.values[ctx.group == ctx.treatment_name].dropna().to_numpy()
        n_control, n_treat = len(control_vals), len(treat_vals)
        if n_control < 2 or n_treat < 2:
            raise ValueError("Not enough observations for Bootstrap")

        rng = np.random.default_rng(self.seed)
        mean_control = float(control_vals.mean())
        effect_abs = float(treat_vals.mean() - mean_control)
        effect_rel = effect_abs / mean_control if mean_control != 0 else float("nan")

        batch_size = self.batch_size or _default_bootstrap_batch_size()
        boot_diffs = np.empty(self.n_boot, dtype=np.float64)
        for start in range(0, self.n_boot, batch_size):
            n = min(batch_size, self.n_boot - start)
            control_idx = rng.integers(0, n_control, size=(n, n_control))
            treat_idx = rng.integers(0, n_treat, size=(n, n_treat))
            boot_diffs[start : start + n] = (
                treat_vals[treat_idx].mean(axis=1) - control_vals[control_idx].mean(axis=1)
            )

        p_value = _bootstrap_p_value(boot_diffs)

        if self.method == "percentile":
            lo, hi = np.percentile(boot_diffs, [ctx.alpha / 2 * 100, (1 - ctx.alpha / 2) * 100])
        else:
            lo, hi = _bca_ci(boot_diffs, effect_abs, control_vals, treat_vals, ctx.alpha)

        ci_rel = (lo / mean_control, hi / mean_control) if mean_control != 0 else (float("nan"), float("nan"))

        ctx.result = TestResult(
            metric=ctx.metric_name,
            method=method_display_name(ctx, f"Bootstrap ({self.method})"),
            effect_abs=effect_abs,
            effect_rel=float(effect_rel),
            ci_abs=(float(lo), float(hi)),
            ci_rel=(float(ci_rel[0]), float(ci_rel[1])),
            p_value=float(p_value),
            p_value_adjusted=None,
            n={ctx.control_name: n_control, ctx.treatment_name: n_treat},
            n_removed=dict(ctx.n_removed),
            variance_reduction=ctx.variance_reduction,
            cuped_rho=ctx.cuped_rho,
            warnings=list(ctx.warnings),
            is_designed_method=ctx.is_designed_method,
            treatment_group=ctx.treatment_name,
            role=ctx.role,
        )
        return ctx


class DeltaMethodTTest(Step):
    """t-тест для ratio-метрик: дисперсия отношения num/den дельта-методом.

    Обязателен, когда единица анализа (например, сессия) не совпадает с единицей
    рандомизации (юзер): наивный тест по строкам недооценивает дисперсию.
    """

    stage = "test"

    def apply(self, ctx: MetricContext) -> MetricContext:
        if ctx.num is None or ctx.den is None:
            raise ValueError("DeltaMethodTTest requires ctx.num and ctx.den (ratio metric)")

        control_mask = ctx.group == ctx.control_name
        treat_mask = ctx.group == ctx.treatment_name
        num_c, den_c = ctx.num[control_mask], ctx.den[control_mask]
        num_t, den_t = ctx.num[treat_mask], ctx.den[treat_mask]
        n_control, n_treat = len(num_c), len(num_t)
        if n_control < 2 or n_treat < 2:
            raise ValueError("Not enough observations for DeltaMethodTTest")

        ratio_c, var_unit_c = delta_method_variance(num_c, den_c)
        ratio_t, var_unit_t = delta_method_variance(num_t, den_t)

        se_c_sq = var_unit_c / n_control
        se_t_sq = var_unit_t / n_treat
        se = (se_c_sq + se_t_sq) ** 0.5

        effect_abs = ratio_t - ratio_c
        effect_rel = effect_abs / ratio_c if ratio_c != 0 else float("nan")

        if se > 0:
            df = (se_c_sq + se_t_sq) ** 2 / (
                se_c_sq**2 / (n_control - 1) + se_t_sq**2 / (n_treat - 1)
            )
            t_stat = effect_abs / se
            p_value = 2 * (1 - sp_stats.t.cdf(abs(t_stat), df))
            t_crit = sp_stats.t.ppf(1 - ctx.alpha / 2, df)
        else:
            p_value = 1.0
            t_crit = 0.0

        ci_abs = (effect_abs - t_crit * se, effect_abs + t_crit * se)
        se_rel = se / abs(ratio_c) if ratio_c != 0 else float("nan")
        ci_rel = (effect_rel - t_crit * se_rel, effect_rel + t_crit * se_rel)

        ctx.result = TestResult(
            metric=ctx.metric_name,
            method=method_display_name(ctx, "Delta method (ratio)"),
            effect_abs=float(effect_abs),
            effect_rel=float(effect_rel),
            ci_abs=(float(ci_abs[0]), float(ci_abs[1])),
            ci_rel=(float(ci_rel[0]), float(ci_rel[1])),
            p_value=float(p_value),
            p_value_adjusted=None,
            n={ctx.control_name: n_control, ctx.treatment_name: n_treat},
            n_removed=dict(ctx.n_removed),
            variance_reduction=ctx.variance_reduction,
            cuped_rho=ctx.cuped_rho,
            warnings=list(ctx.warnings),
            is_designed_method=ctx.is_designed_method,
            treatment_group=ctx.treatment_name,
            role=ctx.role,
        )
        return ctx


class ZTestProportions(Step):
    """Двухвыборочный z-тест пропорций для binary-метрик."""

    stage = "test"

    def apply(self, ctx: MetricContext) -> MetricContext:
        control_vals = ctx.values[ctx.group == ctx.control_name].dropna()
        treat_vals = ctx.values[ctx.group == ctx.treatment_name].dropna()
        n_control, n_treat = len(control_vals), len(treat_vals)
        if n_control < 1 or n_treat < 1:
            raise ValueError("Not enough observations for Z-test of proportions")

        x_control, x_treat = float(control_vals.sum()), float(treat_vals.sum())
        p_control, p_treat = x_control / n_control, x_treat / n_treat

        effect_abs = p_treat - p_control
        effect_rel = effect_abs / p_control if p_control != 0 else float("nan")

        _stat, p_value = proportions_ztest([x_treat, x_control], [n_treat, n_control])

        se = np.sqrt(p_control * (1 - p_control) / n_control + p_treat * (1 - p_treat) / n_treat)
        z_crit = sp_stats.norm.ppf(1 - ctx.alpha / 2)
        ci_abs = (effect_abs - z_crit * se, effect_abs + z_crit * se)
        se_rel = se / abs(p_control) if p_control != 0 else float("nan")
        ci_rel = (effect_rel - z_crit * se_rel, effect_rel + z_crit * se_rel)

        ctx.result = TestResult(
            metric=ctx.metric_name,
            method=method_display_name(ctx, "Z-test of proportions"),
            effect_abs=float(effect_abs),
            effect_rel=float(effect_rel),
            ci_abs=(float(ci_abs[0]), float(ci_abs[1])),
            ci_rel=(float(ci_rel[0]), float(ci_rel[1])),
            p_value=float(p_value),
            p_value_adjusted=None,
            n={ctx.control_name: n_control, ctx.treatment_name: n_treat},
            n_removed=dict(ctx.n_removed),
            variance_reduction=ctx.variance_reduction,
            cuped_rho=ctx.cuped_rho,
            warnings=list(ctx.warnings),
            is_designed_method=ctx.is_designed_method,
            treatment_group=ctx.treatment_name,
            role=ctx.role,
        )
        return ctx
