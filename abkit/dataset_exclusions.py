"""Per-column exclusion list on datasets (Part 2: removable columns).

A dataset can carry a persisted list of EXCLUDED column names. The physical
file (upload) or the SQL snapshot is never rewritten — the exclusion is applied
lazily wherever the data is read/materialized, so it works identically for
upload/SQL/demo sources and, crucially, survives an SQL Refresh: the re-fetch
brings the column back into the raw snapshot, and the stored exclusion simply
re-applies (reconcile below). Excluded columns can be restored later by taking
them off the list.

Single source of the two operations — apply (on read) and reconcile (on
refresh) — dependency-light so every dataset read/write path can import it,
same shape as abkit/dataset_categorical.py.
"""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


def apply_column_exclusions(df: pd.DataFrame, excluded: Iterable[str] | None) -> pd.DataFrame:
    """Drop the excluded columns from a freshly-read dataset frame.

    Names not present in the frame (already gone, renamed, or never there) are
    silently ignored — the exclusion list is advisory, not a schema contract,
    and a stale name must not raise. Returns df unchanged when nothing to drop
    (no copy, cheap on the hot read path)."""
    if not excluded:
        return df
    to_drop = [c for c in excluded if c in df.columns]
    return df.drop(columns=to_drop) if to_drop else df


def visible_columns(all_columns: Iterable[str], excluded: Iterable[str] | None) -> list[str]:
    """The columns a dataset presents to the app (pickers, preview): the full
    physical column list minus the excluded ones, order preserved."""
    excl = set(excluded or [])
    return [c for c in all_columns if c not in excl]


def reconcile_excluded_columns(
    old_excluded: list[str] | None, new_columns: Iterable[str]
) -> list[str]:
    """SQL Refresh: keep the user's exclusion for every column that still
    exists in the re-fetched data, drop exclusions for columns that vanished.
    A genuinely NEW column is never auto-excluded (unlike categorical, there is
    no heuristic here — exclusion is always an explicit user choice). Order
    follows the previous exclusion list."""
    present = set(new_columns)
    return [c for c in (old_excluded or []) if c in present]
