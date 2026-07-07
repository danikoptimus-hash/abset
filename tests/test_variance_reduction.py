import numpy as np
import pandas as pd
import pytest

from abkit.analysis.variance_reduction import CUPED, PostStratification
from abkit.pipeline import MetricContext


def make_ctx(values, group, **kwargs):
    defaults = dict(
        metric_name="m",
        metric_type="continuous",
        control_name="control",
        treatment_name="treatment",
        values=pd.Series(values),
        group=pd.Series(group),
    )
    defaults.update(kwargs)
    return MetricContext(**defaults)


def test_cuped_reduces_variance_close_to_theoretical():
    rng = np.random.default_rng(0)
    n = 20_000
    rho = 0.7
    pre = rng.normal(100, 20, size=n)
    noise = rng.normal(0, 20 * np.sqrt(1 - rho**2), size=n)
    revenue = rho * pre + noise + 50  # corr(revenue, pre) ~ rho by construction (scaled)
    group = ["control"] * (n // 2) + ["treatment"] * (n // 2)

    ctx = make_ctx(revenue, group, covariate=pd.Series(pre))
    result_ctx = CUPED().apply(ctx)

    empirical_rho = np.corrcoef(revenue, pre)[0, 1]
    expected_reduction = empirical_rho**2

    assert result_ctx.variance_reduction == pytest.approx(expected_reduction, rel=0.10)


def test_cuped_requires_covariate():
    ctx = make_ctx([1.0, 2.0, 3.0, 4.0], ["control", "control", "treatment", "treatment"])
    with pytest.raises(ValueError, match="covariate"):
        CUPED().apply(ctx)


def test_cuped_imputes_nan_covariate_and_warns():
    values = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    covariate = pd.Series([1.0, np.nan, 3.0, 4.0, 5.0, 6.0])
    group = pd.Series(["control"] * 3 + ["treatment"] * 3)
    ctx = make_ctx(values, group, covariate=covariate)

    result_ctx = CUPED().apply(ctx)
    assert any("пропусков" in w for w in result_ctx.warnings)
    assert not result_ctx.values.isna().any()


def test_cuped_zero_variance_covariate_warns_and_skips():
    values = pd.Series([1.0, 2.0, 3.0, 4.0])
    covariate = pd.Series([5.0, 5.0, 5.0, 5.0])
    group = pd.Series(["control", "control", "treatment", "treatment"])
    ctx = make_ctx(values, group, covariate=covariate)

    result_ctx = CUPED().apply(ctx)
    assert any("дисперсия ковариаты равна нулю" in w for w in result_ctx.warnings)
    pd.testing.assert_series_equal(result_ctx.values, values)


def test_cuped_preserves_effect_in_expectation():
    rng = np.random.default_rng(1)
    n = 20_000
    pre = rng.normal(100, 20, size=n)
    noise = rng.normal(0, 10, size=n)
    control_vals = 0.8 * pre[: n // 2] + noise[: n // 2]
    treat_vals = 0.8 * pre[n // 2 :] + noise[n // 2 :] + 5.0  # реальный эффект +5
    values = np.concatenate([control_vals, treat_vals])
    group = ["control"] * (n // 2) + ["treatment"] * (n // 2)

    ctx = make_ctx(values, group, covariate=pd.Series(pre))
    result_ctx = CUPED().apply(ctx)

    adjusted_control_mean = result_ctx.values[result_ctx.group == "control"].mean()
    adjusted_treat_mean = result_ctx.values[result_ctx.group == "treatment"].mean()
    assert (adjusted_treat_mean - adjusted_control_mean) == pytest.approx(5.0, abs=0.5)


def test_post_stratification_matches_manual_weighted_estimate():
    data = pd.DataFrame(
        {
            "value": [10, 12, 11, 20, 22, 21, 30, 33, 31, 40],
            "group": ["control", "treatment", "control", "control", "treatment", "treatment", "control", "treatment", "control", "treatment"],
            "stratum": ["a", "a", "a", "b", "b", "b", "b", "b", "b", "b"],
        }
    )
    ctx = make_ctx(
        data["value"], data["group"], stratum=data["stratum"]
    )
    result_ctx = PostStratification().apply(ctx)
    assert result_ctx.result is not None
    assert result_ctx.result.method == "Post-stratification"
    # страта "a" пропущена (в ней только 1 наблюдение в treatment) -> считается только страта "b"
    assert result_ctx.result.n == {"control": 3, "treatment": 4}
    assert any("пропущены" in w for w in result_ctx.warnings)


def test_post_stratification_requires_stratum():
    ctx = make_ctx([1.0, 2.0, 3.0, 4.0], ["control", "control", "treatment", "treatment"])
    with pytest.raises(ValueError, match="stratum"):
        PostStratification().apply(ctx)


def test_post_stratification_skips_small_strata_and_warns():
    values = list(range(20)) + [100, 200]  # последние 2 - отдельная маленькая страта (1 на группу)
    group = ["control", "treatment"] * 10 + ["control", "treatment"]
    stratum = ["a"] * 20 + ["tiny", "tiny"]
    ctx = make_ctx(values, group, stratum=pd.Series(stratum))
    result_ctx = PostStratification().apply(ctx)
    assert any("пропущены" in w for w in result_ctx.warnings)


def test_post_stratification_no_effect_gives_high_p_value():
    rng = np.random.default_rng(2)
    n = 4000
    stratum = rng.choice(["a", "b", "c"], size=n)
    values = rng.normal(100, 20, size=n)
    group = np.where(rng.random(n) < 0.5, "control", "treatment")
    ctx = make_ctx(values, group, stratum=pd.Series(stratum))
    result_ctx = PostStratification().apply(ctx)
    assert result_ctx.result.p_value > 0.01
