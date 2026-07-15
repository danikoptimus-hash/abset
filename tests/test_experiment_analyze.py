import numpy as np
import pandas as pd
import pytest

import abkit.experiment as experiment_module
from abkit.checks import AnalysisError
from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import DesignError, Experiment
from abkit.pipeline import MetricContext, Step


def design_simple_experiment(tmp_path, n=4000, metrics=None, name="analyze_exp", seed=42):
    rng = np.random.default_rng(0)
    design_data = pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.10, size=n),
            "orders": rng.integers(0, 5, size=n),
            "sessions": rng.integers(1, 10, size=n),
        }
    )
    config = DesignConfig(
        name=name,
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=metrics
        or [
            MetricConfig(name="revenue", type="continuous"),
            MetricConfig(name="clicks", type="binary"),
        ],
        sample_size=n,
        split_method="simple",
        seed=seed,
    )
    return Experiment.design(config, design_data, experiments_dir=tmp_path)


def test_analyze_detects_injected_positive_effect(tmp_path):
    experiment = design_simple_experiment(tmp_path)
    rng = np.random.default_rng(5)
    assignments = experiment.assignments
    n = len(assignments)
    revenue = rng.normal(100, 20, size=n)
    clicks = rng.binomial(1, 0.10, size=n)
    is_treatment = (assignments["group"] == "treatment").to_numpy()
    revenue[is_treatment] += 15  # заметный эффект
    post_data = pd.DataFrame({"user_id": assignments["unit_id"], "revenue": revenue, "clicks": clicks})

    results = experiment.analyze(post_data)

    revenue_result = results["revenue"][0]
    assert revenue_result.effect_abs > 0
    assert revenue_result.p_value < 0.01
    assert results.verdict("revenue") == "significant_positive"


def test_analyze_handles_int64_post_data_unit_id(tmp_path):
    """Regression: a post-data CSV with a purely-numeric unit_id column is
    auto-parsed by pandas as int64. assignments.unit_id is normalized to str
    at design time — the join must still match every unit, not crash or
    silently drop everyone."""
    n = 4000
    rng = np.random.default_rng(0)
    design_data = pd.DataFrame(
        {
            "user_id": np.arange(n),
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.10, size=n),
        }
    )
    config = DesignConfig(
        name="analyze_int64_exp",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous"), MetricConfig(name="clicks", type="binary")],
        sample_size=n,
        split_method="simple",
        seed=42,
    )
    experiment = Experiment.design(config, design_data, experiments_dir=tmp_path)
    assert isinstance(experiment.assignments["unit_id"].iloc[0], str)

    rng2 = np.random.default_rng(7)
    post_data = pd.DataFrame(
        {
            "user_id": pd.array(range(n), dtype="int64"),
            "revenue": rng2.normal(100, 20, size=n),
            "clicks": rng2.binomial(1, 0.10, size=n),
        }
    )
    results = experiment.analyze(post_data)
    revenue_result = results["revenue"][0]
    assert sum(revenue_result.n.values()) == n


def test_analyze_compare_methods_completes_at_300k_rows(tmp_path):
    """Regression for a real crash: compare_methods=True unconditionally adds
    Bootstrap (abkit/experiment.py::compare_methods_chains) for continuous
    metrics — Bootstrap.apply() used to materialize a full n_boot x n_units
    resampling index matrix (~45 GB at n_boot=10000, 150k users/group),
    which OOM-killed the whole backend process. Batched resampling
    (ABKIT_BOOTSTRAP_BATCH) must keep this within reach on the exact scale
    that used to crash (~300k total rows, ~150k per group) without lowering
    n_boot from its production default."""
    n = 300_000
    rng = np.random.default_rng(0)
    design_data = pd.DataFrame(
        {"user_id": [f"u{i}" for i in range(n)], "revenue": rng.normal(100, 20, size=n)}
    )
    config = DesignConfig(
        name="compare_methods_scale_exp",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous")],
        sample_size=n,
        split_method="simple",
        seed=42,
    )
    experiment = Experiment.design(config, design_data, experiments_dir=tmp_path)
    post_data = pd.DataFrame(
        {"user_id": experiment.assignments["unit_id"], "revenue": rng.normal(100, 20, size=n)}
    )

    results = experiment.analyze(post_data, compare_methods=True)

    bootstrap_results = [r for r in results["revenue"] if r.method.startswith("Bootstrap")]
    assert len(bootstrap_results) == 1
    assert "(failed)" not in bootstrap_results[0].method
    assert not np.isnan(bootstrap_results[0].p_value)


class _AlwaysFailStep(Step):
    stage = "test"

    @property
    def name(self):
        return "AlwaysFail"

    def apply(self, ctx: MetricContext) -> MetricContext:
        raise ValueError("synthetic failure for test")


