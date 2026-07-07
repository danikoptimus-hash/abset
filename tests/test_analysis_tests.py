import numpy as np
import pandas as pd
import pytest
from scipy import stats as sp_stats
from statsmodels.stats.proportion import proportions_ztest

from abkit.analysis.tests import WelchTTest, ZTestProportions
from abkit.pipeline import MetricContext


def make_ctx(values, group, metric_type="continuous", alpha=0.05, **kwargs):
    defaults = dict(
        metric_name="m",
        metric_type=metric_type,
        control_name="control",
        treatment_name="treatment",
        values=pd.Series(values),
        group=pd.Series(group),
        alpha=alpha,
    )
    defaults.update(kwargs)
    return MetricContext(**defaults)


def test_welch_p_value_matches_scipy():
    rng = np.random.default_rng(0)
    control = rng.normal(100, 20, size=300)
    treatment = rng.normal(105, 20, size=300)
    values = np.concatenate([control, treatment])
    group = ["control"] * 300 + ["treatment"] * 300

    ctx = WelchTTest().apply(make_ctx(values, group))
    _stat, expected_p = sp_stats.ttest_ind(treatment, control, equal_var=False)

    assert ctx.result.p_value == pytest.approx(expected_p)
    assert ctx.result.effect_abs == pytest.approx(treatment.mean() - control.mean())
    assert ctx.result.n == {"control": 300, "treatment": 300}


def test_welch_effect_rel_and_ci_sane():
    control = np.array([10.0, 12.0, 11.0, 9.0, 10.0, 13.0])
    treatment = np.array([12.0, 14.0, 13.0, 11.0, 12.0, 15.0])
    values = np.concatenate([control, treatment])
    group = ["control"] * len(control) + ["treatment"] * len(treatment)

    ctx = WelchTTest().apply(make_ctx(values, group))
    r = ctx.result
    assert r.effect_rel == pytest.approx((treatment.mean() - control.mean()) / control.mean())
    assert r.ci_abs[0] < r.effect_abs < r.ci_abs[1]
    assert r.ci_rel[0] < r.effect_rel < r.ci_rel[1]
    assert r.method == "Welch t-test"
    assert r.is_designed_method is True
    assert r.treatment_group == "treatment"


def test_welch_narrower_alpha_widens_ci():
    rng = np.random.default_rng(1)
    control = rng.normal(50, 10, size=200)
    treatment = rng.normal(52, 10, size=200)
    values = np.concatenate([control, treatment])
    group = ["control"] * 200 + ["treatment"] * 200

    ctx_95 = WelchTTest().apply(make_ctx(values, group, alpha=0.05))
    ctx_99 = WelchTTest().apply(make_ctx(values, group, alpha=0.01))

    width_95 = ctx_95.result.ci_abs[1] - ctx_95.result.ci_abs[0]
    width_99 = ctx_99.result.ci_abs[1] - ctx_99.result.ci_abs[0]
    assert width_99 > width_95


def test_welch_rejects_too_few_observations():
    with pytest.raises(ValueError):
        WelchTTest().apply(make_ctx([1.0], ["control"]))


def test_ztest_proportions_matches_statsmodels():
    rng = np.random.default_rng(0)
    control = rng.binomial(1, 0.10, size=2000)
    treatment = rng.binomial(1, 0.12, size=2000)
    values = np.concatenate([control, treatment])
    group = ["control"] * 2000 + ["treatment"] * 2000

    ctx = ZTestProportions().apply(make_ctx(values, group, metric_type="binary"))
    _stat, expected_p = proportions_ztest(
        [treatment.sum(), control.sum()], [len(treatment), len(control)]
    )

    assert ctx.result.p_value == pytest.approx(expected_p)
    assert ctx.result.effect_abs == pytest.approx(treatment.mean() - control.mean())
    assert ctx.result.method == "Z-тест пропорций"


def test_ztest_proportions_effect_rel_and_ci_sane():
    control = np.array([0, 0, 0, 1, 0, 0, 0, 0, 1, 0] * 20)
    treatment = np.array([0, 1, 0, 1, 0, 1, 0, 0, 1, 0] * 20)
    values = np.concatenate([control, treatment])
    group = ["control"] * len(control) + ["treatment"] * len(treatment)

    ctx = ZTestProportions().apply(make_ctx(values, group, metric_type="binary"))
    r = ctx.result
    p_control = control.mean()
    p_treat = treatment.mean()
    assert r.effect_rel == pytest.approx((p_treat - p_control) / p_control)
    assert r.ci_abs[0] < r.effect_abs < r.ci_abs[1]


def test_ztest_proportions_rejects_empty_group():
    with pytest.raises(ValueError):
        ZTestProportions().apply(make_ctx([], [], metric_type="binary"))
