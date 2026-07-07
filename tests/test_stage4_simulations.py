"""Симуляционные тесты готовности этапа 4: FPR методов в допуске, дельта-метод
держит FPR на кластерных ratio-данных (а наивный t-test по строкам — нет)."""

import numpy as np
import pandas as pd

from abkit.analysis.tests import Bootstrap, DeltaMethodTTest, MannWhitney, WelchTTest, ZTestProportions
from abkit.analysis.variance_reduction import CUPED, PostStratification
from abkit.pipeline import MetricContext, Pipeline


def _fpr(p_values: list[float], alpha: float = 0.05) -> float:
    return float(np.mean(np.array(p_values) < alpha))


def _make_ctx(values, group, metric_type="continuous", **kwargs):
    defaults = dict(
        metric_name="m",
        metric_type=metric_type,
        control_name="control",
        treatment_name="treatment",
        values=pd.Series(values) if values is not None else pd.Series([0.0]),
        group=pd.Series(group),
    )
    defaults.update(kwargs)
    return MetricContext(**defaults)


N_SIMS = 1000
LOW, HIGH = 0.03, 0.07  # допуск для alpha=0.05 при n_sims=1000 (~3 сигмы)


def test_welch_fpr_in_range():
    rng = np.random.default_rng(0)
    n = 150
    p_values = []
    for _ in range(N_SIMS):
        control = rng.normal(100, 20, size=n)
        treatment = rng.normal(100, 20, size=n)
        ctx = WelchTTest().apply(
            _make_ctx(np.concatenate([control, treatment]), ["control"] * n + ["treatment"] * n)
        )
        p_values.append(ctx.result.p_value)
    fpr = _fpr(p_values)
    assert LOW <= fpr <= HIGH, f"Welch FPR={fpr:.4f} вне допуска"


def test_ztest_proportions_fpr_in_range():
    rng = np.random.default_rng(1)
    n = 400
    p_values = []
    for _ in range(N_SIMS):
        control = rng.binomial(1, 0.1, size=n)
        treatment = rng.binomial(1, 0.1, size=n)
        ctx = ZTestProportions().apply(
            _make_ctx(
                np.concatenate([control, treatment]),
                ["control"] * n + ["treatment"] * n,
                metric_type="binary",
            )
        )
        p_values.append(ctx.result.p_value)
    fpr = _fpr(p_values)
    assert LOW <= fpr <= HIGH, f"ZTestProportions FPR={fpr:.4f} вне допуска"


def test_cuped_welch_fpr_in_range():
    rng = np.random.default_rng(2)
    n = 150
    pipeline = Pipeline([CUPED(), WelchTTest()])
    p_values = []
    for _ in range(N_SIMS):
        pre = rng.normal(100, 20, size=2 * n)
        noise = rng.normal(0, 10, size=2 * n)
        values = 0.7 * pre + noise
        group = ["control"] * n + ["treatment"] * n
        ctx = _make_ctx(values, group, covariate=pd.Series(pre))
        ctx = pipeline.run(ctx)
        p_values.append(ctx.result.p_value)
    fpr = _fpr(p_values)
    assert LOW <= fpr <= HIGH, f"CUPED+Welch FPR={fpr:.4f} вне допуска"


def test_mann_whitney_fpr_in_range():
    rng = np.random.default_rng(3)
    n = 150
    p_values = []
    for _ in range(N_SIMS):
        control = rng.normal(100, 20, size=n)
        treatment = rng.normal(100, 20, size=n)
        ctx = MannWhitney().apply(
            _make_ctx(np.concatenate([control, treatment]), ["control"] * n + ["treatment"] * n)
        )
        p_values.append(ctx.result.p_value)
    fpr = _fpr(p_values)
    assert LOW <= fpr <= HIGH, f"MannWhitney FPR={fpr:.4f} вне допуска"


def test_bootstrap_fpr_in_range():
    rng = np.random.default_rng(4)
    n = 150
    n_sims = 400  # bootstrap дороже -> меньше симуляций, шире допуск
    p_values = []
    for i in range(n_sims):
        control = rng.normal(100, 20, size=n)
        treatment = rng.normal(100, 20, size=n)
        ctx = Bootstrap(n_boot=1000, method="bca", seed=i).apply(
            _make_ctx(np.concatenate([control, treatment]), ["control"] * n + ["treatment"] * n)
        )
        p_values.append(ctx.result.p_value)
    fpr = _fpr(p_values)
    se = np.sqrt(0.05 * 0.95 / n_sims)
    assert abs(fpr - 0.05) <= 3.5 * se, f"Bootstrap FPR={fpr:.4f} вне допуска"


