"""Positive-only display mode (report feature): _positive_only_distribution
in backend/chart_data.py is a pure display-data transform — it runs strictly
after Experiment.analyze() has already produced effects/p-values/CIs/
verdicts from the unfiltered data (abkit/analysis, abkit/design). These
tests are at the core level (Experiment.design/.analyze() directly, no HTTP)
for precision: HTTP-level chart_data coverage already exists in
test_analyze_validate_jobs.py."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import Experiment
from backend.chart_data import build_chart_data


def _design_and_analyze_zero_inflated(tmp_path, pct_zero=0.79, n=4000, seed=0):
    """A continuous metric shaped like the motivating case: most users are
    exact zero (never converted/spent), a minority have a real positive
    value — the scenario "Positive only" exists for."""
    rng = np.random.default_rng(seed)
    design_data = pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "monetary": np.where(
                rng.random(n) < pct_zero, 0.0, rng.lognormal(mean=4.0, sigma=1.0, size=n)
            ),
        }
    )
    config = DesignConfig(
        name="zero_inflated_exp",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="monetary", type="continuous", role="primary")],
        sample_size=n,
        split_method="simple",
        seed=seed,
    )
    experiment = Experiment.design(config, design_data, experiments_dir=tmp_path)
    assignments = experiment.assignments
    n_assigned = len(assignments)
    rng2 = np.random.default_rng(seed + 1)
    post_data = pd.DataFrame(
        {
            "user_id": list(assignments["unit_id"]),
            "monetary": np.where(
                rng2.random(n_assigned) < pct_zero, 0.0, rng2.lognormal(mean=4.0, sigma=1.0, size=n_assigned)
            ),
        }
    )
    results = experiment.analyze(post_data, correction="none")
    return results


def test_positive_only_excludes_zeros_from_histogram_and_ecdf(tmp_path):
    results = _design_and_analyze_zero_inflated(tmp_path)
    chart_data = build_chart_data(results)
    dist = next(iter(chart_data["metrics"]["monetary"]["distributions"].values()))

    assert dist["has_zeros"] is True
    pos = dist["positive_only"]
    assert pos["pct_zero_control"] > 50.0  # ~79% zeros by construction
    assert pos["pct_zero_treatment"] > 50.0

    # No exact-zero bin edges/ECDF points below the smallest positive value.
    assert pos["histogram"]["bin_edges"][0] > 0
    assert all(v > 0 for v, _ in pos["control_ecdf"])
    assert all(v > 0 for v, _ in pos["treatment_ecdf"])

    # The ECDF for "clipped"/"full_range" (unfiltered) still includes the
    # zero-mass jump — untouched by adding positive_only alongside it.
    assert dist["control_ecdf"][0][0] == 0.0


def test_has_zeros_false_when_metric_has_no_zeros(tmp_path):
    results = _design_and_analyze_zero_inflated(tmp_path, pct_zero=0.0)
    chart_data = build_chart_data(results)
    dist = next(iter(chart_data["metrics"]["monetary"]["distributions"].values()))
    assert dist["has_zeros"] is False


def test_build_chart_data_does_not_mutate_analysis_results_payload(tmp_path):
    """Item 3: report payload statistics (effects/p-values/CIs/verdicts) are
    byte-identical whether or not chart_data (and its positive_only display
    metadata) is computed — build_chart_data must be read-only with respect
    to the AnalysisResults it's given."""
    results = _design_and_analyze_zero_inflated(tmp_path)

    before_raw = results.to_json()
    build_chart_data(results)
    after_raw = results.to_json()

    # to_json() is a JSON string (AnalysisResults.to_json) — byte-identical
    # is the literal claim item 3 asks for, checked before parsing at all.
    assert before_raw == after_raw

    before = json.loads(before_raw)
    after = json.loads(after_raw)
    # Specifically the part a report/verdict is based on, not just "the
    # whole payload happens to match" — spelled out for clarity.
    assert before["results"] == after["results"]
    assert before["global_warnings"] == after["global_warnings"]
