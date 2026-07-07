import numpy as np
import pandas as pd

from abkit.checks import check_pre_period_aa, check_srm, check_strata_balance
from abkit.config import MetricConfig


def test_srm_passes_when_ratios_match():
    observed = {"control": 5000, "treatment": 5000}
    expected = {"control": 0.5, "treatment": 0.5}
    result = check_srm(observed, expected)
    assert result.passed
    assert result.p_value > 0.001


def test_srm_fails_on_clear_mismatch():
    observed = {"control": 4500, "treatment": 5500}
    expected = {"control": 0.5, "treatment": 0.5}
    result = check_srm(observed, expected)
    assert not result.passed
    assert result.p_value < 0.001


def test_srm_handles_multiple_groups():
    observed = {"control": 3333, "a": 3333, "b": 3334}
    expected = {"control": 1 / 3, "a": 1 / 3, "b": 1 / 3}
    result = check_srm(observed, expected)
    assert result.passed


def test_strata_balance_passes_for_balanced_split():
    rng = np.random.default_rng(0)
    n = 4000
    stratum = pd.Series(rng.choice(["a", "b", "c"], size=n))
    group = pd.Series(rng.choice(["control", "treatment"], size=n))  # независимо от страты
    result = check_strata_balance(stratum, group)
    assert result.passed


def test_strata_balance_fails_for_imbalanced_split():
    # страта 'a' почти целиком в control, страта 'b' почти целиком в treatment
    stratum = pd.Series(["a"] * 500 + ["b"] * 500)
    group = pd.Series(["control"] * 480 + ["treatment"] * 20 + ["control"] * 20 + ["treatment"] * 480)
    result = check_strata_balance(stratum, group)
    assert not result.passed


def test_strata_balance_trivial_when_single_stratum():
    stratum = pd.Series(["_all_"] * 100)
    group = pd.Series(["control"] * 50 + ["treatment"] * 50)
    result = check_strata_balance(stratum, group)
    assert result.passed
    assert result.p_value == 1.0


def test_pre_period_aa_passes_when_no_real_difference():
    rng = np.random.default_rng(42)
    n = 2000
    data = pd.DataFrame({"revenue_pre": rng.normal(100, 20, size=n)})
    group = pd.Series(["control"] * (n // 2) + ["treatment"] * (n // 2))
    metrics = [MetricConfig(name="revenue", type="continuous", pre_col="revenue_pre")]

    results = check_pre_period_aa(data, group, metrics, control_name="control")
    assert len(results) == 1
    assert results[0].passed


def test_pre_period_aa_fails_when_groups_differ():
    rng = np.random.default_rng(1)
    n_control, n_treat = 1000, 1000
    control_vals = rng.normal(100, 20, size=n_control)
    treat_vals = rng.normal(110, 20, size=n_treat)  # искусственный сдвиг -> нечестный сплит
    data = pd.DataFrame({"revenue_pre": np.concatenate([control_vals, treat_vals])})
    group = pd.Series(["control"] * n_control + ["treatment"] * n_treat)
    metrics = [MetricConfig(name="revenue", type="continuous", pre_col="revenue_pre")]

    results = check_pre_period_aa(data, group, metrics, control_name="control")
    assert len(results) == 1
    assert not results[0].passed


def test_pre_period_aa_skips_metrics_without_pre_col():
    data = pd.DataFrame({"clicks": [1, 2, 3, 4]})
    group = pd.Series(["control", "control", "treatment", "treatment"])
    metrics = [MetricConfig(name="clicks", type="binary")]
    results = check_pre_period_aa(data, group, metrics, control_name="control")
    assert results == []


def test_pre_period_aa_handles_multiple_treatment_groups():
    rng = np.random.default_rng(7)
    n = 300
    data = pd.DataFrame({"revenue_pre": rng.normal(100, 20, size=n * 3)})
    group = pd.Series(["control"] * n + ["treatment_a"] * n + ["treatment_b"] * n)
    metrics = [MetricConfig(name="revenue", type="continuous", pre_col="revenue_pre")]

    results = check_pre_period_aa(data, group, metrics, control_name="control")
    assert {r.treatment_group for r in results} == {"treatment_a", "treatment_b"}
