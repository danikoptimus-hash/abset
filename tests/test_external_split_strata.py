"""External split rework: reference dataset, declared strata driving the
analysis balance check + segment breakdown, and analyze-time ad-hoc segment
columns. Core-level (Experiment.design_external/.analyze) coverage — the HTTP/
chart_data path is exercised in backend/tests/test_external_split_job.py, the
wizard flow by e2e."""

from __future__ import annotations

import numpy as np
import pandas as pd

from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import Experiment


def _external_config(name="ext_strata", **overrides) -> DesignConfig:
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


def _strata_data(rng, *, extra_column=False):
    """Two countries with DIFFERENT treatment lifts (US: +10pp, UK: ~flat) so
    the per-segment breakdown is meaningful, plus a `platform` column that is
    never declared as a stratum (for the ad-hoc segment test)."""
    rows = []
    for country, ctrl_p, treat_p in [("US", 0.10, 0.20), ("UK", 0.10, 0.11)]:
        for i in range(120):
            platform = "ios" if i % 2 == 0 else "android"
            rows.append(
                {"variant": "A", "conversion": int(rng.binomial(1, ctrl_p)),
                 "country": country, "platform": platform}
            )
            rows.append(
                {"variant": "B", "conversion": int(rng.binomial(1, treat_p)),
                 "country": country, "platform": platform}
            )
    df = pd.DataFrame(rows)
    if not extra_column:
        df = df.drop(columns=["platform"])
    return df


def test_design_external_persists_reference_dataset_and_strata(tmp_path):
    config = _external_config(
        name="ext_ref", reference_dataset_id="11111111-1111-1111-1111-111111111111",
        strata=["country"],
    )
    experiment = Experiment.design_external(config, experiments_dir=tmp_path)
    # Round-trips through the store, not just the in-memory object.
    loaded = Experiment.load("ext_ref", experiments_dir=tmp_path)
    assert loaded.config.reference_dataset_id == "11111111-1111-1111-1111-111111111111"
    assert loaded.config.strata == ["country"]


def test_analyze_external_with_declared_strata_emits_balance_and_segments(tmp_path):
    rng = np.random.default_rng(0)
    experiment = Experiment.design_external(
        _external_config(name="ext_seg", strata=["country"]), experiments_dir=tmp_path
    )
    data = _strata_data(rng)
    results = experiment.analyze(
        data, correction="none", group_column="variant",
        group_mapping={"A": "control", "B": "treatment"},
    )
    ctx = results.context
    # (a) balance table computed on the analyzed users (was the outside split
    # balanced across country?).
    assert ctx["strata_balance"] is not None
    assert set(ctx["strata_balance"].table.columns) == {"control", "treatment"}
    # (b) segment breakdown by the declared stratum — both countries present.
    by_dim = ctx["segment_results_by_dimension"]
    assert "country" in by_dim
    seg = by_dim["country"]["conversion"]["treatment"]
    seg_values = {name for name, _ in seg}
    assert {"US", "UK"} <= seg_values
    # Distinct lifts: US treatment lift clearly higher than UK.
    by_country = {name: r.effect_rel for name, r in seg}
    assert by_country["US"] > by_country["UK"]
    # Segments are not declared as ad-hoc (they were design strata).
    assert ctx["ad_hoc_segment_dimensions"] == []


def test_analyze_external_missing_declared_stratum_degrades_gracefully(tmp_path):
    rng = np.random.default_rng(1)
    experiment = Experiment.design_external(
        _external_config(name="ext_missing", strata=["country"]), experiments_dir=tmp_path
    )
    # Uploaded data has NO country column — must warn + skip, not crash.
    data = _strata_data(rng).drop(columns=["country"])
    results = experiment.analyze(
        data, correction="none", group_column="variant",
        group_mapping={"A": "control", "B": "treatment"},
    )
    assert len(results.results) == 1  # main analysis still ran
    assert any(
        "Declared stratum column 'country' is not in the analysis dataset" in w
        for w in results.global_warnings
    )
    assert results.context["strata_balance"] is None
    assert results.context["segment_results_by_dimension"] == {}


def test_analyze_external_ad_hoc_segment_columns_merge_with_declared(tmp_path):
    rng = np.random.default_rng(2)
    experiment = Experiment.design_external(
        _external_config(name="ext_adhoc", strata=["country"]), experiments_dir=tmp_path
    )
    data = _strata_data(rng, extra_column=True)
    # Declared "country" + ad-hoc "platform" (present in the data, never
    # declared as a stratum at design).
    results = experiment.analyze(
        data, correction="none", group_column="variant",
        group_mapping={"A": "control", "B": "treatment"},
        segment_columns=["country", "platform"],
    )
    by_dim = results.context["segment_results_by_dimension"]
    assert "country" in by_dim  # declared, from the synthesized stratum
    assert "platform" in by_dim  # ad-hoc, from the raw column
    assert results.context["ad_hoc_segment_dimensions"] == ["platform"]


def test_analyze_external_unknown_segment_column_is_skipped_with_warning(tmp_path):
    rng = np.random.default_rng(3)
    experiment = Experiment.design_external(
        _external_config(name="ext_unknown_seg", strata=["country"]), experiments_dir=tmp_path
    )
    data = _strata_data(rng)
    results = experiment.analyze(
        data, correction="none", group_column="variant",
        group_mapping={"A": "control", "B": "treatment"},
        segment_columns=["country", "nonexistent"],
    )
    assert any(
        "Segment column 'nonexistent' is not in the analysis dataset" in w
        for w in results.global_warnings
    )
    assert "nonexistent" not in results.context["segment_results_by_dimension"]
