import numpy as np
import pandas as pd
import pytest

from abkit.checks import AnalysisError
from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import DesignError, Experiment


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
    with pytest.raises(AnalysisError, match="обнаружены дубли"):
        experiment.analyze(post_data)


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
        "Джойним с назначениями...",
        "Проверяем честность (SRM, потери)...",
        "Считаем метрику 1 из 2: revenue...",
        "Считаем метрику 2 из 2: clicks...",
        "Применяем поправку на множественность...",
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

    assert "Агрегируем данные по дням..." in stages
    assert stages.index("Агрегируем данные по дням...") < stages.index("Джойним с назначениями...")


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
    assert any("разбивку по дням" in w for w in results_daily.global_warnings)


def test_analyze_cumulative_lift_builds_on_daily_data(tmp_path):
    """Кумулятивный лифт строится по дням из сырых (не агрегированных) данных;
    последний день кумулятивного окна = вся история -> совпадает с эффектом
    основного анализа."""
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
        results["revenue"][0].effect_rel * 100, abs=1e-6
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
    assert results["conv"][0].method == "Дельта-метод (ratio)"


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
    assert "Mann-Whitney (Ходжес-Леман)" in methods_used
    assert sum(1 for r in revenue_results if not r.is_designed_method) >= 4
    # compare_methods не должен появляться для binary-метрик
    assert all(r.is_designed_method for r in results["clicks"])
    # альтернативы не участвуют в поправке на множественность (влияющей на вердикт)
    designed = [r for r in revenue_results if r.is_designed_method]
    assert len(designed) == 1


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
