"""A/A и A/B симуляции для валидации дизайна на исторических данных."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table
from statsmodels.stats.proportion import proportion_confint

from abkit.config import DesignConfig, MetricConfig
from abkit.design import power
from abkit.design.splitter import split as run_split
from abkit.design.stratification import build_strata
from abkit.experiment import (
    build_metric_context,
    compare_methods_chains,
    infer_control_name,
    metric_history_values,
    resolve_steps,
)
from abkit.pipeline import Pipeline, Step


def _build_chains_by_metric(
    config: DesignConfig, compare_methods: bool
) -> dict[str, list[tuple[str, list[Step]]]]:
    chains_by_metric: dict[str, list[tuple[str, list[Step]]]] = {}
    for metric in config.metrics:
        designed = resolve_steps(metric, None)
        chains = [(Pipeline(designed).method_name, designed)]
        if compare_methods:
            # seed намеренно не передаем: Bootstrap должен ресэмплить независимо в
            # каждом раунде симуляции (иначе исказится оценка FPR/мощности)
            for chain in compare_methods_chains(metric):
                chains.append((Pipeline(chain).method_name, chain))
        chains_by_metric[metric.name] = chains
    return chains_by_metric


def _flip_binary(
    df: pd.DataFrame, mask: pd.Series, col: str, p_old: float, p_new: float, rng: np.random.Generator
) -> None:
    idx = df.index[mask]
    n = len(idx)
    current = df.loc[idx, col].to_numpy()
    if p_new > p_old:
        candidates = idx[current == 0]
        n_flip = min(int(round((p_new - p_old) * n)), len(candidates))
        if n_flip > 0:
            flip_idx = rng.choice(candidates, size=n_flip, replace=False)
            df.loc[flip_idx, col] = 1
    elif p_new < p_old:
        candidates = idx[current == 1]
        n_flip = min(int(round((p_old - p_new) * n)), len(candidates))
        if n_flip > 0:
            flip_idx = rng.choice(candidates, size=n_flip, replace=False)
            df.loc[flip_idx, col] = 0


def _inject_effect(
    merged: pd.DataFrame,
    metrics: list[MetricConfig],
    treatment_names: list[str],
    effect: float,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Инъекция эффекта: аддитивный сдвиг для continuous, флип для binary,
    мультипликативный сдвиг числителя для ratio."""
    merged = merged.copy()
    for metric in metrics:
        for treat_name in treatment_names:
            mask = (merged["group"] == treat_name).to_numpy()
            if metric.type == "binary":
                p_old = float(merged.loc[mask, metric.name].mean())
                p_new = float(np.clip(p_old * (1 + effect), 0, 1))
                _flip_binary(merged, pd.Series(mask, index=merged.index), metric.name, p_old, p_new, rng)
            elif metric.type == "ratio":
                merged.loc[mask, metric.num] = merged.loc[mask, metric.num] * (1 + effect)
            else:
                baseline_col = metric.name if metric.name in merged.columns else metric.pre_col
                mean_val = float(merged[baseline_col].mean())
                merged.loc[mask, metric.name] = merged.loc[mask, metric.name] + effect * mean_val
    return merged


def _simulate_round(
    data: pd.DataFrame,
    config: DesignConfig,
    control_name: str,
    treatment_names: list[str],
    chains_by_metric: dict[str, list[tuple[str, list[Step]]]],
    seed: int,
    effect: float | None = None,
) -> dict[tuple[str, str, str], float]:
    stratum = build_strata(
        data,
        strata_cols=config.strata,
        n_buckets_continuous=config.n_buckets_continuous,
        min_stratum_size=config.min_stratum_size,
    )
    split_result = run_split(
        data=data,
        unit_col=config.unit_col,
        groups=config.groups,
        method=config.split_method,
        seed=seed,
        stratum=stratum,
        salt=config.hash_salt,
    )
    merged = data.copy()
    merged["group"] = split_result.group.to_numpy()
    merged["stratum"] = stratum.to_numpy()

    if effect is not None:
        rng = np.random.default_rng(seed)
        merged = _inject_effect(merged, config.metrics, treatment_names, effect, rng)

    metrics_by_name = {m.name: m for m in config.metrics}
    out: dict[tuple[str, str, str], float] = {}
    for metric_name, chains in chains_by_metric.items():
        metric = metrics_by_name[metric_name]
        for treat_name in treatment_names:
            for chain_name, steps in chains:
                ctx = build_metric_context(metric, merged, control_name, treat_name, config.alpha, True)
                try:
                    ctx = Pipeline(steps).run(ctx)
                except ValueError:
                    continue
                out[(metric_name, chain_name, treat_name)] = ctx.result.p_value
    return out


