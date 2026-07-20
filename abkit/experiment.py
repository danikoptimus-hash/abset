"""Experiment: сборка полного цикла дизайна (и, в дальнейшем, анализа) A/B теста."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np
import pandas as pd

from abkit import checks, storage
from abkit.analysis.multiple_testing import adjust_p_values
from abkit.analysis.results import AnalysisResults, TestResult
from abkit.analysis.tests import (
    Bootstrap,
    ChiSquareTest,
    DeltaMethodTTest,
    MannWhitney,
    WelchTTest,
    ZTestProportions,
)
from abkit.analysis.variance_reduction import CUPED, PostStratification
from abkit.config import DesignConfig, MetricConfig
from abkit.design import isolation, power
from abkit.design.splitter import split as run_split
from abkit.design.stratification import bucket_column, build_strata, nan_counts_by_column
from abkit.idnorm import normalize_id_series
from abkit.pipeline import MetricContext, Pipeline, Step
from abkit.preprocessing.outliers import RemoveOutliers, Winsorize


class DesignError(Exception):
    """Пользовательская ошибка на этапе дизайна (некорректные входные данные/конфиг)."""


_STEP_REGISTRY: dict[str, type[Step]] = {
    "WelchTTest": WelchTTest,
    "ZTestProportions": ZTestProportions,
    "ChiSquareTest": ChiSquareTest,
    "DeltaMethodTTest": DeltaMethodTTest,
    "MannWhitney": MannWhitney,
    "Bootstrap": Bootstrap,
    "CUPED": CUPED,
    "PostStratification": PostStratification,
    "RemoveOutliers": RemoveOutliers,
    "Winsorize": Winsorize,
}


def _default_steps_for_metric(metric: MetricConfig) -> list[Step]:
    """Дефолтная цепочка по типу метрики: continuous -> Welch (+CUPED если есть pre_col),
    binary -> Z-тест пропорций, ratio -> дельта-метод."""
    if metric.type == "binary":
        return [ZTestProportions()]
    if metric.type == "ratio":
        return [DeltaMethodTTest()]
    if metric.pre_col:
        return [CUPED(), WelchTTest()]
    return [WelchTTest()]


def resolve_steps(
    metric: MetricConfig, methods: dict[str, list[Step]] | None, seed: int | None = None
) -> list[Step]:
    """Определяет цепочку шагов для метрики: явный methods -> default_methods -> дефолт по типу.

    seed передается в Bootstrap, если он указан в default_methods по имени — иначе
    результат analyze() не был бы воспроизводим бит-в-бит (см. DESIGN.md раздел 12).
    """
    if methods and metric.name in methods:
        return methods[metric.name]
    if metric.default_methods:
        try:
            return [
                _STEP_REGISTRY[step_name](seed=seed) if step_name == "Bootstrap" else _STEP_REGISTRY[step_name]()
                for step_name in metric.default_methods
            ]
        except KeyError as e:
            raise checks.AnalysisError(
                f"Unknown method '{e.args[0]}' in default_methods for metric '{metric.name}'"
            ) from e
    return _default_steps_for_metric(metric)


_METHOD_ID_CHAIN_BUILDERS: dict[str, Any] = {
    "welch": lambda seed: [WelchTTest()],
    "cuped_welch": lambda seed: [CUPED(), WelchTTest()],
    "mann_whitney": lambda seed: [MannWhitney()],
    "bootstrap_bca": lambda seed: [Bootstrap(method="bca", seed=seed)],
    "remove_outliers_welch": lambda seed: [RemoveOutliers(upper_q=0.99), WelchTTest()],
    "ztest": lambda seed: [ZTestProportions()],
    "chi_square": lambda seed: [ChiSquareTest()],
    "bootstrap_percentile": lambda seed: [Bootstrap(method="percentile", seed=seed)],
    "delta_method": lambda seed: [DeltaMethodTTest()],
}


def recommended_method_id(metric: MetricConfig) -> str:
    """Item 2: the type/config-based default method id for a metric — same
    rule _default_steps_for_metric() encodes as actual Step instances, kept
    here as an id so the frontend (and run_analyze's "differs from the
    designed method" warning, item 2.5) can compare a manually-picked id
    against it without re-deriving the rule. Also how "designed vs manually
    selected" is later reconstructed from results.json (item 2.3) — by
    comparing a metric's ACTUAL designed TestResult.method string against
    this id's chain, not a separately stored flag that could drift."""
    if metric.type == "binary":
        return "cuped_welch" if metric.pre_col else "ztest"
    if metric.type == "ratio":
        return "delta_method"
    return "cuped_welch" if metric.pre_col else "welch"


def steps_for_method_id(metric: MetricConfig, method_id: str, seed: int | None = None) -> list[Step]:
    """Item 2 (explicit method selection): maps a UI-facing method id to its
    Step chain — used to build the `methods` override passed to
    Experiment.analyze() when the user picks a specific method for a
    metric, instead of always the type/config-based default. Frontend
    mirror (options offered per metric type/pre_col, kept manually in sync
    — same duplication pattern as VERDICT_LABELS etc. elsewhere in this
    codebase): frontend/src/pages/experiment/methodOptions.ts."""
    builder = _METHOD_ID_CHAIN_BUILDERS.get(method_id)
    if builder is None:
        raise checks.AnalysisError(f"Unknown analysis method '{method_id}' for metric '{metric.name}'")
    return builder(seed)


def compare_methods_chains(metric: MetricConfig, seed: int | None = None) -> list[list[Step]]:
    """Стандартный набор альтернативных цепочек для устойчивости выводов (compare_methods=True).

    Изначально (DESIGN.md) — только для continuous-метрик; item 3 расширяет
    набор на binary. Ratio по-прежнему не покрыт (тот же вопрос, не в
    скоупе item 3). Сами по себе альтернативы не влияют на вердикт
    (is_designed_method=False) — решение принимается только designed-цепочкой.
    seed передается в Bootstrap для воспроизводимости (повторный analyze() на
    тех же данных должен давать бит-в-бит тот же results.json).
    """
    if metric.type == "continuous":
        chains: list[list[Step]] = [
            [WelchTTest()],
            [RemoveOutliers(upper_q=0.99), WelchTTest()],
            [Bootstrap(method="bca", seed=seed)],
            [MannWhitney()],
        ]
        if metric.pre_col:
            chains.append([CUPED(), WelchTTest()])
        return chains
    if metric.type == "binary":
        # Mann-Whitney and outlier-removal methods are deliberately NOT
        # included: on a 0/1 series Mann-Whitney degenerates into (a
        # noisier, rank-based restatement of) the same proportion
        # comparison the designed Z-test already makes, and there are no
        # "outliers" to remove from a binary indicator — both would be
        # cross-checks in name only, not in substance.
        chains = [
            # Same 2x2 table as the designed Z-test, computed independently
            # via scipy's chi-square machinery — a real cross-check of the
            # implementation, not just of the statistical assumption.
            [ChiSquareTest()],
            # Percentile (not BCa): BCa's bias-correction leans on a
            # jackknife influence-function estimate that gets noisy near
            # the 0/1 boundary (proportions close to 0 or 1) — percentile
            # is the simpler, more robust choice for a bounded outcome.
            [Bootstrap(method="percentile", seed=seed)],
        ]
        if metric.pre_col:
            # Same CUPED mechanics as continuous — the covariate adjustment
            # doesn't care that the outcome itself is 0/1, and Welch t-test
            # on the CUPED-adjusted values is exactly the recently-fixed
            # continuous CUPED-MDE analysis path, just applied here instead
            # of at design time.
            chains.append([CUPED(), WelchTTest()])
        return chains
    return []


def _failed_method_result(
    metric: MetricConfig,
    pipeline: Pipeline,
    control_name: str,
    treat_name: str,
    n_control: int,
    n_treat: int,
    exc: Exception,
) -> TestResult:
    """Строка-заглушка для альтернативного метода (compare_methods=True),
    упавшего с исключением: designed-метод и остальные альтернативы все
    равно досчитываются (см. цикл по extra_chains в analyze()) — падение
    ОДНОГО сравнительного метода не должно валить весь анализ, только
    пометить его результат как неудачный с краткой причиной."""
    reason = str(exc).strip() or type(exc).__name__
    if len(reason) > 200:
        reason = reason[:200] + "..."
    nan = float("nan")
    return TestResult(
        metric=metric.name,
        method=f"{pipeline.method_name} (failed)",
        effect_abs=nan,
        effect_rel=nan,
        ci_abs=(nan, nan),
        ci_rel=(nan, nan),
        p_value=nan,
        p_value_adjusted=None,
        n={control_name: n_control, treat_name: n_treat},
        n_removed={},
        variance_reduction=None,
        warnings=[f"failed: {reason}"],
        is_designed_method=False,
        treatment_group=treat_name,
        role=metric.role,
    )


@dataclass
class DesignReport:
    """Сводка по этапу дизайна: доступность кандидатов, мощность, проверки сплита."""

    n_candidates_total: int
    n_excluded_by_isolation: int
    n_available: int
    excluded_by_experiment: dict[str, int]
    group_sizes: dict[str, int]
    power_results: dict[str, power.PowerResult]
    srm: checks.SRMResult
    strata_balance: checks.BalanceResult
    pre_period_aa: list[checks.AAResult]
    strata_nan_counts: dict[str, int] = field(default_factory=dict)
    """Число пропусков по каждой стратификационной колонке (до применения nan_strategy)."""
    n_dropped_for_nan_strata: int = 0
    """Сколько юзеров удалено из-за пропусков в стратах (только при nan_strategy='drop')."""
    warnings: list[str] = field(default_factory=list)


def infer_control_name(groups: dict[str, float]) -> str:
    """Регистронезависимый поиск группы "control" (UI по умолчанию называет ее
    "Control") — если группы нет вовсе, откат на первую по порядку добавления."""
    for name in groups:
        if name.lower() == "control":
            return name
    return next(iter(groups))


def build_metric_context(
    metric: MetricConfig,
    merged: pd.DataFrame,
    control_name: str,
    treat_name: str,
    alpha: float,
    is_designed_method: bool = True,
) -> MetricContext:
    """Строит MetricContext для пары control/treatment из объединенных данных.

    Переиспользуется Experiment.analyze() и validation/simulation.py.
    """
    subset = merged[merged["group"].isin([control_name, treat_name])]

    if metric.type == "ratio":
        for col in (metric.num, metric.den):
            if col not in merged.columns:
                raise checks.AnalysisError(
                    f"Ratio metric '{metric.name}' needs column '{col}' in the data"
                )
        values = subset[metric.num] / subset[metric.den].replace(0, np.nan)
        num, den = subset[metric.num], subset[metric.den]
    else:
        if metric.name not in merged.columns:
            raise checks.AnalysisError(
                f"Data is missing metric column '{metric.name}' after joining with assignments"
            )
        values = subset[metric.name]
        num, den = None, None

    covariate = None
    if metric.pre_col and metric.pre_col in merged.columns:
        covariate = subset[metric.pre_col]

    stratum = subset["stratum"] if "stratum" in subset.columns else None

    return MetricContext(
        metric_name=metric.name,
        metric_type=metric.type,
        control_name=control_name,
        treatment_name=treat_name,
        values=values,
        group=subset["group"],
        alpha=alpha,
        stratum=stratum,
        covariate=covariate,
        num=num,
        den=den,
        is_designed_method=is_designed_method,
        role=metric.role,
    )


def _validate_input_data(config: DesignConfig, data: pd.DataFrame) -> None:
    if config.unit_col not in data.columns:
        raise DesignError(f"Data is missing the unit_col column '{config.unit_col}'")
    if data[config.unit_col].isna().any():
        raise DesignError(f"Column unit_col '{config.unit_col}' contains missing values")
    if data[config.unit_col].duplicated().any():
        raise DesignError(f"Column unit_col '{config.unit_col}' contains duplicates")

    for col in config.strata:
        if col not in data.columns:
            raise DesignError(f"Data is missing stratum column '{col}'")
        if config.nan_strategy == "error" and data[col].isna().any():
            raise DesignError(
                f"Stratum column '{col}' contains missing values (nan_strategy='error'). "
                "To avoid failing on missing values, use nan_strategy='separate_stratum' "
                "(default) or 'drop'."
            )

    for metric in config.metrics:
        if metric.type == "ratio":
            for col in (metric.num, metric.den):
                if col not in data.columns:
                    raise DesignError(
                        f"Ratio metric '{metric.name}' needs column '{col}', it is not in the data"
                    )
        else:
            if metric.name not in data.columns and (
                metric.pre_col is None or metric.pre_col not in data.columns
            ):
                raise DesignError(
                    f"No data to estimate variance for metric '{metric.name}': "
                    f"needs a historical column '{metric.name}' or pre_col"
                )


def metric_history_values(metric: MetricConfig, data: pd.DataFrame) -> pd.Series:
    if metric.name in data.columns:
        return data[metric.name]
    return data[metric.pre_col]


def compute_metric_baseline_mean(metric: MetricConfig, data: pd.DataFrame) -> float | None:
    """Baseline (среднее) метрики по данным дизайна — используется UI (app.py)
    для перевода абсолютного MDE в относительный. None, если посчитать нельзя
    (нужных колонок нет в данных) — вызывающая сторона должна показать
    понятную ошибку, а не упасть."""
    if metric.type == "ratio":
        if not metric.num or not metric.den:
            return None
        if metric.num not in data.columns or metric.den not in data.columns:
            return None
        mean, _variance = power.delta_method_variance(data[metric.num], data[metric.den])
        return float(mean)
    if metric.name in data.columns:
        return float(data[metric.name].mean())
    if metric.pre_col and metric.pre_col in data.columns:
        return float(data[metric.pre_col].mean())
    return None


AggMethod = Literal["sum", "max", "last", "first"]

_AGG_DEFAULT_BY_TYPE: dict[str, AggMethod] = {
    "continuous": "sum",
    "binary": "max",
    "ratio": "sum",
}


def resolve_agg_method(metric: MetricConfig, agg_methods: dict[str, AggMethod] | None) -> AggMethod:
    """Способ агрегации по дням для метрики: явный override -> дефолт по типу
    (continuous -> sum, binary -> max, ratio -> sum num и den отдельно)."""
    if agg_methods and metric.name in agg_methods:
        return agg_methods[metric.name]
    return _AGG_DEFAULT_BY_TYPE[metric.type]


def aggregate_post_data(
    data: pd.DataFrame,
    unit_col: str,
    metrics: list[MetricConfig],
    date_col: str | None,
    agg_methods: dict[str, AggMethod] | None = None,
    carry_columns: list[str] | None = None,
) -> pd.DataFrame:
    """Схлопывает данные с разбивкой по дням (юзер x день) до одной строки на юзера.

    Правило по умолчанию: continuous -> sum, binary -> max (был ли хоть один
    positive день), ratio -> sum num и sum den ОТДЕЛЬНО (деление — на уровне
    юзера, после суммирования, а не среднее подневных отношений). Можно
    переопределить per-metric через agg_methods. "last"/"first" ("последнее/первое
    значение") требуют date_col для сортировки — иначе порядок строк произвольный.
    carry_columns — дополнительные колонки, которые нужно протащить через
    агрегацию как есть (например "group"/"stratum": берутся через first(), т.к.
    константны для юзера).
    """
    sorted_data = data.sort_values(date_col) if date_col and date_col in data.columns else data

    agg_dict: dict[str, str] = {}
    for metric in metrics:
        cols = [metric.num, metric.den] if metric.type == "ratio" else [metric.name]
        method = resolve_agg_method(metric, agg_methods)
        for col in cols:
            if col and col in sorted_data.columns:
                agg_dict[col] = method
        if metric.pre_col and metric.pre_col in sorted_data.columns:
            agg_dict.setdefault(metric.pre_col, "first")

    for col in carry_columns or []:
        if col in sorted_data.columns:
            agg_dict.setdefault(col, "first")

    if not agg_dict:
        return sorted_data.drop_duplicates(subset=unit_col, keep="last")

    return sorted_data.groupby(unit_col, as_index=False, sort=False).agg(agg_dict)


def compute_power_results(
    config: DesignConfig, candidates: pd.DataFrame, control_name: str
) -> dict[str, power.PowerResult]:
    """Public (was module-private) since abkit/jobs.py::preview_sample_size
    (wizard item 3, 'Calculate sample size' — computed on isolated
    candidates BEFORE group proportions are decided, with an equal-split
    config.groups dict standing in for the real ones) now reuses this
    directly rather than duplicating the per-metric baseline/variance/
    sample-size branching logic."""
    treatment_names = [g for g in config.groups if g != control_name]
    n_comparisons = max(len(treatment_names), 1)
    control_prop = config.groups[control_name]
    avg_treatment_prop = (1 - control_prop) / n_comparisons
    ratio = avg_treatment_prop / control_prop
    alpha = config.alpha / n_comparisons if n_comparisons > 1 else config.alpha

    n_available = len(candidates)
    n_control_available = n_available * control_prop

    results: dict[str, power.PowerResult] = {}
    for metric in config.metrics:
        warnings: list[str] = []

        if metric.type == "ratio":
            mean, variance = power.delta_method_variance(
                candidates[metric.num], candidates[metric.den]
            )
            std = variance**0.5
            metric_values = candidates[metric.num] / candidates[metric.den].replace(0, np.nan)
        else:
            metric_values = metric_history_values(metric, candidates)
            mean = float(metric_values.mean())
            std = float(metric_values.std(ddof=1))

        rho = None
        if metric.pre_col and metric.pre_col in candidates.columns:
            pre_series = candidates[metric.pre_col]
            if not pre_series.equals(metric_values):
                rho = power.correlation_with_pre(metric_values, pre_series)

        result = power.PowerResult(
            metric=metric.name,
            metric_type=metric.type,
            baseline_mean=mean,
            baseline_std=std,
            rho=rho,
            metric_role=metric.role,
        )

        # Item 5 fix: a secondary metric never inherits config.mde as its own
        # target — that number is the user's typed goal for the metric(s)
        # actually driving sample size (primary), and a secondary metric has
        # its own baseline/variance. Regardless of sizeMode, report the
        # secondary metric's honest achievable MDE at the ACTUAL final
        # per-group n. run_split() always splits the full isolated candidate
        # pool — config.sample_size never actually subsamples it — so
        # n_control_available (not config.sample_size) is that true n.
        if metric.role == "secondary":
            _fill_achievable_mde(result, metric, mean, std, rho, n_control_available, alpha, config.power, ratio)
        elif config.mde is not None:
            if metric.type == "binary":
                p_treat = mean * (1 + config.mde)
                if not 0 < p_treat < 1:
                    warnings.append(
                        f"MDE {config.mde:.2%} is not achievable for baseline {mean:.4f}: "
                        "the resulting proportion is outside (0, 1)"
                    )
                else:
                    n_req = power.sample_size_binary(mean, p_treat, alpha=alpha, power=config.power, ratio=ratio)
                    result.sample_size_per_group = n_req
                    result.mde_abs = p_treat - mean
                    result.mde_rel = config.mde
                    implausible = power.implausible_sample_size_warning(
                        n_req, config.mde_abs_input, metric.type
                    )
                    if implausible:
                        warnings.append(implausible)
                    if rho is not None:
                        n_req_cuped = power.sample_size_binary_cuped(
                            mean, p_treat, rho, alpha=alpha, power=config.power, ratio=ratio
                        )
                        result.sample_size_per_group_cuped = n_req_cuped
                        result.mde_abs_cuped = p_treat - mean
                        result.mde_rel_cuped = config.mde
                        implausible_cuped = power.implausible_sample_size_warning(
                            n_req_cuped, config.mde_abs_input, metric.type
                        )
                        if implausible_cuped and not implausible:
                            warnings.append(implausible_cuped)
            else:
                mde_abs = abs(config.mde * mean) if mean != 0 else abs(config.mde)
                n_req = power.sample_size_continuous(std, mde_abs, alpha=alpha, power=config.power, ratio=ratio)
                result.sample_size_per_group = n_req
                result.mde_abs = mde_abs
                result.mde_rel = config.mde
                # Item 1 bug: unlike binary, a continuous/ratio metric has no
                # (0, 1) bound to catch a wildly mis-scaled absolute MDE
                # (e.g. a percentage typed where a fraction was expected) —
                # it silently produces an oversized effect size and hence an
                # implausibly tiny n, with nothing upstream ever raising.
                implausible = power.implausible_sample_size_warning(n_req, config.mde_abs_input, metric.type)
                if implausible:
                    warnings.append(implausible)
                if rho is not None:
                    std_cuped = std * power.cuped_variance_multiplier(rho) ** 0.5
                    n_req_cuped = power.sample_size_continuous(
                        std_cuped, mde_abs, alpha=alpha, power=config.power, ratio=ratio
                    )
                    result.sample_size_per_group_cuped = n_req_cuped
                    result.mde_abs_cuped = mde_abs
                    result.mde_rel_cuped = config.mde
                    implausible_cuped = power.implausible_sample_size_warning(
                        n_req_cuped, config.mde_abs_input, metric.type
                    )
                    if implausible_cuped and not implausible:
                        warnings.append(implausible_cuped)

            if result.sample_size_per_group is not None and result.sample_size_per_group > n_control_available:
                warnings.append(
                    f"Not enough data for the given MDE: need ~{result.sample_size_per_group:.0f} "
                    f"in the control group, {n_control_available:.0f} available"
                )
        else:
            n_control = config.sample_size * control_prop if config.sample_size else n_control_available
            _fill_achievable_mde(result, metric, mean, std, rho, n_control, alpha, config.power, ratio)

        result.warnings = warnings
        results[metric.name] = result

    # Primary metrics first (declaration order within a role) — a stable
    # sort so metrics keep their config.metrics relative order within their
    # own role; only role grouping changes. Feeds the MDE table (Design tab,
    # design_report.html) directly, so this is the single place that needs
    # to change for both to show primary rows before secondary ones.
    return dict(sorted(results.items(), key=lambda kv: kv[1].metric_role != "primary"))


def _fill_achievable_mde(
    result: power.PowerResult,
    metric: MetricConfig,
    mean: float,
    std: float,
    rho: float | None,
    n_control: float,
    alpha: float,
    power_target: float,
    ratio: float,
) -> None:
    """Achievable-MDE-at-fixed-n: the minimum relative/absolute effect
    detectable for `metric` with its OWN baseline/variance, given n_control
    already fixed by something else (primary-metric sample size, or actual
    available data). Shared by the config.mde-is-None sizing path and by
    every secondary metric (item 5) — those two callers previously
    duplicated this formula, and the secondary-metric path used to be
    missing entirely (secondary metrics wrongly inherited config.mde)."""
    if metric.type == "binary":
        mde_delta = power.mde_binary(mean, n_control, alpha=alpha, power=power_target, ratio=ratio)
        result.sample_size_per_group = n_control
        result.mde_abs = mde_delta
        result.mde_rel = mde_delta / mean if mean else None
        if rho is not None:
            mde_delta_cuped = power.mde_binary_cuped(
                mean, rho, n_control, alpha=alpha, power=power_target, ratio=ratio
            )
            result.sample_size_per_group_cuped = n_control
            result.mde_abs_cuped = mde_delta_cuped
            result.mde_rel_cuped = mde_delta_cuped / mean if mean else None
    else:
        mde_abs = power.mde_continuous(std, n_control, alpha=alpha, power=power_target, ratio=ratio)
        result.sample_size_per_group = n_control
        result.mde_abs = mde_abs
        result.mde_rel = mde_abs / mean if mean else None
        if rho is not None:
            std_cuped = std * power.cuped_variance_multiplier(rho) ** 0.5
            mde_abs_cuped = power.mde_continuous(std_cuped, n_control, alpha=alpha, power=power_target, ratio=ratio)
            result.sample_size_per_group_cuped = n_control
            result.mde_abs_cuped = mde_abs_cuped
            result.mde_rel_cuped = mde_abs_cuped / mean if mean else None


def _power_results_to_dict(results: dict[str, power.PowerResult]) -> dict[str, Any]:
    return {
        name: {
            "metric_type": r.metric_type,
            "baseline_mean": r.baseline_mean,
            "baseline_std": r.baseline_std,
            "mde_abs": r.mde_abs,
            "mde_rel": r.mde_rel,
            "sample_size_per_group": r.sample_size_per_group,
            "rho": r.rho,
            "mde_abs_cuped": r.mde_abs_cuped,
            "mde_rel_cuped": r.mde_rel_cuped,
            "sample_size_per_group_cuped": r.sample_size_per_group_cuped,
            "warnings": r.warnings,
            "metric_role": r.metric_role,
        }
        for name, r in results.items()
    }


@dataclass
class StratumPowerRow:
    """Item 2 (strata power check): one (dimension, stratum value,
    treatment group, metric) combination — achievable MDE INSIDE that
    stratum alone, at the CURRENT (already-chosen) group proportions.
    Distinct from compute_power_results, which never looks inside strata."""

    stratum: str
    treatment_group: str
    metric: str
    n_control: int
    n_treatment: int
    mde_rel: float | None
    mde_rel_cuped: float | None
    status: Literal["ok", "weak", "insufficient"]


# Below this per-stratum-per-group n, an achievable-MDE number is more noise
# than signal (variance/rho estimates on <20 points are unstable) — flagged
# "insufficient" outright rather than shown as a real (if huge) MDE value.
# Same floor build_strata() already uses to merge small strata into
# "_other_" for the real split (abkit/design/stratification.py), reused
# here for the same "too small to say anything" reasoning.
MIN_STRATUM_N_FOR_POWER_CHECK = 20

# "ok" if the stratum's MDE is within 2x the OVERALL (whole-experiment)
# achievable MDE for that metric — i.e. segment-level analysis on this
# stratum can detect an effect not much larger than what the main analysis
# can. Not a statistical standard, just the threshold item 2 asked for.
WEAK_STRATUM_MDE_MULTIPLIER = 2.0


def _stratum_status(
    n_control: int, n_treatment: int, mde_rel: float | None, overall_mde_rel: float | None
) -> Literal["ok", "weak", "insufficient"]:
    if n_control < MIN_STRATUM_N_FOR_POWER_CHECK or n_treatment < MIN_STRATUM_N_FOR_POWER_CHECK:
        return "insufficient"
    if mde_rel is None or overall_mde_rel is None or overall_mde_rel == 0:
        return "insufficient"
    if abs(mde_rel) > WEAK_STRATUM_MDE_MULTIPLIER * abs(overall_mde_rel):
        return "weak"
    return "ok"


def compute_strata_power_rows(
    candidates: pd.DataFrame,
    control_name: str,
    groups: dict[str, float],
    primary_metrics: list[MetricConfig],
    strata_cols: list[str],
    overall_mde_rel: dict[str, float],
    alpha: float,
    power_target: float,
    n_buckets_continuous: int = 4,
) -> dict[str, list[StratumPowerRow]]:
    """Item 2 (strata power check, wizard Parameters step, after proportions
    are set): for each stratification DIMENSION individually (e.g. "gender"
    alone, "country" alone) and, if there's more than one, their COMBINATION
    (e.g. "gender × country") — per stratum value, the sample size that
    value actually gets at the CURRENT group proportions, and the MDE
    achievable from just that subset. Scoped to primary metrics only (a
    secondary metric doesn't drive a decision, so its segment power isn't
    actionable at design time) and to continuous/binary metrics (ratio
    metrics are skipped — same open scope gap as compare_methods_chains()
    for continuous/binary, not attempted here either, given how rarely
    ratio metrics are also the ones needing segment-level power checked).

    Returns {dimension_label: [StratumPowerRow, ...]} — dimension_label is
    a single column name, or "col_a × col_b × ..." for the combined one.
    """
    treatment_names = [g for g in groups if g != control_name]
    metrics = [m for m in primary_metrics if m.type in ("continuous", "binary")]
    if not metrics or not strata_cols:
        return {}

    dimensions: dict[str, pd.Series] = {
        col: bucket_column(candidates[col], n_buckets_continuous) for col in strata_cols
    }
    if len(strata_cols) > 1:
        combined = pd.DataFrame(dimensions).astype(str).agg("|".join, axis=1)
        dimensions[" × ".join(strata_cols)] = combined

    out: dict[str, list[StratumPowerRow]] = {}
    for label, stratum_series in dimensions.items():
        rows: list[StratumPowerRow] = []
        for stratum_value, idx in stratum_series.groupby(stratum_series, observed=True).groups.items():
            subset = candidates.loc[idx]
            n_total = len(subset)
            n_control = int(round(n_total * groups[control_name]))
            for treat_name in treatment_names:
                ratio = groups[treat_name] / groups[control_name] if groups[control_name] > 0 else 1.0
                n_treatment = int(round(n_total * groups[treat_name]))
                for metric in metrics:
                    metric_values = metric_history_values(metric, subset)
                    mean = float(metric_values.mean())
                    std = float(metric_values.std(ddof=1)) if metric.type == "continuous" else None

                    mde_rel = None
                    mde_rel_cuped = None
                    if n_control >= 2 and n_treatment >= 2 and mean != 0 and not pd.isna(mean):
                        if metric.type == "binary":
                            if 0 < mean < 1:
                                mde_delta = power.mde_binary(
                                    mean, n_control, alpha=alpha, power=power_target, ratio=ratio
                                )
                                mde_rel = mde_delta / mean
                        elif std is not None and not pd.isna(std) and std > 0:
                            mde_abs = power.mde_continuous(
                                std, n_control, alpha=alpha, power=power_target, ratio=ratio
                            )
                            mde_rel = mde_abs / mean

                        # CUPED variant: only when the metric declares a
                        # pre-period column AND the stratum has enough
                        # points for a not-too-noisy correlation estimate.
                        if mde_rel is not None and metric.pre_col and metric.pre_col in subset.columns and n_total >= 5:
                            pre_series = subset[metric.pre_col]
                            if not pre_series.equals(metric_values):
                                rho = power.correlation_with_pre(metric_values, pre_series)
                                if metric.type == "binary":
                                    mde_delta_cuped = power.mde_binary_cuped(
                                        mean, rho, n_control, alpha=alpha, power=power_target, ratio=ratio
                                    )
                                    mde_rel_cuped = mde_delta_cuped / mean
                                elif std is not None and std > 0:
                                    std_cuped = std * power.cuped_variance_multiplier(rho) ** 0.5
                                    mde_abs_cuped = power.mde_continuous(
                                        std_cuped, n_control, alpha=alpha, power=power_target, ratio=ratio
                                    )
                                    mde_rel_cuped = mde_abs_cuped / mean

                    status = _stratum_status(n_control, n_treatment, mde_rel, overall_mde_rel.get(metric.name))
                    rows.append(
                        StratumPowerRow(
                            stratum=str(stratum_value), treatment_group=treat_name, metric=metric.name,
                            n_control=n_control, n_treatment=n_treatment,
                            mde_rel=mde_rel, mde_rel_cuped=mde_rel_cuped, status=status,
                        )
                    )
        out[label] = rows
    return out


class Experiment:
    """Инкапсулирует дизайн и (начиная с этапа 3) анализ A/B теста."""

    def __init__(self, config: DesignConfig, path: Path, experiments_dir: Path):
        self.config = config
        self.path = path
        self.experiments_dir = experiments_dir
        self.assignments: pd.DataFrame | None = None
        self.report: DesignReport | None = None

    @property
    def name(self) -> str:
        return self.config.name

    @classmethod
    def load(cls, name: str, experiments_dir: Path | None = None) -> "Experiment":
        """Загружает существующий эксперимент: config + assignments — из файлов
        (ABKIT_MODE=file, дефолт) или из Postgres (ABKIT_MODE=db)."""
        from abkit.experiment_store import get_experiment_store

        experiments_dir = experiments_dir or storage.get_experiments_dir()
        handle = get_experiment_store(experiments_dir).load_experiment(name)
        experiment = cls(config=handle.config, path=handle.path, experiments_dir=experiments_dir)
        experiment.assignments = handle.assignments
        return experiment

    @classmethod
    def design(
        cls,
        config: DesignConfig,
        data: pd.DataFrame,
        experiments_dir: Path | None = None,
        progress_callback: Callable[[str], None] | None = None,
        owner_id: str | None = None,
        is_redesign: bool = False,
    ) -> "Experiment":
        """Полный цикл дизайна: валидация -> изоляция -> мощность -> страты -> сплит ->
        проверки -> сохранение. Возвращает Experiment с заполненным .report и .assignments.

        progress_callback(label), если передан, вызывается перед каждым этапом с
        коротким описанием — для UI-индикаторов прогресса (см. app.py, st.status).
        owner_id: только для ABKIT_MODE=db — кто владелец эксперимента (для прав
        доступа, DOCKER.md §4.1); в файловом режиме игнорируется (там нет модели
        пользователей). Если не передан в db-режиме — владельцем становится
        служебный системный юзер (см. abkit/db/store.py).
        is_redesign: True для "Redesign" (5-part package pt.3) — сохраняет
        результат в СУЩЕСТВУЮЩУЮ строку эксперимента (config.name должен уже
        существовать) вместо создания новой; требует store.replace_experiment
        (только ABKIT_MODE=db — DbExperimentStore). Изоляция по-прежнему
        самоисключает текущий эксперимент через current_experiment_name=
        config.name (abkit/design/isolation.py) — старые assignments еще не
        удалены на момент apply_isolation(), поэтому исключение работает как
        обычно; store.replace_experiment удаляет их только ПОСЛЕ того, как
        новый сплит уже посчитан.
        """
        from abkit.experiment_store import get_experiment_store

        experiments_dir = experiments_dir or storage.get_experiments_dir()
        store = get_experiment_store(experiments_dir)
        cb = progress_callback or (lambda _label: None)

        cb("Validating data...")
        _validate_input_data(config, data)
        control_name = infer_control_name(config.groups)

        cb("Checking isolation from other experiments...")
        # store.occupied_units — только у db-режима (ABKIT_MODE=db); файловый
        # режим (дефолт) продолжает читать assignments.parquet как раньше
        isolation_store = store if hasattr(store, "occupied_units") else None
        isolation_result = isolation.apply_isolation(
            data=data,
            unit_col=config.unit_col,
            experiments_dir=experiments_dir,
            mode=config.isolation,
            exclude_experiments=config.exclude_experiments,
            current_experiment_name=config.name,
            store=isolation_store,
            selected_experiments=config.isolation_selected_experiments,
        )
        candidates = isolation_result.candidates
        if len(candidates) == 0:
            raise DesignError("No candidates left for the split after isolation")
        # Memory hygiene (item 3.2): `data` (the full uploaded/read frame) is
        # never read again below this point — only `candidates` is. When
        # isolation actually filtered rows, apply_isolation() built
        # `candidates` as a NEW (smaller) frame, leaving `data` as dead
        # weight for the rest of this long-running function (strata
        # building, split, power calc, checks, report + samples writing).
        # Safe unconditionally: when isolation excluded nothing,
        # `candidates is data` and this just drops one of two references —
        # the object stays alive via `candidates` as expected.
        del data

        strata_nan_counts = nan_counts_by_column(candidates, config.strata) if config.strata else {}
        n_dropped_for_nan_strata = 0
        strata_nan_warnings: list[str] = []
        if config.strata:
            n_pool = len(candidates)
            for col, n_missing in strata_nan_counts.items():
                if n_missing == 0:
                    continue
                pct = n_missing / n_pool * 100
                if config.nan_strategy == "drop":
                    strata_nan_warnings.append(
                        f"Stratum column '{col}': {n_missing} missing values ({pct:.1f}%) — "
                        "users removed from candidates (nan_strategy='drop')"
                    )
                else:
                    strata_nan_warnings.append(
                        f"Stratum column '{col}': {n_missing} missing values ({pct:.1f}%) — "
                        "assigned to a separate 'unknown' stratum"
                    )
                if pct > 5:
                    strata_nan_warnings.append(
                        f"Warning: {pct:.1f}% of users are missing column '{col}'. "
                        "Check the data quality."
                    )

            if config.nan_strategy == "drop":
                nan_mask = candidates[config.strata].isna().any(axis=1)
                n_dropped_for_nan_strata = int(nan_mask.sum())
                if n_dropped_for_nan_strata:
                    candidates = candidates[~nan_mask]
                if len(candidates) == 0:
                    raise DesignError(
                        "No candidates left for the split after removing users with missing "
                        "strata values (nan_strategy='drop')"
                    )

        cb("Computing power...")
        power_results = compute_power_results(config, candidates, control_name)

        cb("Building strata...")
        stratum = build_strata(
            candidates,
            strata_cols=config.strata,
            n_buckets_continuous=config.n_buckets_continuous,
            min_stratum_size=config.min_stratum_size,
        )

        cb("Splitting into groups...")
        seed = config.seed if config.seed is not None else secrets.randbits(32)
        split_result = run_split(
            data=candidates,
            unit_col=config.unit_col,
            groups=config.groups,
            method=config.split_method,
            seed=seed,
            stratum=stratum,
            salt=config.hash_salt,
        )

        assignments = pd.DataFrame(
            {
                "unit_id": normalize_id_series(candidates[config.unit_col]).to_numpy(),
                "group": split_result.group.to_numpy(),
                "stratum": stratum.to_numpy(),
                "assigned_at": pd.Timestamp.now(tz="UTC"),
            }
        )

        cb("Checking validity (SRM, strata balance, pre-period A/A)...")
        observed_counts = assignments["group"].value_counts().to_dict()
        srm_result = checks.check_srm(observed_counts, config.groups)
        balance_result = checks.check_strata_balance(assignments["stratum"], assignments["group"])
        aa_results = checks.check_pre_period_aa(
            candidates, split_result.group, config.metrics, control_name=control_name
        )

        report_warnings = list(split_result.warnings) + strata_nan_warnings
        if not srm_result.passed:
            report_warnings.append(
                f"SRM: p-value={srm_result.p_value:.2e} < 0.001 — actual group proportions "
                "differ significantly from the intended ones"
            )
        if not balance_result.passed:
            report_warnings.append(
                f"Stratum imbalance between groups: p-value={balance_result.p_value:.4f}"
            )
        for aa in aa_results:
            if not aa.passed:
                report_warnings.append(
                    f"Pre-period A/A failed for metric '{aa.metric}' "
                    f"({control_name} vs {aa.treatment_group}): p-value={aa.p_value:.4f}"
                )

        report = DesignReport(
            n_candidates_total=isolation_result.n_before,
            n_excluded_by_isolation=isolation_result.n_excluded,
            n_available=len(candidates),
            excluded_by_experiment=isolation_result.excluded_by_experiment,
            group_sizes={k: int(v) for k, v in observed_counts.items()},
            power_results=power_results,
            srm=srm_result,
            strata_balance=balance_result,
            pre_period_aa=aa_results,
            strata_nan_counts=strata_nan_counts,
            n_dropped_for_nan_strata=n_dropped_for_nan_strata,
            warnings=report_warnings,
        )

        final_config = config.model_copy(
            update={
                "seed": seed,
                "hash_salt": split_result.salt if split_result.salt else config.hash_salt,
                "computed": {
                    "n_candidates_total": report.n_candidates_total,
                    "n_excluded_by_isolation": report.n_excluded_by_isolation,
                    "n_available": report.n_available,
                    "excluded_by_experiment": report.excluded_by_experiment,
                    "group_sizes": report.group_sizes,
                    "strata_nan_counts": report.strata_nan_counts,
                    "n_dropped_for_nan_strata": report.n_dropped_for_nan_strata,
                    "power": _power_results_to_dict(power_results),
                    "srm": {
                        "chi2": srm_result.chi2,
                        "p_value": srm_result.p_value,
                        "passed": srm_result.passed,
                    },
                    "strata_balance": {
                        "chi2": balance_result.chi2,
                        "p_value": balance_result.p_value,
                        "passed": balance_result.passed,
                        # 6-part package pt.10: per-stratum-per-group counts
                        # (already computed for the chi2 test) — surfaced so
                        # the Design tab can render a balance table, not just
                        # the pass/fail badge.
                        "table": checks.strata_balance_rows(balance_result),
                        "groups": checks.strata_balance_groups(balance_result),
                        "n_strata": len(balance_result.table.index),
                    },
                    "pre_period_aa": [
                        {
                            "metric": aa.metric,
                            "treatment_group": aa.treatment_group,
                            "p_value": aa.p_value,
                            "passed": aa.passed,
                        }
                        for aa in aa_results
                    ],
                    "warnings": report_warnings,
                },
            }
        )

        cb("Saving experiment...")
        if is_redesign:
            if not hasattr(store, "replace_experiment"):
                raise DesignError("Redesign requires ABKIT_MODE=db (no file-mode support)")
            handle = store.replace_experiment(config.name, final_config, assignments)
            # replace_experiment() doesn't touch created_at (abkit/db/store.py's
            # docstring) — the design report should show the experiment's real
            # original creation date, not "now" (this redesign's timestamp).
            # Only reachable in DB mode (the hasattr check above), so this
            # import doesn't break file-mode's store-agnosticism.
            from abkit.db.repositories import ExperimentRepo as _ExperimentRepo

            exp_row = _ExperimentRepo().get_by_name(config.name)
            report_created_at = exp_row.created_at if exp_row else None
        else:
            handle = store.create_experiment(final_config, assignments, owner_id=owner_id)
            report_created_at = datetime.now(timezone.utc)
        path = handle.path

        experiment = cls(config=final_config, path=path, experiments_dir=experiments_dir)
        experiment.assignments = assignments
        experiment.report = report

        from abkit.viz.report import render_design_report  # локальный импорт: избегаем цикла

        design_report_html = render_design_report(experiment, created_at=report_created_at)
        (path / "design_report.html").write_text(design_report_html, encoding="utf-8")

        return experiment

    @classmethod
    def design_external(
        cls,
        config: DesignConfig,
        experiments_dir: Path | None = None,
        owner_id: str | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> "Experiment":
        """External split (item 12, config.split_source == "external"): the
        split happens in an outside system (Firebase A/B Testing and
        similar) — ABSet only stores the declared groups/metrics/hypothesis
        for later analysis. No dataset, no isolation, no power calculation
        (there's no data yet to estimate variance from — an achievable-MDE
        table would just be a guess dressed up as a computation, so we
        don't build one; see AnalyzeSection/DesignSection's "external
        design: power calculated by the external system" note instead), no
        assignments. Saved with empty assignments — every downstream
        consumer (samples download, design_report's power/strata sections)
        already handles a design with zero rows gracefully, since a design
        can legitimately produce zero candidates in the normal flow too.
        """
        from abkit.experiment_store import get_experiment_store

        cb = progress_callback or (lambda _label: None)
        experiments_dir = experiments_dir or storage.get_experiments_dir()
        store = get_experiment_store(experiments_dir)
        empty_assignments = pd.DataFrame(columns=["unit_id", "group", "stratum", "assigned_at"])
        cb("Saving experiment...")
        handle = store.create_experiment(config, empty_assignments, owner_id=owner_id)
        experiment = cls(config=config, path=handle.path, experiments_dir=experiments_dir)
        experiment.assignments = empty_assignments
        return experiment

    def _cumulative_lift(
        self,
        metric: MetricConfig,
        data: pd.DataFrame,
        control_name: str,
        treat_name: str,
        date_col: str,
        steps: list[Step],
        agg_methods: dict[str, AggMethod] | None,
    ) -> pd.DataFrame:
        """Кумулятивный лифт по дням (для секции 'Динамика' отчета).

        Для каждого дня t берет данные с начала теста по день t включительно,
        агрегирует их per user (той же логикой, что и основной анализ:
        aggregate_post_data) и считает эффект designed-цепочкой на этом
        кумулятивном срезе. Если исходные данные уже были одна строка на юзера
        (без разбивки по дням), агрегация — no-op, и это в точности воспроизводит
        "кумулятивное включение юзеров по дате их события".
        """
        assignments = self.assignments.assign(
            unit_id=normalize_id_series(self.assignments["unit_id"])
        )
        data = data.assign(
            **{self.config.unit_col: normalize_id_series(data[self.config.unit_col])}
        )
        joined = assignments.merge(
            data, left_on="unit_id", right_on=self.config.unit_col, how="inner"
        )
        joined = joined[joined["group"].isin([control_name, treat_name])].copy()
        joined[date_col] = pd.to_datetime(joined[date_col])

        rows = []
        for d in sorted(joined[date_col].dt.date.unique()):
            cumulative = joined[joined[date_col].dt.date <= d]
            aggregated = aggregate_post_data(
                cumulative, "unit_id", [metric], date_col, agg_methods,
                carry_columns=["group", "stratum"],
            )
            n_control = (aggregated["group"] == control_name).sum()
            n_treat = (aggregated["group"] == treat_name).sum()
            if n_control < 2 or n_treat < 2:
                continue
            ctx = build_metric_context(metric, aggregated, control_name, treat_name, self.config.alpha, False)
            try:
                ctx = Pipeline(steps).run(ctx)
            except ValueError:
                continue
            rows.append(
                {
                    "date": d,
                    # Regression: these used to be pre-multiplied by 100 here,
                    # inconsistent with every other effect_rel in the codebase
                    # (TestResult.effect_rel, plots.py's other two charts) —
                    # a raw fraction (0.02 = 2%), converted to percent only at
                    # the display layer. The React chart (CumulativeLiftChart)
                    # already does its own *100 assuming the fraction
                    # convention, so the pre-multiplication here was silently
                    # inflating its Y axis 100x (2% became "200", easily
                    # mistaken for "2,000" at larger lifts). Keep raw here;
                    # plots.py's cumulative_lift_plot (the HTML report's
                    # chart) now does its own *100 for the same reason.
                    "effect_rel": ctx.result.effect_rel,
                    "ci_lower": ctx.result.ci_rel[0],
                    "ci_upper": ctx.result.ci_rel[1],
                }
            )
        return pd.DataFrame(rows)

    def _map_external_groups(
        self, data: pd.DataFrame, group_column: str, group_mapping: dict[str, str]
    ) -> tuple[pd.DataFrame, int, int]:
        """External split (item 12): builds the same shape join_with_assignments
        would (a "group" column alongside the metric data), but from the
        post-data's own group column instead of an assignments join — there
        are no assignments for an external-split experiment, the split
        already happened in the outside system. group_mapping maps raw
        column values (as strings) to a declared group name; any value
        mapped to "exclude" (or not present in the mapping at all) drops
        the row. Returns (merged, n_total_rows, n_excluded_rows)."""
        if group_column not in data.columns:
            raise checks.AnalysisError(f"Group column '{group_column}' is not in the uploaded data")
        n_total = len(data)
        raw = data[group_column].astype(str)
        declared_groups = set(self.config.groups)
        mapped = raw.map(lambda v: group_mapping.get(v))
        keep_mask = mapped.isin(declared_groups)
        merged = data.loc[keep_mask].copy()
        merged["group"] = mapped.loc[keep_mask]
        n_excluded = n_total - len(merged)
        return merged, n_total, n_excluded

    def analyze(
        self,
        data: pd.DataFrame,
        methods: dict[str, list[Step]] | None = None,
        correction: str = "holm",
        compare_methods: bool = False,
        extra_methods: dict[str, list[list[Step]]] | None = None,
        date_col: str | None = None,
        agg_methods: dict[str, AggMethod] | None = None,
        progress_callback: Callable[[str], None] | None = None,
        group_column: str | None = None,
        group_mapping: dict[str, str] | None = None,
        segment_columns: list[str] | None = None,
        created_at: datetime | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> AnalysisResults:
        """Анализ по фактическим данным: join -> проверки честности -> пайплайн по
        метрикам -> поправка на множественность.

        compare_methods=True: для каждой continuous-метрики дополнительно считает
        стандартный набор альтернатив (Welch сырой, +trim1%, +CUPED, Bootstrap BCa,
        Mann-Whitney) с is_designed_method=False — для устойчивости выводов, в вердикт
        и поправку на множественность не входят.

        extra_methods: item 3 (consolidated package, multi-select analysis
        methods) — {metric_name: [[Step, ...], ...]}, an EXPLICIT per-metric
        override of which extra (non-designed) chains to run, REPLACING
        compare_methods_chains()'s fixed standard set for any metric present
        in this dict (absent metrics get no extras at all, even if
        compare_methods=True — when extra_methods is provided the bool is
        ignored entirely for metrics it covers). This is how the frontend's
        per-metric multi-select ("designed + whichever else the user
        checked = the comparison set") reaches the core: the React UI always
        sends every named metric's full current selection, so extra_methods
        being non-None means "the caller has fully specified the compare
        set for every metric it names" — unlike compare_methods_chains()'s
        fixed list, an empty list here means "no extras for this metric",
        not "fall back to the default". None (the default) preserves the
        original compare_methods bool behavior untouched, for callers that
        never adopted per-metric selection (CLI, validation simulations).

        date_col: колонка с датой события. Если данные содержат несколько строк на
        юзера (разбивка по дням), date_col обязателен — иначе анализ падает с
        понятной ошибкой; при наличии date_col данные автоматически агрегируются
        до одной строки на юзера для основного анализа (см. agg_methods) и
        используются для кумулятивного лифта по дням в отчете. Not supported for
        split_source="external" (below) — one row per user is assumed there.
        agg_methods: per-metric override способа агрегации по дням (sum/max/last/
        first); по умолчанию continuous -> sum, binary -> max, ratio -> sum num и
        den отдельно.
        progress_callback(label), если передан, вызывается перед каждым этапом —
        для UI-индикаторов прогресса (см. app.py, st.status).

        group_column/group_mapping: REQUIRED when self.config.split_source ==
        "external" (item 12) — there are no assignments to join against, the
        group comes directly from a column in the uploaded post-data, mapped
        to the declared group names (abkit/jobs.py::run_analyze validates
        both are present before calling this). Ignored for the normal
        (split_source="abkit") flow.

        created_at/started_at/completed_at: Stage 2 (report header dates) —
        optional, purely for display in report.html; this in-memory
        Experiment has no DB-row fields of its own (DB mode's lifecycle
        timestamps live on the ExperimentRepo row), so the caller
        (backend/routers/experiments.py::start_analyze, which already has
        that row in scope) passes them through here into attach_context().

        segment_columns: External split rework (§3) — the columns to break the
        effect down by, from the ANALYSIS dataset's ACTUAL columns, chosen at
        analyze time. None (default) → the design-declared strata
        (self.config.strata), preserving the pre-existing behavior. Columns
        that are declared strata are broken down via the design-time stratum
        (ABSet) or a stratum synthesized from the uploaded data (external);
        columns NOT in self.config.strata are "ad-hoc" segments (marked as
        such in the report/results — the analysis dataset may carry attributes
        that weren't declared or didn't exist at design time) and are broken
        down directly on their raw values. Both flows accept ad-hoc columns.
        A declared/ad-hoc column absent from the uploaded data degrades
        gracefully: a warning names it, that column is skipped, the rest runs.
        """
        cb = progress_callback or (lambda _label: None)
        global_warnings: list[str] = []
        loss_result = None
        strata_balance_result: checks.BalanceResult | None = None

        if self.config.split_source == "external":
            if not group_column or not group_mapping:
                raise checks.AnalysisError(
                    "This is an external-split experiment — select a group column and map its "
                    "values to the declared groups before running the analysis."
                )
            if date_col:
                raise checks.AnalysisError(
                    "Day-by-day aggregation (a date column) isn't supported for external-split "
                    "experiments — upload data with one row per user."
                )
            cb("Applying group assignment mapping...")
            merged, n_total, n_excluded = self._map_external_groups(data, group_column, group_mapping)
            if merged.empty:
                raise checks.AnalysisError(
                    "No rows matched a declared group after mapping — check the group column "
                    "and the value mapping."
                )
            cb("Checking validity (SRM)...")
            observed_counts = merged["group"].value_counts().to_dict()
            srm_result = checks.check_srm(observed_counts, self.config.groups)
            if not srm_result.passed:
                global_warnings.append(
                    f"SRM on the actual data: p-value={srm_result.p_value:.2e} < 0.001 — "
                    "the analysis results are unreliable"
                )
            if n_excluded:
                pct = n_excluded / n_total * 100 if n_total else 0.0
                global_warnings.append(
                    f"Group column coverage: {n_excluded} of {n_total} rows had no mapped "
                    f"group value and were excluded ({pct:.1f}%)"
                )
            # External split rework (§2): there are no assignments, so the
            # design-declared strata never produced a `stratum` column. If the
            # declared strata columns are present in the uploaded data,
            # synthesize one here (same build_strata used at design time) so
            # the existing balance/segment machinery below works unchanged.
            # Columns declared but absent from the data degrade gracefully:
            # warn, skip that column, keep the rest.
            missing_strata = [c for c in self.config.strata if c not in merged.columns]
            for col in missing_strata:
                global_warnings.append(
                    f"Declared stratum column '{col}' is not in the analysis dataset — "
                    "its balance check and segment breakdown were skipped."
                )
            external_strata = [c for c in self.config.strata if c in merged.columns]
            if external_strata:
                merged["stratum"] = build_strata(
                    merged, external_strata, self.config.n_buckets_continuous,
                    self.config.min_stratum_size,
                )
        else:
            if self.assignments is None:
                raise DesignError(
                    "This experiment has no assignments (design() was not run, or they were not loaded)"
                )

            if date_col and date_col not in data.columns:
                raise checks.AnalysisError(f"Date column '{date_col}' is not in the data")

            if self.config.unit_col not in data.columns:
                # Regression (found via a real internal_error report): this was
                # an unguarded data[self.config.unit_col] access below, raising
                # a raw pandas KeyError — not one of the domain exceptions
                # backend/jobs/runner.py::_human_readable_message recognizes, so
                # it surfaced to the user as an opaque "Internal processing
                # error" instead of a clear, actionable one (e.g. post-period
                # data uploaded without the unit-id column used at design time).
                raise checks.AnalysisError(
                    f"Unit column '{self.config.unit_col}' is not in the uploaded data. "
                    "Make sure you selected the post-period dataset that has the same "
                    "user-id column used when this experiment was designed."
                )

            dup_mask = data[self.config.unit_col].duplicated(keep=False)
            if dup_mask.any():
                if not date_col:
                    n_users_with_dupes = int(data.loc[dup_mask, self.config.unit_col].nunique())
                    raise checks.AnalysisError(
                        f"Found duplicate '{self.config.unit_col}' values in the data "
                        f"({n_users_with_dupes} users with multiple rows). Either "
                        "aggregate the data beforehand (one row = one user), or "
                        "provide a date column — the program will aggregate by user for "
                        "the main analysis and use the day-by-day breakdown for "
                        "the cumulative lift."
                    )
                n_users = int(data[self.config.unit_col].nunique())
                n_days = int(data[date_col].nunique())
                cb("Aggregating data by day...")
                main_data = aggregate_post_data(
                    data, self.config.unit_col, self.config.metrics, date_col, agg_methods
                )
                global_warnings.append(
                    f"The data has a day-by-day breakdown ({n_users} unique users × "
                    f"{n_days} days). It is automatically aggregated for "
                    "the main analysis: continuous metrics — sum, binary — max, "
                    "ratio — sum(num)/sum(den) (unless overridden for the metric)."
                )
            else:
                main_data = data

            cb("Joining with assignments...")
            merged = checks.join_with_assignments(self.assignments, main_data, self.config.unit_col)

            cb("Checking validity (SRM, data loss)...")
            observed_counts = merged["group"].value_counts().to_dict()
            srm_result = checks.check_srm(observed_counts, self.config.groups)
            if not srm_result.passed:
                global_warnings.append(
                    f"SRM on the actual data: p-value={srm_result.p_value:.2e} < 0.001 — "
                    "the analysis results are unreliable"
                )

            loss_result = checks.check_data_loss(self.assignments, merged["unit_id"])
            if not loss_result.symmetric:
                global_warnings.append(
                    f"Asymmetric data loss between groups (p-value={loss_result.p_value:.4f}): "
                    f"{loss_result.missing_rate}"
                )

        control_name = infer_control_name(self.config.groups)
        treatment_names = [g for g in self.config.groups if g != control_name]

        all_results: list[TestResult] = []
        raw_values: dict[str, dict[str, pd.Series]] = {}
        segment_results: dict[str, dict[str, list[tuple[str, TestResult]]]] = {}
        # Item 3 (per-dimension segment analysis): {dimension_label:
        # {metric_name: {treat_name: [(value, TestResult), ...]}}} —
        # dimension_label is one of self.config.strata's column names, or
        # (when there's more than one) their " × " join for the combined
        # cross-product, which duplicates segment_results' content under
        # that label so the frontend/report have ONE structure to read
        # instead of two. The combined "stratum" column IS the pipe-joined
        # per-row string build_strata() produces at design time — cheaply
        # decomposable back into individual dimension values by splitting
        # on "|" (item 3.4: no re-read/re-bucketing of the original raw
        # columns needed). Rows merged into "_other_" (small strata) or
        # "_all_" (no strata configured) aren't decomposable and are
        # excluded from the PER-DIMENSION breakdown only (not combined) —
        # the same "too rare to say anything individually" reasoning that
        # put them there in the first place.
        segment_results_by_dimension: dict[str, dict[str, dict[str, list[tuple[str, TestResult]]]]] = {}

        # Strata balance (§2a): group × stratum composition + chi-square, on
        # the actually-analyzed users. For ABSet the `stratum` column comes
        # from the assignments join; for external it was synthesized above
        # from the declared strata columns present in the uploaded data. The
        # DESIGN report already shows a balance table for ABSet, but external
        # never had one — computing it here from `merged` gives external the
        # analog ("was the outside split balanced across these attributes?").
        if "stratum" in merged.columns and merged["stratum"].nunique() > 1:
            strata_balance_result = checks.check_strata_balance(
                merged["stratum"], merged["group"], alpha=self.config.alpha
            )

        # `effective_strata` — the declared strata whose values `merged`'s
        # stratum column actually encodes: all of them for ABSet (the stratum
        # was built over the full list at design time), only those present in
        # the uploaded data for external (the rest were skipped + warned about
        # above). Everything below (decomposition, combined label) keys off
        # this, so a partially-present external strata set stays self-
        # consistent (pipe count matches).
        if self.config.split_source == "external":
            effective_strata = [c for c in self.config.strata if c in merged.columns]
        else:
            effective_strata = list(self.config.strata)

        dimension_series: dict[str, pd.Series] = {}
        if len(effective_strata) > 1 and "stratum" in merged.columns:
            split_cols = merged["stratum"].str.split("|", expand=True)
            if split_cols.shape[1] == len(effective_strata):
                decomposable = ~merged["stratum"].isin(["_other_", "_all_"])
                for i, col_name in enumerate(effective_strata):
                    dimension_series[col_name] = split_cols[i].where(decomposable)
        combined_dimension_label = (
            " × ".join(effective_strata) if len(effective_strata) > 1
            else (effective_strata[0] if effective_strata else None)
        )

        # Ad-hoc segments (§3): columns chosen at analyze time that are NOT
        # design-declared strata — broken down directly on their raw values
        # in the uploaded data (both flows). `segment_columns` is the full
        # resolved list the caller passed (declared + ad-hoc); None means "the
        # design-declared strata only", i.e. no ad-hoc, preserving the old
        # behavior. Declared columns are handled by the stratum path above, so
        # only the non-declared ones are processed here.
        requested_segment_columns = (
            list(self.config.strata) if segment_columns is None else list(segment_columns)
        )
        declared_set = set(self.config.strata)
        ad_hoc_dimensions: list[str] = []
        for col in requested_segment_columns:
            if col in declared_set or col in dimension_series:
                continue
            if col in merged.columns:
                # Same bucketing the declared strata get at design time: a
                # high-cardinality numeric column becomes quantile buckets
                # (else it would explode into hundreds of singleton segments);
                # a categorical column stays as-is; NaN → "unknown" (its own
                # segment), matching nan_strategy="separate_stratum".
                dimension_series[col] = bucket_column(
                    merged[col], self.config.n_buckets_continuous
                )
                ad_hoc_dimensions.append(col)
            else:
                global_warnings.append(
                    f"Segment column '{col}' is not in the analysis dataset — skipped."
                )
        daily_results: dict[str, dict[str, pd.DataFrame]] = {}

        n_metrics = len(self.config.metrics)
        for i, metric in enumerate(self.config.metrics, start=1):
            cb(f"Computing metric {i} of {n_metrics}: {metric.name}...")
            designed_steps = resolve_steps(metric, methods, seed=self.config.seed)
            if extra_methods is not None:
                extra_chains = extra_methods.get(metric.name, [])
            else:
                extra_chains = compare_methods_chains(metric, seed=self.config.seed) if compare_methods else []
            raw_values.setdefault(metric.name, {})

            for treat_name in treatment_names:
                ctx = build_metric_context(metric, merged, control_name, treat_name, self.config.alpha, True)
                raw_values[metric.name].setdefault(
                    control_name, ctx.values[ctx.group == control_name]
                )
                raw_values[metric.name][treat_name] = ctx.values[ctx.group == treat_name]

                ctx = Pipeline(designed_steps).run(ctx)
                all_results.append(ctx.result)

                for chain in extra_chains:
                    extra_ctx = build_metric_context(metric, merged, control_name, treat_name, self.config.alpha, False)
                    n_control_extra = int((extra_ctx.group == control_name).sum())
                    n_treat_extra = int((extra_ctx.group == treat_name).sum())
                    pipeline = Pipeline(chain)
                    try:
                        extra_ctx = pipeline.run(extra_ctx)
                        all_results.append(extra_ctx.result)
                    except Exception as e:
                        all_results.append(
                            _failed_method_result(
                                metric, pipeline, control_name, treat_name,
                                n_control_extra, n_treat_extra, e,
                            )
                        )

                strata_values = merged["stratum"].unique() if "stratum" in merged.columns else []
                if len(strata_values) > 1:
                    seg_list = []
                    for s in sorted(strata_values, key=str):
                        seg_subset = merged[merged["stratum"] == s]
                        seg_ctx = build_metric_context(metric, seg_subset, control_name, treat_name, self.config.alpha, False)
                        try:
                            seg_ctx = Pipeline(designed_steps).run(seg_ctx)
                        except ValueError:
                            continue
                        seg_list.append((str(s), seg_ctx.result))
                    segment_results.setdefault(metric.name, {})[treat_name] = seg_list
                    if combined_dimension_label:
                        segment_results_by_dimension.setdefault(combined_dimension_label, {}).setdefault(
                            metric.name, {}
                        )[treat_name] = seg_list

                # Item 3: same computation as the combined block above, but
                # grouped by EACH stratification dimension alone instead of
                # their cross-product — cheap (reuses dimension_series,
                # already decomposed once before this loop) and exploratory
                # in exactly the same sense as the combined segments.
                for dim_label, dim_series in dimension_series.items():
                    dim_values = dim_series.dropna().unique()
                    if len(dim_values) < 2:
                        continue
                    dim_seg_list = []
                    for v in sorted(dim_values, key=str):
                        dim_subset = merged[dim_series == v]
                        dim_seg_ctx = build_metric_context(
                            metric, dim_subset, control_name, treat_name, self.config.alpha, False
                        )
                        try:
                            dim_seg_ctx = Pipeline(designed_steps).run(dim_seg_ctx)
                        except ValueError:
                            continue
                        dim_seg_list.append((str(v), dim_seg_ctx.result))
                    if dim_seg_list:
                        segment_results_by_dimension.setdefault(dim_label, {}).setdefault(
                            metric.name, {}
                        )[treat_name] = dim_seg_list

                if date_col:
                    daily_results.setdefault(metric.name, {})[treat_name] = self._cumulative_lift(
                        metric, data, control_name, treat_name, date_col, designed_steps, agg_methods
                    )

        cb("Applying multiple-testing correction...")
        # поправка на множественность раздельно для primary (влияет на вердикт) и
        # secondary/exploratory (только информативно, в вердикт не входит)
        for role in ("primary", "secondary"):
            role_results = [r for r in all_results if r.role == role and r.is_designed_method]
            if not role_results:
                continue
            adjusted = adjust_p_values([r.p_value for r in role_results], method=correction)
            for r, p_adj in zip(role_results, adjusted):
                r.p_value_adjusted = p_adj

        results = AnalysisResults(all_results, global_warnings=global_warnings)
        results.attach_context(
            experiment_name=self.config.name,
            config=self.config,
            path=self.path,
            control_name=control_name,
            group_sizes={k: int(v) for k, v in observed_counts.items()},
            srm=srm_result,
            loss=loss_result,
            raw_values=raw_values,
            segment_results=segment_results,
            segment_results_by_dimension=segment_results_by_dimension,
            ad_hoc_segment_dimensions=ad_hoc_dimensions,
            strata_balance=strata_balance_result,
            daily_results=daily_results,
            correction=correction,
            created_at=created_at,
            started_at=started_at,
            completed_at=completed_at,
        )
        return results
