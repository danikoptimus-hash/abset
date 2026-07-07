"""Тесты generate_demo_post_data_for_config — генератор demo пост-данных ДЛЯ
ЛЮБОГО эксперимента (используется кнопкой в Analyze-табе), в отличие от
захардкоженной generate_demo_post_data (только для CLI `abkit demo`)."""

import numpy as np
import pandas as pd
import pytest

from abkit.config import DesignConfig, MetricConfig
from abkit.demo_data import generate_demo_post_data_for_config
from abkit.experiment import Experiment


def _design_experiment(tmp_path, metrics, n=4000, seed=1, name="demo_post_check"):
    """Дизайнит эксперимент с историческими данными, покрывающими требования
    _validate_input_data для КАЖДОЙ переданной метрики (иначе design() падает
    с DesignError — нужна колонка metric.name либо metric.pre_col для оценки
    дисперсии на этапе дизайна)."""
    rng = np.random.default_rng(seed)
    data = {"user_id": [f"u{i}" for i in range(n)]}
    for metric in metrics:
        if metric.type == "ratio":
            data[metric.den] = rng.integers(1, 10, size=n)
            data[metric.num] = rng.binomial(data[metric.den], 0.3)
        elif metric.type == "binary":
            col = metric.pre_col or metric.name
            data[col] = rng.binomial(1, 0.2, size=n)
        else:
            col = metric.pre_col or metric.name
            data[col] = rng.normal(100, 20, size=n)
    data = pd.DataFrame(data)
    config = DesignConfig(
        name=name,
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=metrics,
        sample_size=n,
        split_method="simple",
        seed=seed,
    )
    return Experiment.design(config, data, experiments_dir=tmp_path), rng


def test_covers_all_three_metric_types(tmp_path):
    metrics = [
        MetricConfig(name="revenue", type="continuous", pre_col="revenue_pre"),
        MetricConfig(name="clicked", type="binary", role="secondary"),
        MetricConfig(name="conv_rate", type="ratio", num="orders", den="sessions", role="secondary"),
    ]
    experiment, _ = _design_experiment(tmp_path, metrics)

    df = generate_demo_post_data_for_config(experiment.config, experiment.assignments, seed=2)

    assert "revenue" in df.columns
    assert "revenue_pre" in df.columns
    assert "clicked" in df.columns
    assert set(df["clicked"].unique()) <= {0, 1}
    assert "orders" in df.columns and "sessions" in df.columns
    assert (df["orders"] <= df["sessions"]).all()
    assert experiment.config.unit_col in df.columns


def test_detects_injected_effect_on_primary_continuous_metric(tmp_path):
    metrics = [MetricConfig(name="revenue", type="continuous", role="primary")]
    experiment, _ = _design_experiment(tmp_path, metrics, n=6000, seed=3)

    post_data = generate_demo_post_data_for_config(
        experiment.config, experiment.assignments, effect=0.03, seed=3,
    )
    results = experiment.analyze(post_data)

    revenue_result = results["revenue"][0]
    assert revenue_result.effect_abs > 0
    assert revenue_result.p_value < 0.05


def test_no_lift_applied_to_secondary_metrics(tmp_path):
    metrics = [MetricConfig(name="revenue", type="continuous", role="secondary")]
    experiment, _ = _design_experiment(tmp_path, metrics, n=8000, seed=4)

    df = generate_demo_post_data_for_config(
        experiment.config, experiment.assignments, effect=0.03, seed=4,
    )
    merged = df.merge(
        experiment.assignments.rename(columns={"unit_id": experiment.config.unit_col}),
        on=experiment.config.unit_col,
    )
    control_mean = merged.loc[merged["group"] == "control", "revenue"].mean()
    treatment_mean = merged.loc[merged["group"] == "treatment", "revenue"].mean()
    # без лифта разница должна быть в пределах шума, точно меньше самого effect (3% от ~100 = 3)
    assert abs(treatment_mean - control_mean) < 2.0


def test_attrition_drops_roughly_symmetric_share_per_group(tmp_path):
    metrics = [MetricConfig(name="revenue", type="continuous")]
    experiment, _ = _design_experiment(tmp_path, metrics, n=5000, seed=5)
    n_before = len(experiment.assignments)

    df = generate_demo_post_data_for_config(
        experiment.config, experiment.assignments, attrition=0.02, seed=5,
    )

    n_after = len(df)
    assert n_after < n_before
    pct_dropped = (n_before - n_after) / n_before
    assert pct_dropped == pytest.approx(0.02, abs=0.005)

    merged = experiment.assignments.merge(
        df[[experiment.config.unit_col]].rename(columns={experiment.config.unit_col: "unit_id"}),
        on="unit_id", how="left", indicator=True,
    )
    dropped = merged[merged["_merge"] == "left_only"]
    dropped_control = (dropped["group"] == "control").sum()
    dropped_treatment = (dropped["group"] == "treatment").sum()
    # симметрично: доли примерно равны, ни одна группа не потеряла непропорционально много
    assert dropped_control > 0 and dropped_treatment > 0
    assert abs(dropped_control - dropped_treatment) / max(dropped_control, dropped_treatment) < 0.5


def test_binary_metric_baseline_from_pre_col_when_configured(tmp_path):
    metrics = [MetricConfig(name="clicked", type="binary", pre_col="clicked_pre")]
    experiment, _ = _design_experiment(tmp_path, metrics, n=3000, seed=6)

    df = generate_demo_post_data_for_config(experiment.config, experiment.assignments, seed=6)

    assert "clicked_pre" in df.columns
    assert df["clicked_pre"].between(0, 1).all()


def test_end_to_end_analyze_runs_without_error_on_mixed_metrics(tmp_path):
    metrics = [
        MetricConfig(name="revenue", type="continuous", pre_col="revenue_pre", role="primary"),
        MetricConfig(name="clicked", type="binary", role="secondary"),
    ]
    experiment, _ = _design_experiment(tmp_path, metrics, n=2000, seed=7)

    post_data = generate_demo_post_data_for_config(experiment.config, experiment.assignments, seed=7)
    results = experiment.analyze(post_data)

    assert "revenue" in results.metrics
    assert "clicked" in results.metrics
