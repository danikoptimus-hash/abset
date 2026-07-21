"""Построение страт для стратифицированного сплита."""

from __future__ import annotations

import pandas as pd

_OTHER_STRATUM = "_other_"
_NO_STRATA_VALUE = "_all_"
_UNKNOWN_VALUE = "unknown"


def _format_edge(x: float) -> str:
    """Human-readable bin edge: an integer-valued float prints without a
    trailing ".0" (1000 not 1000.0), otherwise 4 significant figures."""
    if float(x) == int(x):
        return str(int(x))
    return f"{x:.4g}"


def _binned_labels(series: pd.Series, n_buckets: int) -> pd.Series:
    """Quantile-bucket a continuous column with HUMAN-READABLE range labels
    ("1000–2000", "2000–3000") instead of pandas' raw interval strings
    ("(0.999, 2.0]"). The single place strata/segment labels are produced, so
    the design report, balance tables, and segment blocks all inherit it."""
    try:
        binned = pd.qcut(series, q=n_buckets, duplicates="drop")
    except (ValueError, IndexError):
        return series.astype(str)
    cats = list(binned.cat.categories)
    if not cats:
        return series.astype(str)
    lo, hi = series.min(), series.max()
    labels: list[str] = []
    seen: dict[str, int] = {}
    for i, iv in enumerate(cats):
        left = lo if i == 0 else iv.left
        right = hi if i == len(cats) - 1 else iv.right
        label = f"{_format_edge(left)}–{_format_edge(right)}"
        # Guarantee uniqueness (rounding could collide adjacent narrow bins) —
        # rename_categories requires distinct labels.
        if label in seen:
            seen[label] += 1
            label = f"{label} ({seen[label]})"
        else:
            seen[label] = 0
        labels.append(label)
    return binned.cat.rename_categories(labels).astype(str)


def bucket_column(series: pd.Series, n_buckets: int, categorical: bool = False) -> pd.Series:
    """Бакетирует непрерывную колонку по квантилям; категориальные оставляет как есть.

    Пропуски (NaN) заменяются на строку "unknown" — юзеры с пропусками в этой
    колонке попадают в собственную (под)страту, а не приводят к ошибке.

    Публичная (была module-private _bucket_column) — item 2 (strata power
    check) переиспользует ее напрямую для бакетирования КАЖДОГО измерения
    страт ПО ОТДЕЛЬНОСТИ (не только их декартова произведения, как здесь в
    build_strata), той же логикой, что и реальный сплит.

    categorical (Part 2): when True the column is treated as categories — each
    raw value is its own bucket, label = the raw value ("1", "2", "3", "5") —
    regardless of dtype. This is how a numeric column the user flagged
    categorical (months_ago) avoids being binned into interval strata. When
    False, a high-cardinality numeric column is quantile-binned with
    human-readable range labels (see _binned_labels).
    """
    nan_mask = series.isna()
    if not categorical and pd.api.types.is_numeric_dtype(series) and series.nunique(dropna=True) > n_buckets:
        result = _binned_labels(series, n_buckets)
    else:
        result = series.astype(str)
    return result.where(~nan_mask, _UNKNOWN_VALUE)


def cross_columns(
    data: pd.DataFrame, cols: list[str], n_buckets_continuous: int = 4,
    categorical_cols: frozenset[str] = frozenset(),
) -> pd.Series:
    """Cross 2+ columns into one "|"-joined label per row, for a segment
    COMBINATION cut (country × platform × ...). Same bucketing as build_strata
    (numeric high-cardinality → quantile buckets, NaN → "unknown"), but WITHOUT
    the min_stratum_size collapse to "_other_": a segment breakdown must SHOW
    small/underpowered cells (greyed at render), not hide them. `categorical_cols`
    (Part 2) — columns to treat per-value regardless of dtype. Empty/single
    `cols` isn't a combination — callers guard for len>=2 before calling."""
    bucketed = pd.DataFrame(
        {col: bucket_column(data[col], n_buckets_continuous, categorical=col in categorical_cols) for col in cols},
        index=data.index,
    )
    crossed = bucketed.astype(str).agg("|".join, axis=1)
    crossed.name = "segment"
    return crossed


def nan_counts_by_column(data: pd.DataFrame, strata_cols: list[str]) -> dict[str, int]:
    """Считает число пропусков в каждой стратификационной колонке (для отчета/warning)."""
    return {col: int(data[col].isna().sum()) for col in strata_cols}


def build_strata(
    data: pd.DataFrame,
    strata_cols: list[str],
    n_buckets_continuous: int = 4,
    min_stratum_size: int = 20,
    categorical_cols: frozenset[str] = frozenset(),
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
        {col: bucket_column(data[col], n_buckets_continuous, categorical=col in categorical_cols) for col in strata_cols},
        index=data.index,
    )
    stratum = bucketed.astype(str).agg("|".join, axis=1)
    stratum.name = "stratum"

    counts = stratum.value_counts()
    small_strata = counts[counts < min_stratum_size].index
    if len(small_strata) > 0:
        stratum = stratum.where(~stratum.isin(small_strata), _OTHER_STRATUM)
    return stratum