def test_analyze_single_failed_comparison_method_does_not_kill_others(tmp_path, monkeypatch):
    """A single alternative method (compare_methods=True) raising must not
    take down the designed method or the other alternatives — it shows up
    as its own 'failed' result instead (see
    abkit/experiment.py::_failed_method_result and the extra_chains loop in
    analyze())."""
    experiment = design_simple_experiment(tmp_path)
    rng = np.random.default_rng(9)
    assignments = experiment.assignments
    n = len(assignments)
    post_data = pd.DataFrame(
        {
            "user_id": assignments["unit_id"],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.10, size=n),
        }
    )

    from abkit.analysis.tests import WelchTTest

    def fake_chains(metric, seed=None):
        if metric.type != "continuous":
            return []
        return [[_AlwaysFailStep()], [WelchTTest()]]

    monkeypatch.setattr(experiment_module, "compare_methods_chains", fake_chains)

    results = experiment.analyze(post_data, compare_methods=True)
    revenue_results = results["revenue"]

    designed = [r for r in revenue_results if r.is_designed_method]
    assert len(designed) == 1  # designed method still computed normally

    failed = [r for r in revenue_results if "(failed)" in r.method]
    assert len(failed) == 1
    assert np.isnan(failed[0].p_value)
    assert failed[0].warnings and failed[0].warnings[0].startswith("failed: synthetic failure")

    succeeded_extra = [
        r for r in revenue_results if not r.is_designed_method and "(failed)" not in r.method
    ]
    assert len(succeeded_extra) == 1  # the other alternative (WelchTTest) still completed


def test_analyze_no_effect_gives_no_effect_verdict(tmp_path):
    experiment = design_simple_experiment(tmp_path)
    rng = np.random.default_rng(6)
    assignments = experiment.assignments
    n = len(assignments)
    post_data = pd.DataFrame(
        {
            "user_id": assignments["unit_id"],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.10, size=n),
        }
    )
    results = experiment.analyze(post_data)
    assert results.verdict("revenue") in ("no_effect_detected", "significant_positive", "significant_negative")
    # структура результатов корректна независимо от вердикта конкретного прогона
    assert "revenue" in results.metrics
    assert "clicks" in results.metrics


def test_analyze_applies_multiple_testing_correction(tmp_path):
    experiment = design_simple_experiment(tmp_path)
    rng = np.random.default_rng(7)
    assignments = experiment.assignments
    n = len(assignments)
    post_data = pd.DataFrame(
        {
            "user_id": assignments["unit_id"],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.10, size=n),
        }
    )
    results = experiment.analyze(post_data, correction="holm")
    for r in results.results:
        assert r.p_value_adjusted is not None
        assert r.p_value_adjusted >= r.p_value - 1e-12


def test_analyze_raises_on_duplicate_data_without_date_col(tmp_path):
    """Дубли по unit_col без date_col — понятная ошибка с инструкцией, а не
    голое 'дублирующихся значений'."""
    experiment = design_simple_experiment(tmp_path)
    assignments = experiment.assignments
    n = len(assignments)
    post_data = pd.DataFrame(
        {
            "user_id": list(assignments["unit_id"]) + [assignments["unit_id"].iloc[0]],
            "revenue": np.random.default_rng(1).normal(100, 20, size=n + 1),
            "clicks": np.random.default_rng(1).binomial(1, 0.1, size=n + 1),
        }
    )
    with pytest.raises(AnalysisError, match="duplicate"):
        experiment.analyze(post_data)


def test_analyze_raises_clear_error_when_unit_col_missing_from_post_data(tmp_path):
    """Regression (found via a real internal_error report, root cause: an
    unguarded data[self.config.unit_col] access — raw pandas KeyError, not
    one of the domain exceptions backend/jobs/runner.py recognizes, so it
    surfaced as an opaque 'Internal processing error' instead of telling the
    user what actually went wrong): uploading post-period data that doesn't
    have the design's unit-id column (e.g. the wrong file, or one exported
    without it) must raise a clear, actionable AnalysisError — same
    treatment as the neighboring duplicate-data and missing-date-col checks,
    not a crash. This is exactly the scenario none of the existing analyze
    tests/fixtures exercised (they all conveniently reuse assignments'
    unit_id column), which is why it slipped through undetected."""
    experiment = design_simple_experiment(tmp_path)
    assignments = experiment.assignments
    n = len(assignments)
    post_data = pd.DataFrame(
        {
            "not_user_id": list(assignments["unit_id"]),
            "revenue": np.random.default_rng(1).normal(100, 20, size=n),
            "clicks": np.random.default_rng(1).binomial(1, 0.1, size=n),
        }
    )
    with pytest.raises(AnalysisError, match="Unit column 'user_id' is not in the uploaded data"):
        experiment.analyze(post_data)


