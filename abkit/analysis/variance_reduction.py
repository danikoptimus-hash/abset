"""Снижение дисперсии: CUPED (variance_reduction-шаг) и PostStratification (test-шаг)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

from abkit.analysis.results import TestResult
from abkit.pipeline import MetricContext, Step, method_display_name


class CUPED(Step):
    """CUPED: Y' = Y - theta*(X_pre - mean(X_pre)), theta = cov(Y,X_pre)/var(X_pre).

    theta считается на объединенных данных control+treatment (не по отдельности),
    иначе поправка сама вносит смещение между группами.
    """

    stage = "variance_reduction"

    def apply(self, ctx: MetricContext) -> MetricContext:
        if ctx.covariate is None:
            # Regression (found while chasing ref edb716f1): metric.pre_col is a
            # design-time DECLARATION, not a guarantee about every future
            # analysis run's data — a post-period file that lacks that column
            # (wrong export, or the pre-period simply isn't tracked yet) used
            # to raise here uncaught, crashing the whole designed pipeline
            # (unlike compare_methods' alt chains, which already tolerate a
            # per-chain failure) into an opaque "Internal processing error".
            # Same graceful-degradation shape as the var_x == 0 case below:
            # skip the correction, let the rest of the chain run on raw values.
            ctx.warnings.append(
                "CUPED: pre-period covariate column is missing from this data — correction not applied"
            )
            return ctx

        covariate = ctx.covariate.copy()
        n_missing = int(covariate.isna().sum())
        if n_missing > 0:
            mean_impute = covariate.mean()
            covariate = covariate.fillna(mean_impute)
            frac = n_missing / len(covariate)
            ctx.warnings.append(
                f"CUPED: {n_missing} missing covariate values ({frac:.1%}) filled with the mean"
            )

        values = ctx.values
        mean_x = float(covariate.mean())
        var_x = float(covariate.var(ddof=1))
        if var_x == 0:
            ctx.warnings.append("CUPED: covariate variance is zero, correction not applied")
            return ctx

        cov_xy = float(np.cov(values.to_numpy(), covariate.to_numpy(), ddof=1)[0, 1])
        theta = cov_xy / var_x

        adjusted = values - theta * (covariate - mean_x)

        var_before = float(values.var(ddof=1))
        var_after = float(adjusted.var(ddof=1))
        ctx.variance_reduction = (1 - var_after / var_before) if var_before > 0 else None
        # rho: correlation between the metric and its pre-period covariate —
        # variance_reduction ≈ rho² (Deng et al. 2013's CUPED theta is the
        # regression coefficient, and 1 - var_after/var_before reduces to
        # rho² for this theta), shown to users as an interpretable diagnostic
        # of how good the covariate is, separate from the achieved reduction.
        ctx.cuped_rho = (cov_xy / (var_x * var_before) ** 0.5) if var_before > 0 else None

        ctx.values = adjusted
        return ctx


class PostStratification(Step):
    """Стратифицированная оценка эффекта: взвешенная по стратам разность средних.

    Полноценный test-шаг (не преобразует значения для другого теста): использует
    формулу дисперсии стратифицированной оценки напрямую.
    """

    stage = "test"

    def apply(self, ctx: MetricContext) -> MetricContext:
        if ctx.stratum is None:
            raise ValueError("PostStratification requires ctx.stratum; it is not set")

        df = pd.DataFrame({"value": ctx.values, "group": ctx.group, "stratum": ctx.stratum})
        control_df = df[df["group"] == ctx.control_name]
        treat_df = df[df["group"] == ctx.treatment_name]
        n_total = len(df)

        effect = 0.0
        variance = 0.0
        weighted_mean_control = 0.0
        n_control_total = 0
        n_treat_total = 0
        skipped = 0

        for s in sorted(df["stratum"].unique(), key=str):
            c = control_df.loc[control_df["stratum"] == s, "value"]
            t = treat_df.loc[treat_df["stratum"] == s, "value"]
            if len(c) < 2 or len(t) < 2:
                skipped += 1
                continue
            w = (len(c) + len(t)) / n_total
            effect += w * (t.mean() - c.mean())
            variance += w**2 * (t.var(ddof=1) / len(t) + c.var(ddof=1) / len(c))
            weighted_mean_control += w * c.mean()
            n_control_total += len(c)
            n_treat_total += len(t)

        if skipped:
            ctx.warnings.append(
                f"PostStratification: {skipped} stratum/strata skipped (fewer than 2 observations per group)"
            )

        se = variance**0.5
        z_crit = sp_stats.norm.ppf(1 - ctx.alpha / 2)
        p_value = 2 * (1 - sp_stats.norm.cdf(abs(effect / se))) if se > 0 else 1.0

        ci_abs = (effect - z_crit * se, effect + z_crit * se)
        effect_rel = effect / weighted_mean_control if weighted_mean_control != 0 else float("nan")
        se_rel = se / abs(weighted_mean_control) if weighted_mean_control != 0 else float("nan")
        ci_rel = (effect_rel - z_crit * se_rel, effect_rel + z_crit * se_rel)

        ctx.result = TestResult(
            metric=ctx.metric_name,
            method=method_display_name(ctx, "Post-stratification"),
            effect_abs=float(effect),
            effect_rel=float(effect_rel),
            ci_abs=(float(ci_abs[0]), float(ci_abs[1])),
            ci_rel=(float(ci_rel[0]), float(ci_rel[1])),
            p_value=float(p_value),
            p_value_adjusted=None,
            n={ctx.control_name: n_control_total, ctx.treatment_name: n_treat_total},
            n_removed=dict(ctx.n_removed),
            variance_reduction=ctx.variance_reduction,
            cuped_rho=ctx.cuped_rho,
            warnings=list(ctx.warnings),
            is_designed_method=ctx.is_designed_method,
            treatment_group=ctx.treatment_name,
            role=ctx.role,
        )
        return ctx
