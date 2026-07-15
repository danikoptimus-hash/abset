"""Построение страт для стратифицированного сплита."""

from __future__ import annotations

import pandas as pd

_OTHER_STRATUM = "_other_"
_NO_STRATA_VALUE = "_all_"
_UNKNOWN_VALUE = "unknown"


def bucket_column(series: pd.Series, n_buckets: int) -> pd.Series:
    """Бакетирует непрерывную колонку по квантилям; категориальные оставляет как есть.

    Пропуски (NaN) заменяются на строку "unknown" — юзеры с пропусками в этой
    колонке попадают в собственную (под)страту, а не приводят к ошибке.

    Публичная (была module-private _bucket_column) — item 2 (strata power
    check) переиспользует ее напрямую для бакетирования КАЖДОГО измерения
    страт ПО ОТДЕЛЬНОСТИ (не только их декартова произведения, как здесь в
    build_strata), той же логикой, что и реальный сплит.
    """
    nan_mask = series.isna()
    if pd.api.types.is_numeric_dtype(series) and series.nunique(dropna=True) > n_buckets:
        try:
            bucketed = pd.qcut(series, q=n_buckets, duplicates="drop")
            result = bucketed.astype(str)
        except ValueError:
            result = series.astype(str)
    else:
        result = series.astype(str)
    return result.where(~nan_mask, _UNKNOWN_VALUE)


def nan_counts_by_column(data: pd.DataFrame, strata_cols: list[str]) -> dict[str, int]:
    """Считает число пропусков в каждой стратификационной колонке (для отчета/warning)."""
    return {col: int(data[col].isna().sum()) for col in strata_cols}


def build_strata(
    data: pd.DataFrame,
    strata_cols: list[str],
    n_buckets_continuous: int = 4,
    min_stratum_size: int = 20,
) -> pd.Series:
    """Строит колонку stratum: декартово произведение бакетированных страт-колонок.

    Непрерывные колонки (числовые с числом уникальных значений > n_buckets_continuous)
    бьются на квантильные бакеты. Пропуски в любой страта-колонке заменяются на
    "unknown" (юзер попадает в отдельную (под)страту, а не приводит к ошибке —
    отвечает за старое поведение "падать на NaN" параметр nan_strategy в
    DesignConfig, который валидируется до вызова этой функции). Страты размером
    меньше min_stratum_size склеиваются в "_other_".
    """
    if not strata_cols:
        return pd.Series([_NO_STRATA_VALUE] * len(data), index=data.index, name="stratum")

    bucketed = pd.DataFrame(
        {col: bucket_column(data[col], n_buckets_continuous) for col in strata_cols},
        index=data.index,
    )
    stratum = bucketed.astype(str).agg("|".join, axis=1)
    stratum.name = "stratum"

    counts = stratum.value_counts()
    small_strata = counts[counts < min_stratum_size].index
    if len(small_strata) > 0:
        stratum = stratum.where(~stratum.isin(small_strata), _OTHER_STRATUM)
    return stratum