def test_analyze_raises_clear_error_when_post_data_has_own_group_column(tmp_path):
    """Regression (ref edb716f1, a real user report): a post-period export
    that carries its own 'group' column (e.g. re-exporting the assignment it
    already knows, alongside the metrics) used to make pandas' merge inside
    checks.join_with_assignments() silently rename BOTH sides' 'group' to
    'group_x'/'group_y' — the downstream `merged["group"]` access then raised
    a raw KeyError, surfacing only as an opaque 'Internal processing error'
    instead of telling the user what actually went wrong. None of the
    existing analyze fixtures had this collision (post_data is always built
    without a 'group' column), which is why it slipped through undetected."""
    experiment = design_simple_experiment(tmp_path)
    assignments = experiment.assignments
    n = len(assignments)
    post_data = pd.DataFrame(
        {
            "user_id": list(assignments["unit_id"]),
            "group": list(assignments["group"]),
            "revenue": np.random.default_rng(1).normal(100, 20, size=n),
            "clicks": np.random.default_rng(1).binomial(1, 0.1, size=n),
        }
    )
    with pytest.raises(AnalysisError, match="'group'"):
        experiment.analyze(post_data)


def test_analyze_succeeds_with_warning_when_declared_pre_col_missing_from_post_data(tmp_path):
    """Regression (found while reproducing ref edb716f1's real dataset end to
    end, a second crash past the group/stratum fix above): a metric that
    declares pre_col at design time, analyzed against post-data that lacks
    that column, used to make the designed pipeline's CUPED step raise a raw
    ValueError uncaught — unlike compare_methods' alt chains (already
    tolerant of a per-chain failure), the designed chain has no verdict
    without a caught exception, so the whole job crashed into an opaque
    'Internal processing error' instead of just skipping CUPED and reporting
    plain Welch, same as the design report already does for a metric with no
    pre_col declared at all."""
    rng = np.random.default_rng(0)
    n = 4000
    design_data = pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "revenue": rng.normal(100, 20, size=n),
            "revenue_pre": rng.normal(95, 18, size=n),
        }
    )
    config = DesignConfig(
        name="cuped_missing_precol_exp",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous", pre_col="revenue_pre")],
        sample_size=n,
        split_method="simple",
        seed=42,
    )
    experiment = Experiment.design(config, design_data, experiments_dir=tmp_path)
    assignments = experiment.assignments
    n_assigned = len(assignments)
    post_data = pd.DataFrame(
        {
            "user_id": list(assignments["unit_id"]),
            "revenue": np.random.default_rng(1).normal(100, 20, size=n_assigned),
        }
    )
    results = experiment.analyze(post_data)
    revenue_result = results["revenue"][0]
    assert revenue_result is not None
    assert any("pre-period covariate" in w for w in revenue_result.warnings)


def test_analyze_progress_callback_reports_stages_in_order(tmp_path):
    """UI (app.py) показывает прогресс через st.status по этапам analyze() —
    нужна гарантия, что callback реально вызывается на каждом этапе (join,
    честность, по метрике, поправка), включая счетчик "метрика i из N"."""
    experiment = design_simple_experiment(tmp_path)
    rng = np.random.default_rng(13)
    assignments = experiment.assignments
    n = len(assignments)
    post_data = pd.DataFrame(
        {
            "user_id": assignments["unit_id"],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.10, size=n),
        }
    )
    stages: list[str] = []

    experiment.analyze(post_data, progress_callback=stages.append)

    assert stages == [
        "Joining with assignments...",
        "Checking validity (SRM, data loss)...",
        "Computing metric 1 of 2: revenue...",
        "Computing metric 2 of 2: clicks...",
        "Applying multiple-testing correction...",
    ]


def test_analyze_progress_callback_reports_aggregation_stage_with_date_col(tmp_path):
    experiment = design_simple_experiment(tmp_path, n=200, name="progress_daily_exp")
    assignments = experiment.assignments
    rng = np.random.default_rng(14)
    rows = []
    for _, r in assignments.iterrows():
        for day in range(2):
            rows.append(
                {
                    "user_id": r["unit_id"],
                    "event_date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                    "revenue": rng.normal(50, 10),
                    "clicks": rng.binomial(1, 0.1),
                }
            )
    daily_data = pd.DataFrame(rows)
    stages: list[str] = []

    experiment.analyze(daily_data, date_col="event_date", progress_callback=stages.append)

    assert "Aggregating data by day..." in stages
    assert stages.index("Aggregating data by day...") < stages.index("Joining with assignments...")


