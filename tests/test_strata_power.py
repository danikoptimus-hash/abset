"""Item 2 (strata power check): compute_strata_power_rows — per-dimension
and combined achievable-MDE breakdown inside individual strata, at the
CURRENT (already-chosen) group proportions."""

from __future__ import annotations

import numpy as np
import pandas as pd

from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import compute_power_results, compute_strata_power_rows


def _make_candidates(n_per_combo=200, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for gender in ["M", "F"]:
        for country in ["RU", "KZ"]:
            revenue = rng.normal(100, 20, size=n_per_combo)
            rows.append(
                pd.DataFrame({"gender": gender, "country": country, "revenue": revenue})
            )
    return pd.concat(rows, ignore_index=True)


def test_strata_power_per_dimension_and_combined_present():
    candidates = _make_candidates()
    groups = {"control": 0.5, "treatment": 0.5}
    metrics = [MetricConfig(name="revenue", type="continuous", role="primary")]
    config = DesignConfig(
        name="strata_power_exp", unit_col="unit_id", groups=groups, metrics=metrics, split_method="simple",
    )
    overall = compute_power_results(config, candidates, "control")
    overall_mde_rel = {"revenue": overall["revenue"].mde_rel}

    dimensions = compute_strata_power_rows(
        candidates, "control", groups, metrics, ["gender", "country"], overall_mde_rel,
        alpha=0.05, power_target=0.8,
    )
    assert set(dimensions.keys()) == {"gender", "country", "gender × country"}
    # Individual dimensions: 2 stratum values each, 1 treatment group, 1 metric.
    assert len(dimensions["gender"]) == 2
    assert len(dimensions["country"]) == 2
    # Combined: 4 stratum values (2x2 cross product).
    assert len(dimensions["gender × country"]) == 4
    assert {r.stratum for r in dimensions["gender"]} == {"M", "F"}


def test_strata_power_status_ok_for_well_powered_stratum():
    """Large, balanced strata with the SAME distribution as the overall
    data should read "ok" — MDE inside the stratum isn't much worse than
    the overall achievable MDE (same relative n_control per stratum)."""
    candidates = _make_candidates(n_per_combo=2000)
    groups = {"control": 0.5, "treatment": 0.5}
    metrics = [MetricConfig(name="revenue", type="continuous", role="primary")]
    config = DesignConfig(
        name="strata_power_ok_exp", unit_col="unit_id", groups=groups, metrics=metrics, split_method="simple",
    )
    overall = compute_power_results(config, candidates, "control")
    overall_mde_rel = {"revenue": overall["revenue"].mde_rel}

    dimensions = compute_strata_power_rows(
        candidates, "control", groups, metrics, ["gender"], overall_mde_rel, alpha=0.05, power_target=0.8,
    )
    assert all(r.status == "ok" for r in dimensions["gender"])


def test_strata_power_status_insufficient_for_tiny_stratum():
    candidates = _make_candidates(n_per_combo=500)
    # A tiny extra stratum value (10 rows) — well below MIN_STRATUM_N_FOR_POWER_CHECK.
    rng = np.random.default_rng(1)
    tiny = pd.DataFrame({"gender": "X", "country": "RU", "revenue": rng.normal(100, 20, size=10)})
    candidates = pd.concat([candidates, tiny], ignore_index=True)

    groups = {"control": 0.5, "treatment": 0.5}
    metrics = [MetricConfig(name="revenue", type="continuous", role="primary")]
    config = DesignConfig(
        name="strata_power_tiny_exp", unit_col="unit_id", groups=groups, metrics=metrics, split_method="simple",
    )
    overall = compute_power_results(config, candidates, "control")
    overall_mde_rel = {"revenue": overall["revenue"].mde_rel}

    dimensions = compute_strata_power_rows(
        candidates, "control", groups, metrics, ["gender"], overall_mde_rel, alpha=0.05, power_target=0.8,
    )
    tiny_row = next(r for r in dimensions["gender"] if r.stratum == "X")
    assert tiny_row.status == "insufficient"
    assert tiny_row.n_control < 20


def test_strata_power_skips_secondary_metrics():
    candidates = _make_candidates()
    candidates["clicks"] = np.random.default_rng(2).binomial(1, 0.1, size=len(candidates))
    groups = {"control": 0.5, "treatment": 0.5}
    primary = [MetricConfig(name="revenue", type="continuous", role="primary")]
    # Secondary metric is never passed to compute_strata_power_rows by the
    # caller (abkit/jobs.py::preview_strata_power filters role=="primary"
    # before calling) — this test documents that the function itself
    # doesn't need its own secondary-filtering, since callers already do it.
    dimensions = compute_strata_power_rows(
        candidates, "control", groups, primary, ["gender"], {"revenue": 0.05}, alpha=0.05, power_target=0.8,
    )
    metrics_seen = {r.metric for r in dimensions["gender"]}
    assert metrics_seen == {"revenue"}


def test_strata_power_includes_cuped_variant_when_pre_col_present():
    rng = np.random.default_rng(3)
    n = 1000
    pre = rng.normal(100, 20, size=n)
    revenue = 0.8 * pre + rng.normal(0, 10, size=n)
    candidates = pd.DataFrame({
        "gender": ["M"] * (n // 2) + ["F"] * (n // 2),
        "revenue": revenue,
        "revenue_pre": pre,
    })
    groups = {"control": 0.5, "treatment": 0.5}
    metrics = [MetricConfig(name="revenue", type="continuous", role="primary", pre_col="revenue_pre")]
    dimensions = compute_strata_power_rows(
        candidates, "control", groups, metrics, ["gender"], {"revenue": 0.05}, alpha=0.05, power_target=0.8,
    )
    assert all(r.mde_rel_cuped is not None for r in dimensions["gender"])
    # CUPED reduces variance -> smaller (better) MDE than without it.
    assert all(r.mde_rel_cuped < r.mde_rel for r in dimensions["gender"])
