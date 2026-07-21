"""Part 2: per-column categorical flag — the heuristic default, the SQL-refresh
reconcile, and category-vs-binned stratification with human-readable interval
labels (never raw pandas "(0.999, 2.0]")."""

from __future__ import annotations

import numpy as np
import pandas as pd

from abkit.dataset_categorical import (
    default_categorical_columns,
    reconcile_categorical_columns,
    resolve_categorical_columns,
)
from abkit.design.stratification import bucket_column, build_strata

_INTERVAL_CHARS = ("(", ")", "[", "]", ".999")


def test_default_heuristic_string_and_bool_categorical():
    df = pd.DataFrame({
        "country": ["US", "UK", "US"],
        "active": [True, False, True],
    })
    assert set(default_categorical_columns(df)) == {"country", "active"}


def test_default_heuristic_numeric_boundary_19_and_21_distinct():
    # 19 distinct -> categorical.
    few = pd.DataFrame({"few": [i % 19 for i in range(100)]})
    assert "few" in default_categorical_columns(few)
    # 21 distinct -> continuous (not categorical).
    many = pd.DataFrame({"many": [i % 21 for i in range(100)]})
    assert "many" not in default_categorical_columns(many)


def test_default_heuristic_20_distinct_is_categorical():
    df = pd.DataFrame({"v": list(range(20)) + [0]})  # exactly 20
    assert "v" in default_categorical_columns(df)


def test_reconcile_keeps_existing_flag_adds_new_drops_vanished():
    old_columns = ["country", "platform", "gone"]
    old_categorical = ["country", "gone"]  # platform explicitly NOT categorical
    new_df = pd.DataFrame({
        "country": ["US", "UK"],
        "platform": [1000, 2000],          # existing, stays unflagged
        "device": ["ios", "android"],      # NEW string -> heuristic categorical
    })
    result = reconcile_categorical_columns(old_columns, old_categorical, new_df)
    assert "country" in result       # existing flag preserved
    assert "platform" not in result  # existing non-flag preserved
    assert "device" in result        # new column -> heuristic
    assert "gone" not in result      # vanished column dropped


def test_resolve_uses_stored_when_present_else_heuristic():
    df = pd.DataFrame({"n": [1, 2, 3]})
    assert resolve_categorical_columns(["explicit"], df) == {"explicit"}
    assert resolve_categorical_columns(None, df) == {"n"}


def test_flagged_numeric_column_stratifies_per_value_with_raw_labels():
    # months_ago ∈ {1,2,3,5} — the motivating bug. Flagged categorical → each
    # value is its own stratum, labeled with the raw value.
    series = pd.Series([1, 2, 3, 5] * 10)
    bucketed = bucket_column(series, n_buckets=4, categorical=True)
    assert set(bucketed.unique()) == {"1", "2", "3", "5"}


def test_unflagged_numeric_bins_with_human_labels_no_interval_syntax():
    series = pd.Series(np.arange(1000, 5000, 1))  # continuous, high cardinality
    bucketed = bucket_column(series, n_buckets=4, categorical=False)
    labels = set(bucketed.unique())
    assert len(labels) > 1  # actually binned
    for label in labels:
        for bad in _INTERVAL_CHARS:
            assert bad not in label, f"raw interval syntax {bad!r} leaked into {label!r}"


def test_build_strata_categorical_flag_produces_per_value_strata():
    df = pd.DataFrame({"months_ago": [1, 2, 3, 5] * 25})
    stratum = build_strata(df, ["months_ago"], categorical_cols=frozenset({"months_ago"}))
    assert set(stratum.unique()) == {"1", "2", "3", "5"}


def test_build_strata_unflagged_numeric_bins_with_clean_labels():
    df = pd.DataFrame({"income": np.arange(1000, 9000, 1)})
    stratum = build_strata(df, ["income"], categorical_cols=frozenset())
    for label in stratum.unique():
        for bad in _INTERVAL_CHARS:
            assert bad not in label


def test_segment_cut_respects_categorical_flag(tmp_path):
    """An ad-hoc segment cut on a flagged numeric column is broken down
    per-value (not binned) — the flag threads from the router into analyze()."""
    from pathlib import Path

    from abkit.config import DesignConfig, MetricConfig
    from abkit.experiment import Experiment

    cfg = DesignConfig(
        name="segflag", unit_col="", groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="conv", type="binary")],
        split_source="external", isolation="off",
    )
    exp = Experiment.design_external(cfg, experiments_dir=Path(tmp_path))
    rng = np.random.default_rng(0)
    rows = []
    for m in (1, 2, 3, 5):
        for _ in range(60):
            rows.append({"variant": "A", "conv": int(rng.binomial(1, 0.1)), "months_ago": m})
            rows.append({"variant": "B", "conv": int(rng.binomial(1, 0.2)), "months_ago": m})
    df = pd.DataFrame(rows)
    res = exp.analyze(
        df, correction="none", group_column="variant",
        group_mapping={"A": "control", "B": "treatment"},
        segment_columns=["months_ago"], categorical_columns=["months_ago"],
    )
    seg = res.context["segment_results_by_dimension"]["months_ago"]["conv"]["treatment"]
    assert {name for name, _ in seg} == {"1", "2", "3", "5"}