def test_analyze_daily_data_aggregation_matches_pre_aggregated(tmp_path):
    """Дубли по unit_col + date_col: агрегация (continuous=sum, binary=max) дает
    тот же результат основного анализа, что и заранее агрегированные (одна
    строка на юзера) данные."""
    experiment = design_simple_experiment(tmp_path, n=1000, name="daily_agg_exp")
    assignments = experiment.assignments
    n = len(assignments)
    rng = np.random.default_rng(30)

    revenue_total = rng.normal(100, 20, size=n)
    clicks_flag = rng.binomial(1, 0.10, size=n)

    pre_aggregated = pd.DataFrame(
        {"user_id": assignments["unit_id"], "revenue": revenue_total, "clicks": clicks_flag}
    )

    n_days = 3
    weights = rng.dirichlet(np.ones(n_days), size=n)
    click_day = rng.integers(0, n_days, size=n)

    daily_rows = []
    for i, user_id in enumerate(assignments["unit_id"]):
        for day in range(n_days):
            daily_rows.append(
                {
                    "user_id": user_id,
                    "event_date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                    "revenue": revenue_total[i] * weights[i, day],
                    "clicks": 1 if clicks_flag[i] == 1 and click_day[i] == day else 0,
                }
            )
    daily_data = pd.DataFrame(daily_rows)

    results_pre = experiment.analyze(pre_aggregated)
    results_daily = experiment.analyze(daily_data, date_col="event_date")

    assert results_daily["revenue"][0].effect_abs == pytest.approx(
        results_pre["revenue"][0].effect_abs, abs=1e-8
    )
    assert results_daily["revenue"][0].p_value == pytest.approx(
        results_pre["revenue"][0].p_value, abs=1e-8
    )
    assert results_daily["clicks"][0].effect_abs == pytest.approx(
        results_pre["clicks"][0].effect_abs, abs=1e-8
    )
    assert any("day-by-day breakdown" in w for w in results_daily.global_warnings)


def test_analyze_cumulative_lift_builds_on_daily_data(tmp_path):
    """Кумулятивный лифт строится по дням из сырых (не агрегированных) данных;
    последний день кумулятивного окна = вся история -> совпадает с эффектом
    основного анализа.

    Regression guard: daily_results' effect_rel/ci_lower/ci_upper are raw
    fractions (0.02 = 2%), same convention as TestResult.effect_rel — NOT
    pre-multiplied by 100. They used to be pre-multiplied here, which was
    invisible at the core-test level (this assertion used to also multiply
    by 100 on the right-hand side, masking it) but doubled the React chart's
    axis 100x once its own *100 conversion ran on top (CumulativeLiftChart.tsx).
    """
    experiment = design_simple_experiment(
        tmp_path,
        n=1000,
        metrics=[MetricConfig(name="revenue", type="continuous")],
        name="cumlift_exp",
    )
    assignments = experiment.assignments
    n = len(assignments)
    rng = np.random.default_rng(31)
    is_treatment = (assignments["group"] == "treatment").to_numpy()

    n_days = 5
    daily_rows = []
    for day in range(n_days):
        revenue_day = rng.normal(10, 3, size=n)
        revenue_day[is_treatment] += 1.0
        for i, user_id in enumerate(assignments["unit_id"]):
            daily_rows.append(
                {
                    "user_id": user_id,
                    "event_date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                    "revenue": revenue_day[i],
                }
            )
    daily_data = pd.DataFrame(daily_rows)

    results = experiment.analyze(daily_data, date_col="event_date")
    daily_df = results.context["daily_results"]["revenue"]["treatment"]

    assert len(daily_df) == n_days
    assert list(daily_df["date"]) == sorted(daily_df["date"])
    assert daily_df.iloc[-1]["effect_rel"] == pytest.approx(
        results["revenue"][0].effect_rel, abs=1e-6
    )


def test_analyze_ratio_metric_aggregates_num_den_separately(tmp_path):
    """Ratio-метрика на дневных данных: num и den агрегируются раздельно суммой,
    деление — на уровне юзера ПОСЛЕ агрегации (не среднее подневных отношений)."""
    experiment = design_simple_experiment(
        tmp_path,
        metrics=[MetricConfig(name="conv", type="ratio", num="orders", den="sessions")],
        name="ratio_daily_exp",
    )
    assignments = experiment.assignments
    n = len(assignments)
    rng = np.random.default_rng(32)

    n_days = 4
    orders_daily = rng.integers(0, 3, size=(n, n_days))
    sessions_daily = rng.integers(1, 4, size=(n, n_days))

    pre_aggregated = pd.DataFrame(
        {
            "user_id": assignments["unit_id"],
            "orders": orders_daily.sum(axis=1),
            "sessions": sessions_daily.sum(axis=1),
        }
    )

    daily_rows = []
    for i, user_id in enumerate(assignments["unit_id"]):
        for day in range(n_days):
            daily_rows.append(
                {
                    "user_id": user_id,
                    "event_date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                    "orders": orders_daily[i, day],
                    "sessions": sessions_daily[i, day],
                }
            )
    daily_data = pd.DataFrame(daily_rows)

    results_pre = experiment.analyze(pre_aggregated)
    results_daily = experiment.analyze(daily_data, date_col="event_date")

    assert results_daily["conv"][0].effect_abs == pytest.approx(
        results_pre["conv"][0].effect_abs, abs=1e-8
    )
    assert results_daily["conv"][0].p_value == pytest.approx(results_pre["conv"][0].p_value, abs=1e-8)

    # sanity: "сначала делить, потом усреднять" дал бы другой результат -
    # тест действительно проверяет порядок операций, а не совпадение по случайности
    naive_ratio_mean = (orders_daily / np.maximum(sessions_daily, 1)).mean(axis=1)
    assert not np.allclose(naive_ratio_mean, pre_aggregated["orders"] / pre_aggregated["sessions"])


