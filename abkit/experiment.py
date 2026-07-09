"""Experiment: сборка полного цикла дизайна (и, в дальнейшем, анализа) A/B теста."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np
import pandas as pd

from abkit import checks, storage
from abkit.analysis.multiple_testing import adjust_p_values
from abkit.analysis.results import AnalysisResults, TestResult
from abkit.analysis.tests import Bootstrap, DeltaMethodTTest, MannWhitney, WelchTTest, ZTestProportions
from abkit.analysis.variance_reduction import CUPED, PostStratification
from abkit.config import DesignConfig, MetricConfig
from abkit.design import isolation, power
from abkit.design.splitter import split as run_split
from abkit.design.stratification import build_strata, nan_counts_by_column
from abkit.idnorm import normalize_id_series
from abkit.pipeline import MetricContext, Pipeline, Step
from abkit.preprocessing.outliers import RemoveOutliers, Winsorize


class DesignError(Exception):
    """Пользовательская ошибка на этапе дизайна (некорректные входные данные/конфиг)."""


_STEP_REGISTRY: dict[str, type[Step]] = {
    "WelchTTest": WelchTTest,
    "ZTestProportions": ZTestProportions,
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


def compare_methods_chains(metric: MetricConfig, seed: int | None = None) -> list[list[Step]]:
    """Стандартный набор альтернативных цепочек для устойчивости выводов (compare_methods=True).

    Только для continuous-метрик, как описано в DESIGN.md; сами по себе не влияют на
    вердикт (is_designed_method=False). seed передается в Bootstrap для воспроизводимости
    (повторный analyze() на тех же данных должен давать бит-в-бит тот же results.json).
    """
    if metric.type != "continuous":
        return []
    chains: list[list[Step]] = [
        [WelchTTest()],
        [RemoveOutliers(upper_q=0.99), WelchTTest()],
        [Bootstrap(method="bca", seed=seed)],
        [MannWhitney()],
    ]
    if metric.pre_col:
        chains.append([CUPED(), WelchTTest()])
    return chains


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


def _compute_power_results(
    config: DesignConfig, candidates: pd.DataFrame, control_name: str
) -> dict[str, power.PowerResult]:
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
            metric=metric.name, metric_type=metric.type, baseline_mean=mean, baseline_std=std, rho=rho
        )

        if config.mde is not None:
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
            else:
                mde_abs = abs(config.mde * mean) if mean != 0 else abs(config.mde)
                n_req = power.sample_size_continuous(std, mde_abs, alpha=alpha, power=config.power, ratio=ratio)
                result.sample_size_per_group = n_req
                result.mde_abs = mde_abs
                result.mde_rel = config.mde
                if rho is not None:
                    std_cuped = std * power.cuped_variance_multiplier(rho) ** 0.5
                    result.sample_size_per_group_cuped = power.sample_size_continuous(
                        std_cuped, mde_abs, alpha=alpha, power=config.power, ratio=ratio
                    )
                    result.mde_abs_cuped = mde_abs
                    result.mde_rel_cuped = config.mde

            if result.sample_size_per_group is not None and result.sample_size_per_group > n_control_available:
                warnings.append(
                    f"Not enough data for the given MDE: need ~{result.sample_size_per_group:.0f} "
                    f"in the control group, {n_control_available:.0f} available"
                )
        else:
            n_control = config.sample_size * control_prop if config.sample_size else n_control_available
            if metric.type == "binary":
                mde_delta = power.mde_binary(mean, n_control, alpha=alpha, power=config.power, ratio=ratio)
                result.sample_size_per_group = n_control
                result.mde_abs = mde_delta
                result.mde_rel = mde_delta / mean if mean else None
            else:
                mde_abs = power.mde_continuous(std, n_control, alpha=alpha, power=config.power, ratio=ratio)
                result.sample_size_per_group = n_control
                result.mde_abs = mde_abs
                result.mde_rel = mde_abs / mean if mean else None
                if rho is not None:
                    std_cuped = std * power.cuped_variance_multiplier(rho) ** 0.5
                    mde_abs_cuped = power.mde_continuous(std_cuped, n_control, alpha=alpha, power=config.power, ratio=ratio)
                    result.sample_size_per_group_cuped = n_control
                    result.mde_abs_cuped = mde_abs_cuped
                    result.mde_rel_cuped = mde_abs_cuped / mean if mean else None

        result.warnings = warnings
        results[metric.name] = result
    return results


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
        }
        for name, r in results.items()
    }


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
    ) -> "Experiment":
        """Полный цикл дизайна: валидация -> изоляция -> мощность -> страты -> сплит ->
        проверки -> сохранение. Возвращает Experiment с заполненным .report и .assignments.

        progress_callback(label), если передан, вызывается перед каждым этапом с
        коротким описанием — для UI-индикаторов прогресса (см. app.py, st.status).
        owner_id: только для ABKIT_MODE=db — кто владелец эксперимента (для прав
        доступа, DOCKER.md §4.1); в файловом режиме игнорируется (там нет модели
        пользователей). Если не передан в db-режиме — владельцем становится
        служебный системный юзер (см. abkit/db/store.py).
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
        power_results = _compute_power_results(config, candidates, control_name)

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
        handle = store.create_experiment(final_config, assignments, owner_id=owner_id)
        path = handle.path

        experiment = cls(config=final_config, path=path, experiments_dir=experiments_dir)
        experiment.assignments = assignments
        experiment.report = report

        from abkit.viz.report import render_design_report  # локальный импорт: избегаем цикла

        design_report_html = render_design_report(experiment)
        (path / "design_report.html").write_text(design_report_html, encoding="utf-8")

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
                    "effect_rel": ctx.result.effect_rel * 100,
                    "ci_lower": ctx.result.ci_rel[0] * 100,
                    "ci_upper": ctx.result.ci_rel[1] * 100,
                }
            )
        return pd.DataFrame(rows)

    def analyze(
        self,
        data: pd.DataFrame,
        methods: dict[str, list[Step]] | None = None,
        correction: str = "holm",
        compare_methods: bool = False,
        date_col: str | None = None,
        agg_methods: dict[str, AggMethod] | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> AnalysisResults:
        """Анализ по фактическим данным: join -> проверки честности -> пайплайн по
        метрикам -> поправка на множественность.

        compare_methods=True: для каждой continuous-метрики дополнительно считает
        стандартный набор альтернатив (Welch сырой, +trim1%, +CUPED, Bootstrap BCa,
        Mann-Whitney) с is_designed_method=False — для устойчивости выводов, в вердикт
        и поправку на множественность не входят.

        date_col: колонка с датой события. Если данные содержат несколько строк на
        юзера (разбивка по дням), date_col обязателен — иначе анализ падает с
        понятной ошибкой; при наличии date_col данные автоматически агрегируются
        до одной строки на юзера для основного анализа (см. agg_methods) и
        используются для кумулятивного лифта по дням в отчете.
        agg_methods: per-metric override способа агрегации по дням (sum/max/last/
        first); по умолчанию continuous -> sum, binary -> max, ratio -> sum num и
        den отдельно.
        progress_callback(label), если передан, вызывается перед каждым этапом —
        для UI-индикаторов прогресса (см. app.py, st.status).
        """
        cb = progress_callback or (lambda _label: None)
        if self.assignments is None:
            raise DesignError("This experiment has no assignments (design() was not run, or they were not loaded)")

        if date_col and date_col not in data.columns:
            raise checks.AnalysisError(f"Date column '{date_col}' is not in the data")

        global_warnings: list[str] = []

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
        daily_results: dict[str, dict[str, pd.DataFrame]] = {}

        n_metrics = len(self.config.metrics)
        for i, metric in enumerate(self.config.metrics, start=1):
            cb(f"Computing metric {i} of {n_metrics}: {metric.name}...")
            designed_steps = resolve_steps(metric, methods, seed=self.config.seed)
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
            daily_results=daily_results,
            correction=correction,
        )
        return results
