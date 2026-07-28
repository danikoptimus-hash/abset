"""Part 2 (removable columns): the exclusion helper — apply on read, compute
the visible column set, and reconcile on SQL refresh."""

from __future__ import annotations

import pandas as pd

from abkit.dataset_exclusions import (
    apply_column_exclusions,
    reconcile_excluded_columns,
    visible_columns,
)


def _df():
    return pd.DataFrame({"user_id": [1, 2], "revenue": [10, 20], "group": ["A", "B"]})


def test_apply_drops_only_listed_columns():
    out = apply_column_exclusions(_df(), ["group"])
    assert list(out.columns) == ["user_id", "revenue"]


def test_apply_none_or_empty_is_noop_same_object():
    df = _df()
    assert apply_column_exclusions(df, None) is df
    assert apply_column_exclusions(df, []) is df


def test_apply_ignores_names_not_present():
    # A stale exclusion name (already renamed/gone) must not raise.
    out = apply_column_exclusions(_df(), ["group", "not_a_column"])
    assert list(out.columns) == ["user_id", "revenue"]


def test_visible_columns_preserves_order():
    assert visible_columns(["a", "b", "c", "d"], ["b", "d"]) == ["a", "c"]
    assert visible_columns(["a", "b"], None) == ["a", "b"]


def test_reconcile_keeps_present_drops_vanished():
    # The KEY case: after a refresh, 'group' still exists -> stays excluded;
    # 'gone' vanished from the source -> its exclusion drops out. A genuinely
    # new column is NEVER auto-excluded.
    result = reconcile_excluded_columns(["group", "gone"], ["user_id", "revenue", "group", "new_col"])
    assert result == ["group"]


def test_reconcile_empty_inputs():
    assert reconcile_excluded_columns(None, ["a", "b"]) == []
    assert reconcile_excluded_columns([], ["a"]) == []