def test_analyze_raises_on_missing_metric_column(tmp_path):
    experiment = design_simple_experiment(tmp_path)
    assignments = experiment.assignments
    post_data = pd.DataFrame({"user_id": assignments["unit_id"], "clicks": [0] * len(assignments)})
    with pytest.raises(AnalysisError, match="revenue"):
        experiment.analyze(post_data)


def test_analyze_ratio_metric_uses_delta_method_by_default(tmp_path):
    experiment = design_simple_experiment(
        tmp_path,
        metrics=[MetricConfig(name="conv", type="ratio", num="orders", den="sessions")],
        name="ratio_analyze_exp",
    )
    assignments = experiment.assignments
    n = len(assignments)
    post_data = pd.DataFrame(
        {
            "user_id": assignments["unit_id"],
            "orders": np.random.default_rng(2).integers(0, 5, size=n),
            "sessions": np.random.default_rng(3).integers(1, 10, size=n),
        }
    )
    results = experiment.analyze(post_data)
    assert results["conv"][0].method == "Delta method (ratio)"


def test_analyze_flags_srm_on_actual_data(tmp_path):
    experiment = design_simple_experiment(tmp_path, n=2000)
    assignments = experiment.assignments
    # искусственно теряем много treatment-строк -> SRM на фактических данных
    kept = assignments[
        (assignments["group"] == "control") | (assignments.index % 3 == 0)
    ]
    rng = np.random.default_rng(9)
    post_data = pd.DataFrame(
        {
            "user_id": kept["unit_id"],
            "revenue": rng.normal(100, 20, size=len(kept)),
            "clicks": rng.binomial(1, 0.1, size=len(kept)),
        }
    )
    results = experiment.analyze(post_data)
    assert any("SRM" in w for w in results.global_warnings)


def test_analyze_without_assignments_raises(tmp_path):
    experiment = design_simple_experiment(tmp_path)
    experiment.assignments = None
    with pytest.raises(DesignError):
        experiment.analyze(pd.DataFrame({"user_id": [], "revenue": []}))


def test_analyze_uses_cuped_by_default_when_pre_col_configured(tmp_path):
    rng = np.random.default_rng(10)
    n = 4000
    pre = rng.normal(100, 20, size=n)
    design_data = pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "revenue": pre * 0.8 + rng.normal(0, 10, size=n),
            "revenue_pre": pre,
        }
    )
    config = DesignConfig(
        name="cuped_default_exp",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous", pre_col="revenue_pre")],
        sample_size=n,
        split_method="simple",
        seed=1,
    )
    experiment = Experiment.design(config, design_data, experiments_dir=tmp_path)
    assignments = experiment.assignments

    pre2 = rng.normal(100, 20, size=n)
    post_data = pd.DataFrame(
        {
            "user_id": assignments["unit_id"],
            "revenue": pre2 * 0.8 + rng.normal(0, 10, size=n),
            "revenue_pre": pre2,
        }
    )
    results = experiment.analyze(post_data)
    assert results["revenue"][0].method == "CUPED + Welch t-test"
    assert results["revenue"][0].variance_reduction is not None
    assert results["revenue"][0].variance_reduction > 0.3


