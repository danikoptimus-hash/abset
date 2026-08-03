"""BUG (confirmed on a live experiment): the segments pipeline sourced its
column set from the DESIGN DECLARATION instead of the analyze REQUEST — it
decomposed every declared stratum regardless of what was requested AND
fabricated a cross of ALL declared strata that the analyst never asked for
('monetary_segment × months_ago × dominant_stream × gender' in the report),
while a legitimately requested ad-hoc column could go missing.

Contract enforced here: the rendered dimension set is EXACTLY the request's set
— the requested single columns (declared defaults + user additions/removals) +
explicitly requested cross-combinations. No design-declared column that wasn't
requested, and NEVER an auto-cross unless it was requested as a combination.

Core-level (Experiment.analyze) coverage — file store, no testcontainers. The
HTTP/chart_data twin lives in backend/tests/test_external_split_job.py; the
undeclared-column-on-Analyze e2e is frontend/e2e/segments.spec.ts.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import Experiment


# ------------------------------------------------------------- external split ---

def _external_cfg(strata, name="seg_req") -> DesignConfig:
    return DesignConfig(
        name=name, unit_col="", groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="conversion", type="binary", role="primary")],
        split_source="external", isolation="off", strata=strata,
    )


def _external_data(rng, *, per_cell=50):
    """A × B declared strata + an undeclared ad-hoc 'channel', all with enough
    rows per cell that every individual breakdown is computable."""
    rows = []
    for a in ["a1", "a2"]:
        for b in ["b1", "b2"]:
            for ch in ["push", "sms", "email"]:
                for _ in range(per_cell):
                    for variant, p in (("X", 0.20), ("Y", 0.25)):
                        rows.append({
                            "variant": variant, "conversion": int(rng.binomial(1, p)),
                            "A": a, "B": b, "channel": ch,
                        })
    return pd.DataFrame(rows)


def _analyze_external(tmp_path, strata, data, **kwargs):
    exp = Experiment.design_external(_external_cfg(strata), experiments_dir=Path(tmp_path))
    return exp.analyze(
        data, correction="none", group_column="variant",
        group_mapping={"X": "control", "Y": "treatment"},
        categorical_columns=["A", "B", "channel"], **kwargs,
    )


def test_external_declared_plus_adhoc_no_autocross(tmp_path):
    """strata=[A,B], request=[A,B,channel] → EXACTLY {A,B,channel}. The
    'A × B' auto-cross the design declaration used to fabricate is GONE."""
    res = _analyze_external(
        tmp_path, ["A", "B"], _external_data(np.random.default_rng(0)),
        segment_columns=["A", "B", "channel"],
    )
    dims = set(res.context["segment_results_by_dimension"])
    assert dims == {"A", "B", "channel"}, dims
    assert res.context["ad_hoc_segment_dimensions"] == ["channel"]
    assert res.context["combination_segment_dimensions"] == []


def test_external_deselecting_a_declared_stratum_is_honored(tmp_path):
    """strata=[A,B] but request drops B → [A,channel]. The rendered set must be
    EXACTLY {A,channel}: B (declared but not requested) must NOT appear, and
    neither may the auto-cross."""
    res = _analyze_external(
        tmp_path, ["A", "B"], _external_data(np.random.default_rng(1)),
        segment_columns=["A", "channel"],
    )
    dims = set(res.context["segment_results_by_dimension"])
    assert dims == {"A", "channel"}, dims


def test_external_only_declared_produces_no_autocross(tmp_path):
    """request=[A,B] (exactly the declared strata) → {A,B}, no 'A × B'."""
    res = _analyze_external(
        tmp_path, ["A", "B"], _external_data(np.random.default_rng(2)),
        segment_columns=["A", "B"],
    )
    assert set(res.context["segment_results_by_dimension"]) == {"A", "B"}


def test_external_default_none_is_declared_strata_without_autocross(tmp_path):
    """segment_columns=None still DEFAULTS to the declared strata (each
    individually) — but without the fabricated cross."""
    res = _analyze_external(
        tmp_path, ["A", "B"], _external_data(np.random.default_rng(3)),
    )
    assert set(res.context["segment_results_by_dimension"]) == {"A", "B"}


def test_external_explicit_combination_is_respected_verbatim(tmp_path):
    """The cross appears ONLY when explicitly requested as a combination —
    then verbatim, badged as a combination."""
    res = _analyze_external(
        tmp_path, ["A", "B"], _external_data(np.random.default_rng(4)),
        segment_columns=["A", "B"], segment_combinations=[["A", "B"]],
    )
    dims = set(res.context["segment_results_by_dimension"])
    assert dims == {"A", "B", "A × B"}, dims
    assert res.context["combination_segment_dimensions"] == ["A × B"]


# -------------------------------------------------------------------- ABSet ---

def _abset(tmp_path, strata, n=4000):
    rng = np.random.default_rng(0)
    design_data = pd.DataFrame({
        "user_id": [f"u{i}" for i in range(n)],
        "revenue": rng.normal(100, 20, n),
        "A": rng.choice(["a1", "a2"], n),
        "B": rng.choice(["b1", "b2"], n),
    })
    cfg = DesignConfig(
        name="abset_seg_req", unit_col="user_id", groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous", role="primary")],
        strata=strata, sample_size=n, split_method="stratified", seed=7,
    )
    return Experiment.design(cfg, design_data, experiments_dir=tmp_path)


def test_abset_declared_plus_adhoc_no_autocross(tmp_path):
    """ABSet twin of the external case: strata=[A,B] (from the assignments
    join), request=[A,B,channel] where channel exists only in the post-data →
    EXACTLY {A,B,channel}, no auto-cross."""
    exp = _abset(tmp_path, ["A", "B"])
    a = exp.assignments
    rng = np.random.default_rng(9)
    post = pd.DataFrame({
        "user_id": a["unit_id"].to_numpy(),
        "revenue": rng.normal(100, 20, len(a)),
        "channel": rng.choice(["push", "sms", "email"], len(a)),
    })
    res = exp.analyze(post, segment_columns=["A", "B", "channel"], categorical_columns=["channel"])
    dims = set(res.context["segment_results_by_dimension"])
    assert dims == {"A", "B", "channel"}, dims
    assert res.context["ad_hoc_segment_dimensions"] == ["channel"]


def test_abset_only_declared_no_autocross(tmp_path):
    exp = _abset(tmp_path, ["A", "B"])
    a = exp.assignments
    rng = np.random.default_rng(10)
    post = pd.DataFrame({
        "user_id": a["unit_id"].to_numpy(),
        "revenue": rng.normal(100, 20, len(a)),
    })
    res = exp.analyze(post)  # default None → declared [A,B], no cross
    assert set(res.context["segment_results_by_dimension"]) == {"A", "B"}
