"""Regression: an ad-hoc segment column selected on Analyze must NOT be
silently dropped from the results.

Root cause: an undeclared segment column ('channel') whose per-segment cells
were all too small (each cell < 2 users/group -> AnalysisError, a ValueError
subclass -> caught by the per-dimension loop's `except ValueError`), or which
had a single distinct value (the `< 2 distinct` guard), produced no entry in
segment_results_by_dimension AND wrote no warning — so the column just vanished
from the report. Contract enforced now: every requested cut either appears OR
gets a structured skip notice (context['segment_skips']) naming the column and
the reason.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import Experiment


# ---------------------------------------------------------------- external ---

def _external(tmp_path):
    cfg = DesignConfig(
        name="seg_skip", unit_col="", groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous", role="primary")],
        split_source="external", isolation="off",
    )
    return Experiment.design_external(cfg, experiments_dir=Path(tmp_path))


def _rows(channels, per, rng, gender=False):
    rows = []
    for ch in channels:
        for _ in range(per):
            for v in ("A", "B"):
                r = {"variant": v, "revenue": float(rng.normal(100, 15)), "channel": ch}
                if gender:
                    r["gender"] = "m" if rng.random() < 0.5 else "f"
                rows.append(r)
    return rows


def _analyze(exp, df, **kw):
    return exp.analyze(
        df, correction="none", group_column="variant",
        group_mapping={"A": "control", "B": "treatment"}, **kw,
    )


def test_external_adhoc_good_column_produces_breakdown_no_skip(tmp_path):
    exp = _external(tmp_path)
    df = pd.DataFrame(_rows(["organic", "paid", "referral"], 50, np.random.default_rng(0)))
    res = _analyze(exp, df, segment_columns=["channel"])
    assert "channel" in res.context["segment_results_by_dimension"]
    assert "channel" in res.context["ad_hoc_segment_dimensions"]
    assert res.context["segment_skips"] == []


def test_external_adhoc_single_value_is_skip_notice_not_silent(tmp_path):
    exp = _external(tmp_path)
    df = pd.DataFrame(_rows(["organic"], 100, np.random.default_rng(0)))
    res = _analyze(exp, df, segment_columns=["channel"])
    assert "channel" not in res.context["segment_results_by_dimension"]
    skips = {s["label"]: s["reason"] for s in res.context["segment_skips"]}
    assert "channel" in skips
    assert "one distinct value" in skips["channel"]


def test_external_adhoc_all_cells_too_small_is_skip_notice(tmp_path):
    # High-cardinality channel: each value has ~1 unit/group -> every cell
    # degenerate -> the exact silent-drop the bug report hit.
    exp = _external(tmp_path)
    rng = np.random.default_rng(0)
    rows = [
        {"variant": v, "revenue": float(rng.normal(100, 15)), "channel": f"camp_{i}"}
        for i in range(200) for v in ("A", "B")
    ]
    res = _analyze(exp, pd.DataFrame(rows), segment_columns=["channel"])
    assert "channel" not in res.context["segment_results_by_dimension"]
    skips = {s["label"]: s["reason"] for s in res.context["segment_skips"]}
    assert "channel" in skips
    assert "enough data" in skips["channel"]


def test_external_adhoc_missing_column_is_skip_notice(tmp_path):
    exp = _external(tmp_path)
    df = pd.DataFrame(_rows(["organic", "paid"], 50, np.random.default_rng(0)))
    res = _analyze(exp, df, segment_columns=["nonexistent"])
    skips = {s["label"]: s["reason"] for s in res.context["segment_skips"]}
    assert "nonexistent" in skips
    assert "not in the analysis dataset" in skips["nonexistent"]


def test_external_adhoc_combination_channel_x_gender(tmp_path):
    exp = _external(tmp_path)
    df = pd.DataFrame(_rows(["organic", "paid"], 80, np.random.default_rng(1), gender=True))
    res = _analyze(exp, df, segment_columns=["channel"], segment_combinations=[["channel", "gender"]])
    produced = set(res.context["segment_results_by_dimension"])
    skipped = {s["label"] for s in res.context["segment_skips"]}
    # The combination either produced a breakdown or is explained — never absent.
    combo_label = "channel × gender"
    assert combo_label in produced or combo_label in skipped
    # And the ad-hoc single column is present in either produced or skips too.
    assert "channel" in produced or "channel" in skipped


# -------------------------------------------------------------------- ABSet ---

def _abset(tmp_path, n=4000):
    rng = np.random.default_rng(0)
    design_data = pd.DataFrame({"user_id": [f"u{i}" for i in range(n)], "revenue": rng.normal(100, 20, n)})
    cfg = DesignConfig(
        name="abset_seg_skip", unit_col="user_id", groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous")], sample_size=n,
        split_method="simple", seed=42,
    )
    return Experiment.design(cfg, design_data, experiments_dir=tmp_path)


def _abset_post(exp, channel_values):
    a = exp.assignments
    n = len(a)
    rng = np.random.default_rng(7)
    return pd.DataFrame({
        "user_id": a["unit_id"].to_numpy(),
        "revenue": rng.normal(100, 20, n),
        "channel": [channel_values[i % len(channel_values)] for i in range(n)],
    })


def test_abset_adhoc_good_column_produces_breakdown(tmp_path):
    exp = _abset(tmp_path)
    post = _abset_post(exp, ["organic", "paid", "referral"])
    res = exp.analyze(post, segment_columns=["channel"])
    assert "channel" in res.context["segment_results_by_dimension"]
    assert "channel" in res.context["ad_hoc_segment_dimensions"]
    assert res.context["segment_skips"] == []


def test_abset_adhoc_single_value_is_skip_notice(tmp_path):
    exp = _abset(tmp_path)
    post = _abset_post(exp, ["organic"])
    res = exp.analyze(post, segment_columns=["channel"])
    assert "channel" not in res.context["segment_results_by_dimension"]
    skips = {s["label"]: s["reason"] for s in res.context["segment_skips"]}
    assert "channel" in skips and "one distinct value" in skips["channel"]


# ------------------------------------------------------------- chart payload ---

def test_chart_data_carries_segment_skips(tmp_path):
    from backend.chart_data import build_chart_data

    exp = _external(tmp_path)
    df = pd.DataFrame(_rows(["organic"], 100, np.random.default_rng(0)))
    res = _analyze(exp, df, segment_columns=["channel"])
    chart = build_chart_data(res)
    assert any(s["label"] == "channel" for s in chart["segment_skips"])


def test_analysis_report_renders_skip_notice(tmp_path):
    from abkit.viz.report import render_analysis_report

    exp = _external(tmp_path)
    df = pd.DataFrame(_rows(["organic"], 100, np.random.default_rng(0)))
    res = _analyze(exp, df, segment_columns=["channel"])
    html = render_analysis_report(res, res.context)
    assert "Requested segment cuts not shown" in html
    assert "channel" in html
    assert "one distinct value" in html
