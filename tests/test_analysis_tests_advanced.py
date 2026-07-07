import numpy as np
import pandas as pd
import pytest
from scipy import stats as sp_stats

from abkit.analysis.tests import Bootstrap, DeltaMethodTTest, MannWhitney
from abkit.pipeline import MetricContext


def make_ctx(values=None, group=None, metric_type="continuous", alpha=0.05, **kwargs):
    defaults = dict(
        metric_name="m",
        metric_type=metric_type,
        control_name="control",
        treatment_name="treatment",
        values=pd.Series(values) if values is not None else pd.Series([], dtype=float),
        group=pd.Series(group) if group is not None else pd.Series([], dtype=object),
        alpha=alpha,
    )
    defaults.update(kwargs)
    return MetricContext(**defaults)


def test_mann_whitney_p_value_matches_scipy():
    rng = np.random.default_rng(0)
    control = rng.normal(100, 20, size=200)
    treatment = rng.normal(108, 20, size=200)
    values = np.concatenate([control, treatment])
    group = ["control"] * 200 + ["treatment"] * 200

    ctx = MannWhitney().apply(make_ctx(values, group))
    _stat, expected_p = sp_stats.mannwhitneyu(treatment, control, alternative="two-sided")

    assert ctx.result.p_value == pytest.approx(expected_p)
    assert ctx.result.method == "Mann-Whitney (Ходжес-Леман)"
    assert any("Ходжеса-Лемана" in w for w in ctx.result.warnings)


def test_mann_whitney_hl_estimate_close_to_true_shift():
    rng = np.random.default_rng(1)
    control = rng.normal(0, 5, size=2000)
    treatment = rng.normal(3, 5, size=2000)
    values = np.concatenate([control, treatment])
    group = ["control"] * 2000 + ["treatment"] * 2000

    ctx = MannWhitney().apply(make_ctx(values, group))
    assert ctx.result.effect_abs == pytest.approx(3.0, abs=0.5)
    assert ctx.result.ci_abs[0] < ctx.result.effect_abs < ctx.result.ci_abs[1]


def test_mann_whitney_rejects_empty_group():
    with pytest.raises(ValueError):
        MannWhitney().apply(make_ctx([1.0], ["control"]))


def test_bootstrap_percentile_ci_contains_true_effect():
    rng = np.random.default_rng(2)
    control = rng.normal(100, 10, size=500)
    treatment = rng.normal(105, 10, size=500)
    values = np.concatenate([control, treatment])
    group = ["control"] * 500 + ["treatment"] * 500

    ctx = Bootstrap(n_boot=2000, method="percentile", seed=0).apply(make_ctx(values, group))
    assert ctx.result.ci_abs[0] < 5.0 < ctx.result.ci_abs[1]
    assert ctx.result.effect_abs == pytest.approx(treatment.mean() - control.mean())


def test_bootstrap_bca_ci_contains_true_effect():
    rng = np.random.default_rng(3)
    control = rng.normal(100, 10, size=500)
    treatment = rng.normal(105, 10, size=500)
    values = np.concatenate([control, treatment])
    group = ["control"] * 500 + ["treatment"] * 500

    ctx = Bootstrap(n_boot=2000, method="bca", seed=0).apply(make_ctx(values, group))
    assert ctx.result.ci_abs[0] < 5.0 < ctx.result.ci_abs[1]
    assert ctx.result.method == "Bootstrap (bca)"


def test_bootstrap_no_effect_gives_high_p_value():
    rng = np.random.default_rng(4)
    control = rng.normal(100, 10, size=500)
    treatment = rng.normal(100, 10, size=500)
    values = np.concatenate([control, treatment])
    group = ["control"] * 500 + ["treatment"] * 500

    ctx = Bootstrap(n_boot=2000, method="bca", seed=1).apply(make_ctx(values, group))
    assert ctx.result.p_value > 0.05


def test_bootstrap_rejects_invalid_method():
    with pytest.raises(ValueError, match="method"):
        Bootstrap(method="bogus")


def test_bootstrap_rejects_too_few_observations():
    with pytest.raises(ValueError):
        Bootstrap().apply(make_ctx([1.0], ["control"]))


def test_delta_method_ttest_no_effect_gives_high_p_value():
    rng = np.random.default_rng(5)
    n = 2000
    sessions = rng.integers(3, 10, size=n)
    conv_rate = 0.2
    clicks = rng.binomial(sessions, conv_rate)
    group = rng.choice(["control", "treatment"], size=n)

    ctx = make_ctx(
        metric_type="ratio",
        group=group,
        num=pd.Series(clicks),
        den=pd.Series(sessions),
    )
    result_ctx = DeltaMethodTTest().apply(ctx)
    assert result_ctx.result.p_value > 0.01


def test_delta_method_ttest_detects_real_effect():
    rng = np.random.default_rng(6)
    n = 3000
    sessions = rng.integers(3, 10, size=n)
    is_treat = rng.random(n) < 0.5
    conv_rate = np.where(is_treat, 0.30, 0.20)
    clicks = rng.binomial(sessions, conv_rate)
    group = np.where(is_treat, "treatment", "control")

    ctx = make_ctx(
        metric_type="ratio",
        group=group,
        num=pd.Series(clicks),
        den=pd.Series(sessions),
    )
    result_ctx = DeltaMethodTTest().apply(ctx)
    assert result_ctx.result.effect_abs > 0
    assert result_ctx.result.p_value < 0.01


def test_delta_method_ttest_requires_num_den():
    ctx = make_ctx([1.0, 2.0], ["control", "treatment"], metric_type="ratio")
    with pytest.raises(ValueError, match="num.*den"):
        DeltaMethodTTest().apply(ctx)
