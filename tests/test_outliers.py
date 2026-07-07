import numpy as np
import pandas as pd
import pytest

from abkit.pipeline import MetricContext
from abkit.preprocessing.outliers import Log1p, RemoveOutliers, Winsorize


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


def test_remove_outliers_uses_combined_threshold_for_both_groups():
    # control has an extreme outlier, treatment doesn't -> threshold from combined data
    # should still cut treatment's top values if they exceed the combined 99th percentile
    values = list(range(100)) + [10000] + list(range(100, 200))
    group = ["control"] * 101 + ["treatment"] * 100
    ctx = make_ctx(values, group)

    result_ctx = RemoveOutliers(upper_q=0.99).apply(ctx)

    assert 10000 not in result_ctx.values.values
    assert sum(result_ctx.n_removed.values()) > 0


def test_remove_outliers_records_n_removed_per_group():
    values = [1.0] * 90 + [1000.0] * 10 + [1.0] * 90 + [1000.0] * 10
    group = ["control"] * 100 + ["treatment"] * 100
    ctx = make_ctx(values, group)

    result_ctx = RemoveOutliers(upper_q=0.90).apply(ctx)

    assert result_ctx.n_removed["control"] > 0
    assert result_ctx.n_removed["treatment"] > 0


def test_remove_outliers_rejects_invalid_quantiles():
    with pytest.raises(ValueError):
        RemoveOutliers(lower_q=0.9, upper_q=0.1)


def test_winsorize_clips_instead_of_removing():
    values = list(range(100)) + [10000]
    group = ["control"] * 100 + ["treatment"]
    ctx = make_ctx(values, group)

    result_ctx = Winsorize(upper_q=0.95).apply(ctx)

    assert len(result_ctx.values) == len(values)  # ничего не удалено
    assert result_ctx.values.max() < 10000
    assert sum(result_ctx.n_removed.values()) > 0


def test_winsorize_no_effect_when_no_outliers():
    values = list(range(1, 101))
    group = ["control"] * 50 + ["treatment"] * 50
    ctx = make_ctx(values, group)

    result_ctx = Winsorize(lower_q=0.0, upper_q=1.0).apply(ctx)
    pd.testing.assert_series_equal(
        result_ctx.values.reset_index(drop=True), pd.Series(values, dtype=float), check_dtype=False
    )


def test_log1p_transforms_values():
    values = [0.0, 1.0, np.e - 1]
    ctx = make_ctx(values, ["control", "control", "treatment"])
    result_ctx = Log1p().apply(ctx)
    assert result_ctx.values.iloc[0] == pytest.approx(0.0)
    assert result_ctx.values.iloc[1] == pytest.approx(np.log(2))


def test_log1p_rejects_negative_values():
    ctx = make_ctx([-1.0, 2.0], ["control", "treatment"])
    with pytest.raises(ValueError, match="неотрицательных"):
        Log1p().apply(ctx)


def test_remove_outliers_keeps_covariate_and_stratum_aligned():
    values = list(range(100)) + [10000]
    group = ["control"] * 100 + ["treatment"]
    covariate = pd.Series(range(101), dtype=float)
    stratum = pd.Series(["a"] * 101)
    ctx = make_ctx(values, group, covariate=covariate, stratum=stratum)

    result_ctx = RemoveOutliers(upper_q=0.99).apply(ctx)
    assert len(result_ctx.covariate) == len(result_ctx.values)
    assert len(result_ctx.stratum) == len(result_ctx.values)