def test_detailed_rows_includes_all_comparisons_grouped_by_metric_designed_first(tmp_path):
    """UX11: детальная таблица результатов должна включать ВСЕ вычисленные
    сравнения (designed и exploratory) — за вычетом дублей designed-метода
    (UX-пакет, дедуп): revenue не имеет pre_col, поэтому его designed-цепочка
    — просто Welch t-test, а первая же alt-цепочка из compare_methods_chains()
    — тоже просто Welch t-test; exact-дубль схлопывается в одну (designed)
    строку.

    Порядок строк (6-part package pt.2, primary-first): больше не плоская
    сортировка по алфавиту (metric, method) — обе метрики здесь primary, так
    что порядок метрик остается порядком объявления в конфиге (revenue,
    затем clicks — 'clicks' < 'revenue' алфавитно, старая сортировка их бы
    переставила), а внутри метрики designed-строка идет первой, альтернативы
    — после (не по алфавиту метода)."""
    experiment = design_simple_experiment(tmp_path)
    rng = np.random.default_rng(12)
    assignments = experiment.assignments
    n = len(assignments)
    post_data = pd.DataFrame(
        {
            "user_id": assignments["unit_id"],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.10, size=n),
        }
    )
    results = experiment.analyze(post_data, compare_methods=True)
    control_name = results.context["control_name"]

    rows = results.detailed_rows(control_name)
    assert len(rows) == len(results.results) - 1
    # ровно одна designed-строка на пару (metric, treatment_group)
    designed_rows = [r for r in rows if r["designed"]]
    assert len(designed_rows) == len(results.metrics)
    # ровно одна строка Welch t-test для revenue — не две (designed + дубль-alt)
    revenue_welch_rows = [r for r in rows if r["metric"] == "revenue" and r["method"] == "Welch t-test"]
    assert len(revenue_welch_rows) == 1
    assert revenue_welch_rows[0]["designed"] is True

    # Metrics form contiguous groups in declaration order (revenue, clicks),
    # not interleaved and not alphabetically reordered.
    seen_metrics: list[str] = []
    for r in rows:
        if not seen_metrics or seen_metrics[-1] != r["metric"]:
            seen_metrics.append(r["metric"])
    assert seen_metrics == ["revenue", "clicks"]
    # Within each metric's group, the designed row comes first.
    for metric_name in seen_metrics:
        metric_rows = [r for r in rows if r["metric"] == metric_name]
        assert metric_rows[0]["designed"] is True

    for row in rows:
        assert row["group"] == f"treatment vs {control_name}"
        assert row["n_control"] is not None and row["n_test"] is not None
        assert row["verdict"] in ("significant_positive", "significant_negative", "no_effect_detected")
        assert row["correction_method"] == results.context["correction"]


def test_analysis_results_metrics_and_results_order_primary_before_secondary(tmp_path):
    """6-part package pt.3: AnalysisResults.__init__ sorts primary metrics
    before secondary (stable — keeps declaration order within a role).
    Declared here with secondary FIRST (clicks, then revenue) so the test
    actually exercises the reordering rather than a config that would pass
    even unsorted."""
    experiment = design_simple_experiment(
        tmp_path,
        metrics=[
            MetricConfig(name="clicks", type="binary", role="secondary"),
            MetricConfig(name="revenue", type="continuous", role="primary"),
        ],
    )
    rng = np.random.default_rng(14)
    assignments = experiment.assignments
    n = len(assignments)
    post_data = pd.DataFrame(
        {
            "user_id": assignments["unit_id"],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.10, size=n),
        }
    )
    results = experiment.analyze(post_data)
    assert results.metrics == ["revenue", "clicks"]
    assert [r.metric for r in results.results] == ["revenue", "clicks"]
    assert [r["metric"] for r in results.detailed_rows("control")] == ["revenue", "clicks"]


def test_detailed_rows_labels_variance_reduction_technique(tmp_path):
    rng = np.random.default_rng(13)
    n = 4000
    pre = rng.normal(100, 20, size=n)
    design_data = pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "revenue": pre * 0.8 + rng.normal(0, 10, size=n),
            "revenue_pre": pre,
        }
    )
    config = DesignConfig(
        name="cuped_detailed_rows_exp",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous", pre_col="revenue_pre")],
        sample_size=n,
        split_method="simple",
        seed=1,
    )
    experiment = Experiment.design(config, design_data, experiments_dir=tmp_path)
    assignments = experiment.assignments
    pre2 = rng.normal(100, 20, size=n)
    post_data = pd.DataFrame(
        {
            "user_id": assignments["unit_id"],
            "revenue": pre2 * 0.8 + rng.normal(0, 10, size=n),
            "revenue_pre": pre2,
        }
    )
    results = experiment.analyze(post_data)
    rows = results.detailed_rows(results.context["control_name"])
    row = next(r for r in rows if r["metric"] == "revenue")
    assert row["variance_reduction"].startswith("CUPED (")


def test_detailed_rows_no_variance_reduction_shows_dash(tmp_path):
    experiment = design_simple_experiment(tmp_path)
    rng = np.random.default_rng(14)
    assignments = experiment.assignments
    n = len(assignments)
    post_data = pd.DataFrame(
        {
            "user_id": assignments["unit_id"],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.10, size=n),
        }
    )
    results = experiment.analyze(post_data)
    rows = results.detailed_rows(results.context["control_name"])
    assert all(r["variance_reduction"] == "—" for r in rows)


