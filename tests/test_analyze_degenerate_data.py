"""Regression for the analyze 500 crash (ref 0f63af75) + the adjacent
degenerate-data paths audited alongside it.

Root cause: a per-segment Welch t-test on a segment where BOTH groups had zero
variance (every value identical) divided 0/0 in _welch_df -> raw
ZeroDivisionError, which the segment loop's `except ValueError` did not catch,
so it aborted the whole analysis as "Internal processing error (ref: ...)".

Fix: the degenerate statistical cases now raise AnalysisError (a ValueError
subclass) with a user-facing message — so a per-segment/per-day breakdown skips
the degenerate slice, and a whole-metric degeneracy surfaces cleanly instead of
a 500.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from abkit.analysis.tests import WelchTTest, _welch_df
from abkit.checks import AnalysisError
from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import Experiment, build_metric_context
from abkit.pipeline import MetricContext


def _ctx(vals, groups, metric_type="continuous", num=None, den=None):
    return MetricContext(
        metric_name="m", metric_type=metric_type, control_name="control",
        treatment_name="treatment", values=pd.Series(vals), group=pd.Series(groups),
        alpha=0.05, stratum=None, covariate=num if num is None else pd.Series(num),
        num=None if num is None else pd.Series(num), den=None if den is None else pd.Series(den),
        is_designed_method=True, role="primary",
    )


# ---- unit level: the crash line and its siblings ----

def test_analysis_error_is_a_value_error():
    # This subclassing is what makes the segment loops' `except ValueError`
    # skip a degenerate slice while the classifier still treats it as a clean
    # user error — the crux of the fix.
    assert issubclass(AnalysisError, ValueError)


def test_welch_df_zero_denominator_does_not_raise():
    # The literal crash line — must never raise ZeroDivisionError again.
    assert _welch_df(0.0, 5, 0.0, 5) == float("inf")


def test_welch_zero_variance_both_groups_raises_clean_analysis_error():
    g = ["control", "control", "treatment", "treatment"]
    with pytest.raises(AnalysisError, match="no variance"):
        WelchTTest().apply(_ctx([5.0, 5.0, 5.0, 5.0], g))


def test_welch_all_null_after_join_raises_clean_analysis_error():
    g = ["control", "control", "treatment", "treatment"]
    with pytest.raises(AnalysisError, match="not enough"):
        WelchTTest().apply(_ctx([np.nan] * 4, g))


def test_build_metric_context_rejects_non_numeric_metric():
    merged = pd.DataFrame({"group": ["control", "treatment"] * 3, "txt": list("abcdef")})
    with pytest.raises(AnalysisError, match="not numeric"):
        build_metric_context(MetricConfig(name="txt", type="continuous"), merged, "control", "treatment", 0.05, True)


def test_build_metric_context_coerces_numeric_string_metric():
    merged = pd.DataFrame({"group": ["control", "treatment"] * 3, "v": ["1.0", "2.0", "3.0", "4.0", "5.0", "6.0"]})
    ctx = build_metric_context(MetricConfig(name="v", type="continuous"), merged, "control", "treatment", 0.05, True)
    assert pd.api.types.is_numeric_dtype(ctx.values)


# ---- integration: the reported scenario, end to end via analyze() ----

def _external_experiment(tmp_path):
    cfg = DesignConfig(
        name="degenerate_seg", unit_col="", groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous", role="primary")],
        split_source="external", isolation="off",
    )
    return Experiment.design_external(cfg, experiments_dir=Path(tmp_path))


def test_analyze_skips_degenerate_zero_variance_segment(tmp_path):
    """THE regression for ref 0f63af75: one segment is all-constant in both
    groups (zero variance) while the metric has variance overall. Analysis must
    SUCCEED — the degenerate segment is skipped, not a 500."""
    exp = _external_experiment(tmp_path)
    rng = np.random.default_rng(0)
    rows = []
    for _ in range(60):
        # 'flat' region: every unit has revenue == 5.0 in BOTH variants -> the
        # per-segment Welch t-test there divides 0/0 (the crash).
        rows.append({"variant": "A", "revenue": 5.0, "region": "flat"})
        rows.append({"variant": "B", "revenue": 5.0, "region": "flat"})
        # 'varied' region: normal spread -> a computable segment.
        rows.append({"variant": "A", "revenue": float(rng.normal(100, 15)), "region": "varied"})
        rows.append({"variant": "B", "revenue": float(rng.normal(105, 15)), "region": "varied"})
    df = pd.DataFrame(rows)

    res = exp.analyze(
        df, correction="none", group_column="variant",
        group_mapping={"A": "control", "B": "treatment"},
        segment_columns=["region"], categorical_columns=["region"],
    )

    seg = dict(res.context["segment_results_by_dimension"]["region"]["revenue"]["treatment"])
    assert "varied" in seg          # computable segment kept
    assert "flat" not in seg        # degenerate segment skipped, not crashed


def test_analyze_constant_metric_surfaces_clean_error(tmp_path):
    """A metric that is constant across the WHOLE experiment (zero variance in
    both groups everywhere) is not a segment edge case — it surfaces as a clean
    AnalysisError to the user, never a raw ZeroDivisionError / 500."""
    exp = _external_experiment(tmp_path)
    rows = []
    for _ in range(50):
        rows.append({"variant": "A", "revenue": 7.0})
        rows.append({"variant": "B", "revenue": 7.0})
    df = pd.DataFrame(rows)

    with pytest.raises(AnalysisError, match="no variance"):
        exp.analyze(
            df, correction="none", group_column="variant",
            group_mapping={"A": "control", "B": "treatment"},
        )


def test_analyze_non_numeric_metric_surfaces_clean_error(tmp_path):
    exp = _external_experiment(tmp_path)
    rows = []
    for i in range(50):
        rows.append({"variant": "A", "revenue": f"cat_{i % 3}"})
        rows.append({"variant": "B", "revenue": f"cat_{i % 3}"})
    df = pd.DataFrame(rows)

    with pytest.raises(AnalysisError, match="not numeric"):
        exp.analyze(
            df, correction="none", group_column="variant",
            group_mapping={"A": "control", "B": "treatment"},
        )