def _run_rounds(
    data: pd.DataFrame,
    config: DesignConfig,
    control_name: str,
    treatment_names: list[str],
    chains_by_metric: dict[str, list[tuple[str, list[Step]]]],
    seeds: np.ndarray,
    effect: float | None,
    n_jobs: int,
    show_progress: bool,
    description: str,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[dict[tuple[str, str, str], float]]:
    if n_jobs != 1:
        from joblib import Parallel, delayed

        # прогресс-колбэк не поддержан для n_jobs>1: joblib не дает простого
        # способа сообщать о завершении отдельных задач без лишней сложности
        return Parallel(n_jobs=n_jobs)(
            delayed(_simulate_round)(
                data, config, control_name, treatment_names, chains_by_metric, int(s), effect
            )
            for s in seeds
        )

    iterator = seeds
    if show_progress:
        from rich.progress import track

        iterator = track(seeds, description=description)

    total = len(seeds)
    results = []
    for i, s in enumerate(iterator):
        results.append(
            _simulate_round(data, config, control_name, treatment_names, chains_by_metric, int(s), effect)
        )
        if progress_callback:
            progress_callback(i + 1, total)
    return results


@dataclass
class MethodFPR:
    """Эмпирический FPR одной цепочки методов по одной метрике/treatment-группе."""

    method: str
    metric: str
    treatment_group: str
    n_sims: int
    fpr: float
    ci_low: float
    ci_high: float
    passed: bool  # ДИ FPR накрывает alpha


@dataclass
class AAReport:
    methods: list[MethodFPR] = field(default_factory=list)

    def summary(self) -> None:
        console = Console(legacy_windows=False)
        table = Table(title="A/A валидация: эмпирический FPR")
        table.add_column("Метрика")
        table.add_column("Группа")
        table.add_column("Метод")
        table.add_column("n_sims")
        table.add_column("FPR")
        table.add_column("ДИ (95%)")
        table.add_column("Статус")
        for m in self.methods:
            table.add_row(
                m.metric,
                m.treatment_group,
                m.method,
                str(m.n_sims),
                f"{m.fpr:.2%}",
                f"[{m.ci_low:.2%}, {m.ci_high:.2%}]",
                "ок" if m.passed else "ПРОВАЛ",
            )
        console.print(table)


def run_aa(
    data: pd.DataFrame,
    config: DesignConfig,
    n_sims: int = 2000,
    compare_methods: bool = False,
    seed: int | None = None,
    n_jobs: int = 1,
    show_progress: bool = True,
    progress_callback: Callable[[int, int], None] | None = None,
) -> AAReport:
    """A/A симуляция: n_sims раз фейковый сплит по конфигу -> полный пайплайн -> p-value.

    Критерий провала метода: 95%-ный ДИ эмпирического FPR не накрывает config.alpha.
    progress_callback(completed, total) вызывается после каждого раунда при n_jobs=1
    (например, для st.progress в Streamlit).
    """
    control_name = infer_control_name(config.groups)
    treatment_names = [g for g in config.groups if g != control_name]
    chains_by_metric = _build_chains_by_metric(config, compare_methods)

    rng = np.random.default_rng(seed)
    seeds = rng.integers(0, 2**32 - 1, size=n_sims)

    rounds = _run_rounds(
        data, config, control_name, treatment_names, chains_by_metric, seeds,
        effect=None, n_jobs=n_jobs, show_progress=show_progress, description="A/A симуляции",
        progress_callback=progress_callback,
    )

    p_values: dict[tuple[str, str, str], list[float]] = {}
    for round_result in rounds:
        for key, p_value in round_result.items():
            p_values.setdefault(key, []).append(p_value)

    methods_report = []
    for (metric_name, chain_name, treat_name), pvals in p_values.items():
        n = len(pvals)
        n_rejected = sum(1 for p in pvals if p < config.alpha)
        fpr = n_rejected / n
        ci_low, ci_high = proportion_confint(n_rejected, n, alpha=0.05, method="wilson")
        methods_report.append(
            MethodFPR(
                method=chain_name,
                metric=metric_name,
                treatment_group=treat_name,
                n_sims=n,
                fpr=fpr,
                ci_low=float(ci_low),
                ci_high=float(ci_high),
                passed=bool(ci_low <= config.alpha <= ci_high),
            )
        )
    return AAReport(methods=methods_report)


@dataclass
class MethodPower:
    """Эмпирическая (и, где возможно, аналитическая) мощность одной цепочки методов."""

    method: str
    metric: str
    treatment_group: str
    n_sims: int
    empirical_power: float
    analytical_power: float | None
    discrepancy_warning: str | None


@dataclass
class ABReport:
    methods: list[MethodPower] = field(default_factory=list)

    def summary(self) -> None:
        console = Console(legacy_windows=False)
        table = Table(title="A/B валидация: эмпирическая мощность")
        table.add_column("Метрика")
        table.add_column("Группа")
        table.add_column("Метод")
        table.add_column("n_sims")
        table.add_column("Мощность (эмп.)")
        table.add_column("Мощность (аналит.)")
        for m in self.methods:
            table.add_row(
                m.metric,
                m.treatment_group,
                m.method,
                str(m.n_sims),
                f"{m.empirical_power:.2%}",
                f"{m.analytical_power:.2%}" if m.analytical_power is not None else "-",
            )
        console.print(table)
        warnings = [m.discrepancy_warning for m in self.methods if m.discrepancy_warning]
        if warnings:
            console.print("[yellow]Предупреждения:[/yellow]")
            for w in warnings:
                console.print(f"  - {w}")


def run_ab(
    data: pd.DataFrame,
    config: DesignConfig,
    n_sims: int = 2000,
    effect: float = 0.05,
    compare_methods: bool = False,
    seed: int | None = None,
    n_jobs: int = 1,
    show_progress: bool = True,
    progress_callback: Callable[[int, int], None] | None = None,
) -> ABReport:
    """A/B симуляция: n_sims раз фейковый сплит + инъекция эффекта -> эмпирическая мощность.

    Сверяется с аналитической мощностью из power.py; расхождение > 5 п.п. — warning.
    progress_callback(completed, total) вызывается после каждого раунда при n_jobs=1.
    """
    control_name = infer_control_name(config.groups)
    treatment_names = [g for g in config.groups if g != control_name]
    chains_by_metric = _build_chains_by_metric(config, compare_methods)

    rng = np.random.default_rng(seed)
    seeds = rng.integers(0, 2**32 - 1, size=n_sims)

    rounds = _run_rounds(
        data, config, control_name, treatment_names, chains_by_metric, seeds,
        effect=effect, n_jobs=n_jobs, show_progress=show_progress, description="A/B симуляции",
        progress_callback=progress_callback,
    )

    p_values: dict[tuple[str, str, str], list[float]] = {}
    for round_result in rounds:
        for key, p_value in round_result.items():
            p_values.setdefault(key, []).append(p_value)

    metrics_by_name = {m.name: m for m in config.metrics}
    control_prop = config.groups[control_name]
    n_control_expected = len(data) * control_prop

    methods_report = []
    for (metric_name, chain_name, treat_name), pvals in p_values.items():
        n = len(pvals)
        n_rejected = sum(1 for p in pvals if p < config.alpha)
        empirical_power = n_rejected / n

        metric = metrics_by_name[metric_name]
        analytical_power = None
        if metric.type in ("binary", "continuous"):
            try:
                baseline = metric_history_values(metric, data)
                if metric.type == "binary":
                    p_control = float(baseline.mean())
                    p_treat = float(np.clip(p_control * (1 + effect), 0, 1))
                    analytical_power = power.power_given_n_binary(
                        p_control, p_treat, n_control_expected, alpha=config.alpha
                    )
                else:
                    mean_val, std_val = float(baseline.mean()), float(baseline.std(ddof=1))
                    mde_abs = abs(effect * mean_val)
                    analytical_power = power.power_given_n_continuous(
                        std_val, mde_abs, n_control_expected, alpha=config.alpha
                    )
            except (ValueError, KeyError):
                analytical_power = None

        discrepancy_warning = None
        if analytical_power is not None and abs(empirical_power - analytical_power) > 0.05:
            discrepancy_warning = (
                f"{metric_name} ({chain_name}, {treat_name}): эмпирическая мощность "
                f"{empirical_power:.1%} расходится с аналитической {analytical_power:.1%} "
                "больше чем на 5 п.п."
            )

        methods_report.append(
            MethodPower(
                method=chain_name,
                metric=metric_name,
                treatment_group=treat_name,
                n_sims=n,
                empirical_power=empirical_power,
                analytical_power=analytical_power,
                discrepancy_warning=discrepancy_warning,
            )
        )
    return ABReport(methods=methods_report)