def test_detailed_display_rows_have_readable_column_headers(tmp_path):
    experiment = design_simple_experiment(tmp_path)
    rng = np.random.default_rng(15)
    assignments = experiment.assignments
    n = len(assignments)
    post_data = pd.DataFrame(
        {
            "user_id": assignments["unit_id"],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.10, size=n),
        }
    )
    results = experiment.analyze(post_data)
    rows = results.detailed_display_rows(results.context["control_name"])
    assert rows
    # No "Designed" column (UX package, 5.1) — the designed method is
    # distinguished by bolding the row instead, driven by detailed_rows()'s
    # "designed" flag separately (see abkit/viz/report.py).
    expected_columns = {
        "Metric", "Comparison group", "Method", "Effect (abs.)",
        "Lift %", "95% CI of lift", "p-value", "p-value (adj.)", "Correction",
        "n (control)", "n (test)", "Variance reduction", "CUPED rho", "Verdict",
    }
    assert set(rows[0].keys()) == expected_columns


def test_analyze_compare_methods_adds_alternative_chains(tmp_path):
    experiment = design_simple_experiment(tmp_path)
    rng = np.random.default_rng(11)
    assignments = experiment.assignments
    n = len(assignments)
    post_data = pd.DataFrame(
        {
            "user_id": assignments["unit_id"],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.1, size=n),
        }
    )
    results = experiment.analyze(post_data, compare_methods=True)

    revenue_results = results["revenue"]
    methods_used = {r.method for r in revenue_results}
    assert "Welch t-test" in methods_used
    assert "Bootstrap (bca)" in methods_used
    assert "Mann-Whitney (Hodges-Lehmann)" in methods_used
    assert sum(1 for r in revenue_results if not r.is_designed_method) >= 4
    # item 3: binary тоже получает compare_methods-альтернативы (Chi-square,
    # Bootstrap percentile) — раньше их не было вообще, см.
    # test_analyze_compare_methods_binary_metric_gets_alternatives ниже для
    # полной проверки состава.
    clicks_results = results["clicks"]
    clicks_methods = {r.method for r in clicks_results}
    assert "Chi-square test" in clicks_methods
    assert "Bootstrap (percentile)" in clicks_methods
    assert sum(1 for r in clicks_results if not r.is_designed_method) >= 2
    # альтернативы не участвуют в поправке на множественность (влияющей на вердикт)
    designed = [r for r in revenue_results if r.is_designed_method]
    assert len(designed) == 1


def test_analyze_compare_methods_binary_metric_gets_alternatives(tmp_path):
    """Item 3: binary metric with a pre-period column + compare_methods=True
    gets the full binary compare-methods set (Z-test designed + Chi-square +
    Bootstrap percentile + CUPED+Welch), same treatment continuous metrics
    already had — Mann-Whitney/outlier methods are deliberately absent (see
    compare_methods_chains)."""
    rng = np.random.default_rng(3)
    n = 3000
    design_data = pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "converted": rng.binomial(1, 0.20, size=n),
            "converted_pre": rng.binomial(1, 0.20, size=n),
        }
    )
    config = DesignConfig(
        name="binary_compare_exp",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="converted", type="binary", pre_col="converted_pre")],
        sample_size=n,
        split_method="simple",
        seed=17,
    )
    experiment = Experiment.design(config, design_data, experiments_dir=tmp_path)
    assignments = experiment.assignments
    n_assigned = len(assignments)
    is_treat = (assignments["group"] == "treatment").to_numpy()
    rng2 = np.random.default_rng(4)
    base_rate = rng2.binomial(1, 0.20, size=n_assigned)
    base_rate[is_treat] = rng2.binomial(1, 0.26, size=int(is_treat.sum()))
    post_data = pd.DataFrame(
        {
            "user_id": list(assignments["unit_id"]),
            "converted": base_rate,
            "converted_pre": rng2.binomial(1, 0.20, size=n_assigned),
        }
    )

    results = experiment.analyze(post_data, correction="none", compare_methods=True)
    converted_results = results["converted"]
    methods_used = {r.method for r in converted_results}

    assert "Z-test of proportions" in methods_used
    assert "Chi-square test" in methods_used
    assert "Bootstrap (percentile)" in methods_used
    assert "CUPED + Welch t-test" in methods_used
    assert len(converted_results) >= 4

    designed = [r for r in converted_results if r.is_designed_method]
    assert len(designed) == 1
    assert designed[0].method == "Z-test of proportions"

    cuped_row = next(r for r in converted_results if r.method == "CUPED + Welch t-test")
    assert cuped_row.variance_reduction is not None
    assert cuped_row.cuped_rho is not None

    # Explicitly excluded (degenerate/inapplicable for a 0/1 series).
    assert not any("Mann-Whitney" in m for m in methods_used)
    assert not any("RemoveOutliers" in m or "Outliers" in m for m in methods_used)