def test_post_stratification_fpr_in_range():
    rng = np.random.default_rng(5)
    n = 600
    p_values = []
    for _ in range(N_SIMS):
        stratum = rng.choice(["a", "b", "c"], size=n)
        values = rng.normal(100, 20, size=n)
        group = rng.choice(["control", "treatment"], size=n)
        ctx = _make_ctx(values, group, stratum=pd.Series(stratum))
        ctx = PostStratification().apply(ctx)
        p_values.append(ctx.result.p_value)
    fpr = _fpr(p_values)
    assert LOW <= fpr <= HIGH, f"PostStratification FPR={fpr:.4f} вне допуска"


def test_delta_method_ttest_fpr_in_range():
    rng = np.random.default_rng(6)
    n = 300
    p_values = []
    for _ in range(N_SIMS):
        sessions = rng.integers(3, 10, size=2 * n)
        clicks = rng.binomial(sessions, 0.2)
        group = ["control"] * n + ["treatment"] * n
        ctx = _make_ctx(
            None,
            group,
            metric_type="ratio",
            num=pd.Series(clicks),
            den=pd.Series(sessions),
        )
        ctx = DeltaMethodTTest().apply(ctx)
        p_values.append(ctx.result.p_value)
    fpr = _fpr(p_values)
    assert LOW <= fpr <= HIGH, f"DeltaMethodTTest FPR={fpr:.4f} вне допуска"


def _simulate_clustered_ratio_round(rng, n_users_per_group=150, sessions_lo=10, sessions_hi=40,
                                     base_rate=0.2, user_sd=0.15):
    """Один прогон: кликовая метрика с сильной внутрипользовательской корреляцией
    (кликабельность варьируется по юзерам), эффекта нет (H0). Юнит рандомизации —
    юзер, но данные приходят по сессиям (юнит анализа мельче юнита рандомизации)."""
    naive_control, naive_treatment = [], []
    agg_num_control, agg_den_control = [], []
    agg_num_treatment, agg_den_treatment = [], []

    for grp in ("control", "treatment"):
        for _ in range(n_users_per_group):
            true_rate = np.clip(rng.normal(base_rate, user_sd), 0.01, 0.7)
            n_sessions = rng.integers(sessions_lo, sessions_hi + 1)
            clicks = rng.binomial(1, true_rate, size=n_sessions)
            if grp == "control":
                naive_control.append(clicks)
                agg_num_control.append(clicks.sum())
                agg_den_control.append(n_sessions)
            else:
                naive_treatment.append(clicks)
                agg_num_treatment.append(clicks.sum())
                agg_den_treatment.append(n_sessions)

    naive_control_rows = np.concatenate(naive_control).astype(float)
    naive_treatment_rows = np.concatenate(naive_treatment).astype(float)

    return (
        naive_control_rows,
        naive_treatment_rows,
        np.array(agg_num_control, dtype=float),
        np.array(agg_den_control, dtype=float),
        np.array(agg_num_treatment, dtype=float),
        np.array(agg_den_treatment, dtype=float),
    )


def test_delta_method_holds_fpr_but_naive_row_level_ttest_does_not_on_clustered_ratio_data():
    """Критерий готовности этапа 4: на кластерных ratio-данных (юнит анализа —
    сессия, юнит рандомизации — юзер) дельта-метод по юзеру держит FPR, а наивный
    t-test по строкам (сессиям), игнорирующий кластеризацию, — нет (негативный тест)."""
    rng = np.random.default_rng(7)
    n_sims = 300
    naive_p_values = []
    delta_p_values = []

    for _ in range(n_sims):
        control_rows, treatment_rows, num_c, den_c, num_t, den_t = _simulate_clustered_ratio_round(rng)

        naive_ctx = WelchTTest().apply(
            _make_ctx(
                np.concatenate([control_rows, treatment_rows]),
                ["control"] * len(control_rows) + ["treatment"] * len(treatment_rows),
            )
        )
        naive_p_values.append(naive_ctx.result.p_value)

        delta_ctx = DeltaMethodTTest().apply(
            _make_ctx(
                None,
                ["control"] * len(num_c) + ["treatment"] * len(num_t),
                metric_type="ratio",
                num=pd.Series(np.concatenate([num_c, num_t])),
                den=pd.Series(np.concatenate([den_c, den_t])),
            )
        )
        delta_p_values.append(delta_ctx.result.p_value)

    naive_fpr = _fpr(naive_p_values)
    delta_fpr = _fpr(delta_p_values)

    assert 0.02 <= delta_fpr <= 0.08, f"Дельта-метод: FPR={delta_fpr:.4f} вне допуска"
    assert naive_fpr > 0.15, (
        f"Наивный t-test по строкам должен показывать инфлированный FPR из-за "
        f"псевдоповторов внутри юзера, получено {naive_fpr:.4f}"
    )
