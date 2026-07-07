import numpy as np
import pandas as pd
import pytest
from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import samplesize_proportions_2indep_onetail

from abkit.design import power


@pytest.mark.parametrize("std,mde_abs,ratio", [(10.0, 1.0, 1.0), (5.0, 0.3, 1.0), (10.0, 1.0, 2.0), (10.0, 1.0, 0.5)])
def test_sample_size_continuous_matches_statsmodels(std, mde_abs, ratio):
    ours = power.sample_size_continuous(std, mde_abs, alpha=0.05, power=0.8, ratio=ratio)
    reference = NormalIndPower().solve_power(
        effect_size=mde_abs / std, alpha=0.05, power=0.8, ratio=ratio, alternative="two-sided"
    )
    assert ours == pytest.approx(reference, rel=0.01)


def test_mde_continuous_is_inverse_of_sample_size():
    std, alpha, power_ = 10.0, 0.05, 0.8
    mde_abs = 1.5
    n = power.sample_size_continuous(std, mde_abs, alpha=alpha, power=power_)
    recovered_mde = power.mde_continuous(std, n, alpha=alpha, power=power_)
    assert recovered_mde == pytest.approx(mde_abs, rel=1e-6)


@pytest.mark.parametrize("target_power,ratio", [(0.5, 1.0), (0.8, 1.0), (0.9, 1.0), (0.8, 2.0)])
def test_power_given_n_continuous_recovers_target_power(target_power, ratio):
    std, mde_abs, alpha = 15.0, 2.0, 0.05
    n = power.sample_size_continuous(std, mde_abs, alpha=alpha, power=target_power, ratio=ratio)
    recovered = power.power_given_n_continuous(std, mde_abs, n, alpha=alpha, ratio=ratio)
    assert recovered == pytest.approx(target_power, abs=1e-6)


def test_power_given_n_continuous_rejects_nonpositive_n():
    with pytest.raises(ValueError):
        power.power_given_n_continuous(std=1.0, mde_abs=1.0, n_control=0)


@pytest.mark.parametrize(
    "p_control,p_treat,ratio",
    [(0.10, 0.11, 1.0), (0.10, 0.13, 2.0), (0.02, 0.025, 3.0), (0.10, 0.11, 0.5)],
)
def test_sample_size_binary_matches_statsmodels(p_control, p_treat, ratio):
    ours = power.sample_size_binary(p_control, p_treat, alpha=0.05, power=0.8, ratio=ratio)
    reference = samplesize_proportions_2indep_onetail(
        diff=p_control - p_treat, prop2=p_treat, power=0.8, ratio=ratio, alpha=0.05, alternative="two-sided"
    )
    assert ours == pytest.approx(reference, rel=0.01)


def test_mde_binary_is_inverse_of_sample_size():
    p_control, alpha, power_ = 0.10, 0.05, 0.8
    p_treat = 0.12
    n = power.sample_size_binary(p_control, p_treat, alpha=alpha, power=power_)
    recovered_delta = power.mde_binary(p_control, n, alpha=alpha, power=power_)
    assert recovered_delta == pytest.approx(p_treat - p_control, rel=1e-4)


@pytest.mark.parametrize("target_power,ratio", [(0.5, 1.0), (0.8, 1.0), (0.9, 1.0), (0.8, 2.0)])
def test_power_given_n_binary_recovers_target_power(target_power, ratio):
    p_control, p_treat, alpha = 0.10, 0.12, 0.05
    n = power.sample_size_binary(p_control, p_treat, alpha=alpha, power=target_power, ratio=ratio)
    recovered = power.power_given_n_binary(p_control, p_treat, n, alpha=alpha, ratio=ratio)
    assert recovered == pytest.approx(target_power, abs=1e-6)


def test_power_given_n_binary_rejects_out_of_range_proportions():
    with pytest.raises(ValueError):
        power.power_given_n_binary(p_control=0.0, p_treat=0.1, n_control=100)


def test_sample_size_continuous_rejects_nonpositive_mde():
    with pytest.raises(ValueError):
        power.sample_size_continuous(std=1.0, mde_abs=0.0)


def test_sample_size_binary_rejects_out_of_range_proportions():
    with pytest.raises(ValueError):
        power.sample_size_binary(p_control=0.0, p_treat=0.1)
    with pytest.raises(ValueError):
        power.sample_size_binary(p_control=0.1, p_treat=1.0)


def test_sample_size_binary_rejects_equal_proportions():
    with pytest.raises(ValueError):
        power.sample_size_binary(p_control=0.1, p_treat=0.1)


def test_delta_method_variance_matches_naive_ratio_when_den_constant():
    rng = np.random.default_rng(0)
    den = pd.Series(np.full(5000, 10.0))
    num = pd.Series(rng.normal(loc=50, scale=5, size=5000))
    mean, variance = power.delta_method_variance(num, den)
    # если знаменатель константа, дисперсия ratio = var(num)/den^2
    assert mean == pytest.approx(num.mean() / 10.0, rel=1e-6)
    assert variance == pytest.approx(num.var(ddof=1) / 100.0, rel=1e-6)


def test_delta_method_variance_requires_at_least_two_obs():
    with pytest.raises(ValueError):
        power.delta_method_variance(pd.Series([1.0]), pd.Series([2.0]))


def test_delta_method_variance_rejects_zero_mean_denominator():
    num = pd.Series([1.0, -1.0, 1.0, -1.0])
    den = pd.Series([1.0, -1.0, 1.0, -1.0])
    with pytest.raises(ValueError):
        power.delta_method_variance(num, den)


def test_correlation_with_pre_perfect_correlation():
    metric = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    pre = pd.Series([2.0, 4.0, 6.0, 8.0, 10.0])
    rho = power.correlation_with_pre(metric, pre)
    assert rho == pytest.approx(1.0)


def test_correlation_with_pre_handles_nans():
    metric = pd.Series([1.0, 2.0, np.nan, 4.0, 5.0])
    pre = pd.Series([2.0, 4.0, 6.0, 8.0, 10.0])
    rho = power.correlation_with_pre(metric, pre)
    assert rho == pytest.approx(1.0)


def test_cuped_variance_multiplier():
    assert power.cuped_variance_multiplier(0.0) == pytest.approx(1.0)
    assert power.cuped_variance_multiplier(0.6) == pytest.approx(1 - 0.36)


def test_cuped_reduces_required_sample_size():
    std, mde_abs = 10.0, 1.0
    n_no_cuped = power.sample_size_continuous(std, mde_abs)
    rho = 0.7
    std_cuped = std * np.sqrt(power.cuped_variance_multiplier(rho))
    n_cuped = power.sample_size_continuous(std_cuped, mde_abs)
    assert n_cuped < n_no_cuped
    assert n_cuped == pytest.approx(n_no_cuped * (1 - rho**2), rel=1e-6)