def test_analyze_with_compare_methods_is_bit_for_bit_reproducible(tmp_path):
    """Bootstrap внутри compare_methods должен быть засеян от config.seed, иначе
    повторный analyze() на тех же данных не дает бит-в-бит тот же results.json
    (нефункциональное требование DESIGN.md, раздел 12)."""
    experiment = design_simple_experiment(tmp_path)
    rng = np.random.default_rng(12)
    assignments = experiment.assignments
    n = len(assignments)
    post_data = pd.DataFrame(
        {
            "user_id": assignments["unit_id"],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.1, size=n),
        }
    )

    results1 = experiment.analyze(post_data, compare_methods=True)
    results2 = experiment.analyze(post_data, compare_methods=True)

    assert results1.to_json() == results2.to_json()


def test_analyze_aa_false_positive_rate_in_expected_range(tmp_path):
    """Статистический смоук-тест: без реального эффекта FPR Welch t-test должен
    попадать в [3.5%, 6.5%] при alpha=0.05 и 2000 симуляциях (критерий готовности этапа 3)."""
    n = 2000
    experiment = design_simple_experiment(tmp_path, n=n, name="aa_smoke")
    assignments = experiment.assignments

    n_sims = 2000
    alpha = 0.05
    rng = np.random.default_rng(123)
    rejections = 0
    for _ in range(n_sims):
        sim_data = pd.DataFrame(
            {
                "user_id": assignments["unit_id"],
                "revenue": rng.normal(100, 20, size=n),
                "clicks": rng.binomial(1, 0.10, size=n),
            }
        )
        results = experiment.analyze(sim_data)
        p = results["revenue"][0].p_value
        if p < alpha:
            rejections += 1

    fpr = rejections / n_sims
    assert 0.035 <= fpr <= 0.065, f"Эмпирический FPR {fpr:.4f} вне ожидаемого диапазона"


def test_analyze_computes_per_dimension_and_combined_segments(tmp_path):
    """Item 3: with 2+ strata columns, segment_results_by_dimension carries
    one entry per INDIVIDUAL column (gender alone, country alone) plus their
    combination ("gender × country") — decomposed cheaply from the combined
    "stratum" column rather than a separate pass over the raw columns."""
    n = 2000
    rng = np.random.default_rng(11)
    design_data = pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "revenue": rng.normal(100, 20, size=n),
            "gender": ["M" if i % 2 == 0 else "F" for i in range(n)],
            "country": ["RU" if i % 4 < 2 else "KZ" for i in range(n)],
        }
    )
    config = DesignConfig(
        name="segment_dim_exp",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous", role="primary")],
        strata=["gender", "country"],
        sample_size=n,
        split_method="stratified",
        seed=7,
    )
    experiment = Experiment.design(config, design_data, experiments_dir=tmp_path)
    assignments = experiment.assignments

    post_data = pd.DataFrame(
        {"user_id": assignments["unit_id"], "revenue": rng.normal(100, 20, size=len(assignments))}
    )
    results = experiment.analyze(post_data)
    dims = results.context["segment_results_by_dimension"]

    assert set(dims.keys()) == {"gender", "country", "gender × country"}
    revenue_gender = dims["gender"]["revenue"]["treatment"]
    assert {s for s, _r in revenue_gender} == {"M", "F"}
    revenue_country = dims["country"]["revenue"]["treatment"]
    assert {s for s, _r in revenue_country} == {"KZ", "RU"}
    revenue_combined = dims["gender × country"]["revenue"]["treatment"]
    assert len(revenue_combined) == 4


def test_analyze_single_stratum_column_has_no_separate_combined_dimension(tmp_path):
    """Item 3: with just ONE strata column, "combined" and "individual" are
    the same thing — no separate " × "-joined entry, only the column's own
    name (mirrors compute_strata_power_rows' item 2 convention)."""
    n = 1000
    rng = np.random.default_rng(12)
    design_data = pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "revenue": rng.normal(100, 20, size=n),
            "platform": ["ios" if i % 2 == 0 else "android" for i in range(n)],
        }
    )
    config = DesignConfig(
        name="segment_single_dim_exp",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous", role="primary")],
        strata=["platform"],
        sample_size=n,
        split_method="stratified",
        seed=8,
    )
    experiment = Experiment.design(config, design_data, experiments_dir=tmp_path)
    assignments = experiment.assignments

    post_data = pd.DataFrame(
        {"user_id": assignments["unit_id"], "revenue": rng.normal(100, 20, size=len(assignments))}
    )
    results = experiment.analyze(post_data)
    dims = results.context["segment_results_by_dimension"]
    assert set(dims.keys()) == {"platform"}
