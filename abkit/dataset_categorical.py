"""Per-column categorical flag on datasets (Part 2).

Motivating bug: stratifying by an integer column with a handful of meaningful
values (months_ago ∈ {1,2,3,5}) produced pd.cut interval strata like
"(0.999, 2.0]" — 1 and 2 merged, raw pandas interval labels leaked into the
report. Root cause: a column's NATURE (categorical vs continuous) was inferred
from dtype alone. The fix makes it an explicit, user-editable dataset property;
this module is the single source of the DEFAULT heuristic and the refresh
reconcile, dependency-light so every dataset-write path can import it.
"""

from __future__ import annotations

import pandas as pd

# A numeric column with at most this many distinct values defaults to
# categorical (each value is a meaningful bucket, not a point on a continuum).
CATEGORICAL_MAX_DISTINCT = 20


def default_categorical_columns(df: pd.DataFrame) -> list[str]:
    """Heuristic default: string/bool columns are categorical; a numeric column
    is categorical only when it has <= CATEGORICAL_MAX_DISTINCT distinct values.
    Order follows the DataFrame's columns."""
    out: list[str] = []
    for col in df.columns:
        series = df[col]
        if pd.api.types.is_bool_dtype(series):
            out.append(col)
        elif pd.api.types.is_numeric_dtype(series):
            if series.nunique(dropna=True) <= CATEGORICAL_MAX_DISTINCT:
                out.append(col)
        else:
            out.append(col)
    return out


def reconcile_categorical_columns(
    old_columns: list[str] | None,
    old_categorical: list[str] | None,
    df: pd.DataFrame,
) -> list[str]:
    """SQL Refresh: keep the user's flag for columns that still exist, apply the
    heuristic to NEW columns, and drop the flag of columns that vanished. A
    column that existed before and was NOT flagged stays unflagged (an explicit
    user choice is preserved), only genuinely new columns get the heuristic."""
    old_set = set(old_columns or [])
    prev_cat = set(old_categorical or [])
    heuristic = set(default_categorical_columns(df))
    result: list[str] = []
    for col in df.columns:
        if col in old_set:
            if col in prev_cat:
                result.append(col)
        elif col in heuristic:
            result.append(col)
    return result


def resolve_categorical_columns(stored: list[str] | None, df: pd.DataFrame) -> set[str]:
    """The effective categorical set for a dataset at use time: the stored,
    user-resolved list if present, else the heuristic computed from the data
    (lazy backfill for datasets created before this feature — no migration
    backfill needed, the heuristic is applied on first design/analyze read)."""
    if stored is not None:
        return set(stored)
    return set(default_categorical_columns(df))
