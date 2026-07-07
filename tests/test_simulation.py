import numpy as np
import pandas as pd

from abkit.config import DesignConfig, MetricConfig
from abkit.validation.simulation import AAReport, ABReport, run_aa, run_ab


def make_data(n=2000, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.10, size=n),
        }
    )


def make_config(**overrides):
    defaults = dict(
        name="sim_exp",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[
            MetricConfig(name="revenue", type="continuous"),
            MetricConfig(name="clicks", type="binary"),
        ],
        split_method="simple",
        alpha=0.05,
        seed=1,
    )
    defaults.update(overrides)
    return DesignConfig(**defaults)


def test_run_aa_returns_report_with_expected_structure():
    data = make_data()
    config = make_config()
    report = run_aa(data, config, n_sims=300, seed=1, show_progress=False)

    assert isinstance(report, AAReport)
    metrics_covered = {m.metric for m in report.methods}
    assert metrics_covered == {"revenue", "clicks"}
    for m in report.methods:
        assert m.n_sims == 300
        assert 0 <= m.fpr <= 1
        assert m.ci_low <= m.fpr <= m.ci_high


def test_run_aa_fpr_within_tolerance_for_honest_pipeline():
    data = make_data(n=3000)
    config = make_config()
    report = run_aa(data, config, n_sims=500, seed=2, show_progress=False)
    for m in report.methods:
        assert m.passed, f"{m.metric}/{m.method}: FPR={m.fpr:.4f}, CI=[{m.ci_low:.4f},{m.ci_high:.4f}]"


def test_run_aa_compare_methods_adds_more_chains():
    data = make_data()
    config = make_config(metrics=[MetricConfig(name="revenue", type="continuous")])
    report_default = run_aa(data, config, n_sims=50, seed=1, show_progress=False)
    report_compare = run_aa(data, config, n_sims=50, compare_methods=True, seed=1, show_progress=False)
    assert len(report_compare.methods) > len(report_default.methods)


def test_run_ab_detects_high_power_for_large_effect():
    data = make_data(n=3000)
    config = make_config(metrics=[MetricConfig(name="revenue", type="continuous")])
    report = run_ab(data, config, n_sims=300, effect=0.3, seed=3, show_progress=False)

    assert isinstance(report, ABReport)
    assert len(report.methods) == 1
    m = report.methods[0]
    assert m.empirical_power > 0.9
    assert m.analytical_power is not None
    assert m.analytical_power > 0.9


def test_run_ab_empirical_matches_analytical_power_closely():
    data = make_data(n=4000)
    config = make_config(metrics=[MetricConfig(name="revenue", type="continuous")])
    report = run_ab(data, config, n_sims=500, effect=0.1, seed=4, show_progress=False)
    m = report.methods[0]
    assert abs(m.empirical_power - m.analytical_power) < 0.1


def test_run_ab_binary_metric_computes_analytical_power():
    data = make_data(n=4000)
    config = make_config(metrics=[MetricConfig(name="clicks", type="binary")])
    report = run_ab(data, config, n_sims=300, effect=0.3, seed=5, show_progress=False)
    m = report.methods[0]
    assert m.analytical_power is not None


def test_run_ab_low_power_scenario_flags_discrepancy_or_matches():
    # маленький эффект и маленькая выборка -> низкая мощность; просто проверяем,
    # что структура репорта корректна и предупреждение (если есть) осмысленно
    data = make_data(n=500)
    config = make_config(metrics=[MetricConfig(name="clicks", type="binary")])
    report = run_ab(data, config, n_sims=300, effect=0.05, seed=6, show_progress=False)
    m = report.methods[0]
    assert 0 <= m.empirical_power <= 1
    if m.discrepancy_warning:
        assert "расходится" in m.discrepancy_warning


def test_run_aa_reproducible_with_same_seed():
    data = make_data()
    config = make_config(metrics=[MetricConfig(name="revenue", type="continuous")])
    report1 = run_aa(data, config, n_sims=100, seed=42, show_progress=False)
    report2 = run_aa(data, config, n_sims=100, seed=42, show_progress=False)
    assert report1.methods[0].fpr == report2.methods[0].fpr


def test_run_aa_with_n_jobs_parallel_matches_sequential_shape():
    data = make_data()
    config = make_config(metrics=[MetricConfig(name="revenue", type="continuous")])
    report_seq = run_aa(data, config, n_sims=60, seed=7, n_jobs=1, show_progress=False)
    report_par = run_aa(data, config, n_sims=60, seed=7, n_jobs=2, show_progress=False)
    assert len(report_seq.methods) == len(report_par.methods)
    assert report_par.methods[0].n_sims == 60


def test_run_aa_progress_callback_called_for_each_round():
    data = make_data()
    config = make_config(metrics=[MetricConfig(name="revenue", type="continuous")])
    calls = []
    run_aa(
        data, config, n_sims=25, seed=1, show_progress=False,
        progress_callback=lambda done, total: calls.append((done, total)),
    )
    assert calls == [(i, 25) for i in range(1, 26)]


def test_run_ab_progress_callback_called_for_each_round():
    data = make_data()
    config = make_config(metrics=[MetricConfig(name="revenue", type="continuous")])
    calls = []
    run_ab(
        data, config, n_sims=15, effect=0.1, seed=1, show_progress=False,
        progress_callback=lambda done, total: calls.append((done, total)),
    )
    assert calls == [(i, 15) for i in range(1, 16)]
