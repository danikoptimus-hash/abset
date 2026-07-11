"""Item 12: external split (config.split_source="external") — the split
happens outside ABSet (Firebase A/B Testing and similar); ABSet only stores
declared groups/metrics and analyzes post-period data against a group column
the user maps at analysis time. Core-level (Experiment.design_external/
.analyze()) coverage — HTTP/wizard flow is covered by e2e."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from abkit.checks import AnalysisError
from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import Experiment


def _external_config(name="ext_exp", **overrides) -> DesignConfig:
    fields = dict(
        name=name,
        unit_col="",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="conversion", type="binary", role="primary")],
        split_source="external",
        isolation="off",
    )
    fields.update(overrides)
    return DesignConfig(**fields)


def test_design_external_creates_experiment_with_no_assignments(tmp_path):
    config = _external_config()
    experiment = Experiment.design_external(config, experiments_dir=tmp_path)
    assert experiment.name == "ext_exp"
    assert experiment.assignments is not None
    assert len(experiment.assignments) == 0
    assert experiment.config.split_source == "external"


def test_analyze_external_requires_group_column_and_mapping(tmp_path):
    experiment = Experiment.design_external(_external_config(name="ext_missing_mapping"), experiments_dir=tmp_path)
    data = pd.DataFrame({"variant": ["A"] * 10, "conversion": [1] * 10})
    with pytest.raises(AnalysisError, match="select a group column and map"):
        experiment.analyze(data)


def test_analyze_external_rejects_date_col(tmp_path):
    experiment = Experiment.design_external(_external_config(name="ext_date_col"), experiments_dir=tmp_path)
    data = pd.DataFrame({"variant": ["A", "B"] * 10, "conversion": [1, 0] * 10, "day": ["2024-01-01"] * 20})
    with pytest.raises(AnalysisError, match="Day-by-day aggregation"):
        experiment.analyze(
            data, date_col="day", group_column="variant",
            group_mapping={"A": "control", "B": "treatment"},
        )


def test_analyze_external_rejects_missing_group_column(tmp_path):
    experiment = Experiment.design_external(_external_config(name="ext_bad_col"), experiments_dir=tmp_path)
    data = pd.DataFrame({"conversion": [1, 0] * 10})
    with pytest.raises(AnalysisError, match="Group column 'variant' is not in the uploaded data"):
        experiment.analyze(data, group_column="variant", group_mapping={"A": "control", "B": "treatment"})


def test_analyze_external_rejects_when_nothing_maps(tmp_path):
    experiment = Experiment.design_external(_external_config(name="ext_no_match"), experiments_dir=tmp_path)
    data = pd.DataFrame({"variant": ["C", "D"] * 10, "conversion": [1, 0] * 10})
    with pytest.raises(AnalysisError, match="No rows matched a declared group"):
        experiment.analyze(data, group_column="variant", group_mapping={"A": "control", "B": "treatment"})


def test_analyze_external_end_to_end_with_excluded_rows_and_srm(tmp_path):
    """25/25 mapped to control/treatment (balanced -> SRM passes), plus a
    third raw value ("C") explicitly excluded -> coverage warning."""
    rng = np.random.default_rng(0)
    config = _external_config(name="ext_e2e", groups={"control": 0.5, "treatment": 0.5})
    experiment = Experiment.design_external(config, experiments_dir=tmp_path)

    variant = ["A"] * 25 + ["B"] * 25 + ["C"] * 5
    conversion = list(rng.binomial(1, 0.10, size=25)) + list(rng.binomial(1, 0.15, size=25)) + [1, 0, 1, 0, 1]
    data = pd.DataFrame({"variant": variant, "conversion": conversion})

    results = experiment.analyze(
        data, correction="none", group_column="variant",
        group_mapping={"A": "control", "B": "treatment", "C": "exclude"},
    )
    assert len(results.results) == 1
    r = results.results[0]
    assert r.n["control"] == 25
    assert r.n["treatment"] == 25
    assert any("Group column coverage" in w for w in results.global_warnings)


def test_analyze_external_srm_uses_declared_group_proportions(tmp_path):
    """Wildly unbalanced actual split (90/10) against a declared 50/50 ->
    SRM must fail and warn, exactly like the abkit-split flow's SRM check —
    the only difference is where the "expected" ratios come from (declared
    groups instead of the real split's intended ratio, which is the same
    field either way: config.groups)."""
    config = _external_config(name="ext_srm_fail", groups={"control": 0.5, "treatment": 0.5})
    experiment = Experiment.design_external(config, experiments_dir=tmp_path)

    data = pd.DataFrame(
        {
            "variant": ["A"] * 900 + ["B"] * 100,
            "conversion": [0, 1] * 500,
        }
    )
    results = experiment.analyze(
        data, correction="none", group_column="variant",
        group_mapping={"A": "control", "B": "treatment"},
    )
    assert any("SRM on the actual data" in w for w in results.global_warnings)


def test_analyze_external_cuped_works_with_pre_column(tmp_path):
    rng = np.random.default_rng(1)
    n = 400
    config = _external_config(
        name="ext_cuped",
        metrics=[MetricConfig(name="revenue", type="continuous", role="primary", pre_col="revenue_pre")],
    )
    experiment = Experiment.design_external(config, experiments_dir=tmp_path)

    pre = rng.normal(100, 20, size=n)
    post = pre + rng.normal(0, 5, size=n)
    data = pd.DataFrame(
        {
            "variant": (["A"] * (n // 2)) + (["B"] * (n // 2)),
            "revenue": post,
            "revenue_pre": pre,
        }
    )
    results = experiment.analyze(
        data, correction="none", compare_methods=True, group_column="variant",
        group_mapping={"A": "control", "B": "treatment"},
    )
    cuped_rows = [r for r in results.results if r.cuped_rho is not None]
    assert cuped_rows, "expected at least one CUPED row from compare_methods"
