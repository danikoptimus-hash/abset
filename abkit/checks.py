"""Проверки честности сплита и данных: SRM, баланс страт, pre-period A/A,
дубли, join с назначениями, потери данных при анализе."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

from abkit.config import MetricConfig
from abkit.idnorm import normalize_id_series


@dataclass
class SRMResult:
    """Sample Ratio Mismatch: сверка фактических долей групп с заявленными."""

    chi2: float
    p_value: float
    passed: bool  # False = обнаружен SRM (провал проверки)
    observed: dict[str, int]
    expected: dict[str, float]


def check_srm(
    observed: dict[str, int],
    expected_ratios: dict[str, float],
    threshold: float = 0.001,
) -> SRMResult:
    """Chi-square тест фактических долей групп против заявленных. p < threshold — SRM."""
    names = list(expected_ratios.keys())
    obs = np.array([observed.get(name, 0) for name in names], dtype=float)
    total = obs.sum()
    expected = np.array([expected_ratios[name] * total for name in names])
    chi2, p_value = sp_stats.chisquare(obs, expected)
    return SRMResult(
        chi2=float(chi2),
        p_value=float(p_value),
        passed=bool(p_value >= threshold),
        observed={name: int(obs[i]) for i, name in enumerate(names)},
        expected={name: float(expected[i]) for i, name in enumerate(names)},
    )


@dataclass
class BalanceResult:
    """Баланс распределения страт между группами."""

    chi2: float
    p_value: float
    passed: bool
    table: pd.DataFrame


def check_strata_balance(
    stratum: pd.Series, group: pd.Series, alpha: float = 0.05
) -> BalanceResult:
    """Chi-square тест таблицы сопряженности stratum x group."""
    table = pd.crosstab(stratum, group)
    if table.shape[0] < 2 or table.shape[1] < 2:
        # одна страта или одна группа — тест вырожден, баланс тривиально соблюден
        return BalanceResult(chi2=0.0, p_value=1.0, passed=True, table=table)
    chi2, p_value, _dof, _expected = sp_stats.chi2_contingency(table)
    return BalanceResult(
        chi2=float(chi2), p_value=float(p_value), passed=bool(p_value >= alpha), table=table
    )


def strata_balance_rows(result: BalanceResult) -> list[dict]:
    """JSON-safe per-stratum-per-group counts from check_strata_balance's
    crosstab (6-part package pt.10: "таблица баланса страт по группам, если
    ее нет в отчете — считается в проверках сплита, вывести") — the table
    was already computed for the chi2 test, just never surfaced beyond the
    single pass/fail badge. One flat dict per stratum: {"stratum": ...,
    "<group>": count, ...}; used by both design_report.html and the
    computed summary persisted for the Design tab."""
    return [
        {"stratum": str(idx), **{str(g): int(n) for g, n in row.items()}}
        for idx, row in result.table.iterrows()
    ]


def strata_balance_groups(result: BalanceResult) -> list[str]:
    """Column order for strata_balance_rows — kept separate since a flat
    dict per row doesn't preserve group order on its own."""
    return [str(g) for g in result.table.columns]


@dataclass
class AAResult:
    """Pre-period A/A тест по одной метрике между control и конкретной treatment-группой."""

    metric: str
    treatment_group: str
    p_value: float
    passed: bool
    mean_control: float
    mean_treatment: float


def check_pre_period_aa(
    data: pd.DataFrame,
    group: pd.Series,
    metrics: list[MetricConfig],
    control_name: str,
    alpha: float = 0.05,
) -> list[AAResult]:
    """Welch t-test по pre_col каждой метрики между control и каждой treatment-группой.

    Значимое различие на pre-period данных до эксперимента — красный флаг (сплит
    не рандомизирован честно, либо страты выбраны неудачно).
    """
    results: list[AAResult] = []
    treatment_names = [g for g in group.unique() if g != control_name]
    control_mask = group == control_name

    for metric in metrics:
        if not metric.pre_col:
            continue
        control_vals = data.loc[control_mask, metric.pre_col].dropna()
        for treat_name in treatment_names:
            treat_vals = data.loc[group == treat_name, metric.pre_col].dropna()
            if len(control_vals) < 2 or len(treat_vals) < 2:
                continue
            _stat, p_value = sp_stats.ttest_ind(control_vals, treat_vals, equal_var=False)
            results.append(
                AAResult(
                    metric=metric.name,
                    treatment_group=treat_name,
                    p_value=float(p_value),
                    passed=bool(p_value >= alpha),
                    mean_control=float(control_vals.mean()),
                    mean_treatment=float(treat_vals.mean()),
                )
            )
    return results


class AnalysisError(Exception):
    """Пользовательская ошибка на этапе анализа (не баг): нечестные/некорректные данные."""


def check_no_duplicates(data: pd.DataFrame, unit_col: str) -> None:
    """Дубли unit_col в фактических данных — фатальная ошибка анализа."""
    dup_mask = data[unit_col].duplicated()
    if dup_mask.any():
        raise AnalysisError(
            f"The data has {int(dup_mask.sum())} duplicate '{unit_col}' values — cannot analyze"
        )


_RESERVED_ASSIGNMENT_COLUMNS = ("group", "stratum", "assigned_at")


def join_with_assignments(
    assignments: pd.DataFrame, data: pd.DataFrame, unit_col: str
) -> pd.DataFrame:
    """Inner join фактических данных с назначениями групп по unit_col.

    Перед джойном проверяет отсутствие дублей в данных (assignments по построению
    уникальны — гарантируется storage/splitter). Ключ с обеих сторон
    приводится к str (astype+strip) перед merge — ID это идентификатор, а не
    число, и старые assignments.parquet (файловый режим, до этой правки)
    могут все еще хранить unit_id числовым.

    Regression (ref edb716f1): `assignments` always has `group`/`stratum`/
    `assigned_at` columns. If the uploaded post-period `data` happens to
    carry a column with one of those same names (e.g. exporting your own
    "group" column alongside the metrics), pandas' merge silently renames
    BOTH sides' copies to `<name>_x`/`<name>_y` instead of erroring — so
    `merged["group"]` below (and everywhere downstream) raised a raw,
    unguarded `KeyError: 'group'` that surfaced only as an opaque "Internal
    processing error". Reject the collision up front with an actionable
    message instead.
    """
    check_no_duplicates(data, unit_col)
    collisions = [c for c in _RESERVED_ASSIGNMENT_COLUMNS if c in data.columns]
    if collisions:
        cols = ", ".join(repr(c) for c in collisions)
        raise AnalysisError(
            f"The uploaded data has column(s) {cols} that collide with ABSet's own "
            "group-assignment columns of the same name (recorded at design time). "
            "Rename or drop them in your post-period dataset before analyzing."
        )
    assignments = assignments.assign(unit_id=normalize_id_series(assignments["unit_id"]))
    data = data.assign(**{unit_col: normalize_id_series(data[unit_col])})
    return assignments.merge(data, left_on="unit_id", right_on=unit_col, how="inner")


@dataclass
class LossResult:
    """Потери данных: доля назначенных юзеров, отсутствующих в фактических данных."""

    assigned: dict[str, int]
    present: dict[str, int]
    missing: dict[str, int]
    missing_rate: dict[str, float]
    chi2: float
    p_value: float
    symmetric: bool  # True = потери по группам статистически неотличимы (честно)


def check_data_loss(
    assignments: pd.DataFrame, present_unit_ids: pd.Series, alpha: float = 0.05
) -> LossResult:
    """Сверяет назначенных юзеров с фактически присутствующими в данных по группам.

    Асимметричные потери (chi-square на таблице [присутствует, отсутствует] x group)
    — красный флаг: например, группа treatment теряет данные иначе, чем control.
    """
    assigned_counts = assignments["group"].value_counts().to_dict()
    present_set = set(normalize_id_series(pd.Series(present_unit_ids)))
    present_mask = normalize_id_series(assignments["unit_id"]).isin(present_set)
    present_counts = assignments.loc[present_mask, "group"].value_counts().to_dict()

    names = list(assigned_counts.keys())
    present_arr = [present_counts.get(name, 0) for name in names]
    missing_arr = [assigned_counts[name] - present_counts.get(name, 0) for name in names]

    missing = dict(zip(names, missing_arr))
    missing_rate = {
        name: (missing[name] / assigned_counts[name] if assigned_counts[name] else 0.0)
        for name in names
    }

    table = np.array([present_arr, missing_arr])
    if table.shape[1] < 2 or (table.sum(axis=0) == 0).any() or (table.sum(axis=1) == 0).any():
        chi2, p_value = 0.0, 1.0
    else:
        chi2, p_value, _dof, _expected = sp_stats.chi2_contingency(table)

    return LossResult(
        assigned=assigned_counts,
        present=dict(zip(names, present_arr)),
        missing=missing,
        missing_rate=missing_rate,
        chi2=float(chi2),
        p_value=float(p_value),
        symmetric=bool(p_value >= alpha),
    )
