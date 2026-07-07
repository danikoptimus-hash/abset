import numpy as np
import pandas as pd
import pytest

from abkit import storage
from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import DesignError, Experiment


def make_synthetic_data(n=10_000, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "platform": rng.choice(["ios", "android"], size=n),
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.1, size=n),
            "sessions": rng.integers(1, 10, size=n),
            "orders": rng.integers(0, 5, size=n),
        }
    )


def make_config(**overrides):
    defaults = dict(
        name="exp_design_test",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[
            MetricConfig(name="revenue", type="continuous"),
            MetricConfig(name="clicks", type="binary"),
        ],
        strata=["platform"],
        mde=0.1,
        split_method="stratified",
        seed=42,
    )
    defaults.update(overrides)
    return DesignConfig(**defaults)


def test_design_end_to_end_creates_experiment(tmp_path):
    data = make_synthetic_data()
    config = make_config()

    experiment = Experiment.design(config, data, experiments_dir=tmp_path)

    assert experiment.assignments is not None
    assert len(experiment.assignments) == len(data)
    assert set(experiment.assignments["group"].unique()) == {"control", "treatment"}
    assert experiment.report is not None
    assert experiment.report.n_available == len(data)

    # реестр обновлен
    registry = storage.read_registry(tmp_path)
    assert registry["exp_design_test"]["status"] == "designed"

    # файлы на диске
    assert (tmp_path / "exp_design_test" / "config.yaml").exists()
    assert (tmp_path / "exp_design_test" / "assignments.parquet").exists()


def test_design_progress_callback_reports_all_stages_in_order(tmp_path):
    """UI (app.py) показывает прогресс через st.status по этапам design(); нужна
    гарантия, что callback реально вызывается на каждом этапе и в правильном
    порядке — иначе пользователь снова увидит "зависший" интерфейс."""
    data = make_synthetic_data()
    config = make_config()
    stages: list[str] = []

    Experiment.design(config, data, experiments_dir=tmp_path, progress_callback=stages.append)

    assert stages == [
        "Валидируем данные...",
        "Проверяем изоляцию от других экспериментов...",
        "Считаем мощность...",
        "Строим страты...",
        "Разбиваем на группы (сплит)...",
        "Проверяем честность (SRM, баланс страт, pre-period A/A)...",
        "Сохраняем эксперимент...",
    ]


def test_design_without_progress_callback_still_works(tmp_path):
    data = make_synthetic_data()
    config = make_config()
    experiment = Experiment.design(config, data, experiments_dir=tmp_path)
    assert experiment.assignments is not None


def test_design_writes_per_group_csv_samples(tmp_path):
    data = make_synthetic_data(n=5_000)
    config = make_config()
    experiment = Experiment.design(config, data, experiments_dir=tmp_path)

    samples_dir = experiment.path / "samples"
    control_csv = samples_dir / "control.csv"
    treatment_csv = samples_dir / "treatment.csv"
    assert control_csv.exists()
    assert treatment_csv.exists()

    control_df = pd.read_csv(control_csv)
    treatment_df = pd.read_csv(treatment_csv)

    assert list(control_df.columns) == ["unit_id", "stratum", "assigned_at"]
    assert list(treatment_df.columns) == ["unit_id", "stratum", "assigned_at"]

    group_sizes = experiment.assignments["group"].value_counts()
    assert len(control_df) == group_sizes["control"]
    assert len(treatment_df) == group_sizes["treatment"]

    # CSV без BOM, разделитель - запятая
    raw = control_csv.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.split(b"\n", 1)[0] == b"unit_id,stratum,assigned_at"

    # объединение всех CSV по unit_id совпадает с assignments.parquet
    combined_ids = set(control_df["unit_id"]) | set(treatment_df["unit_id"])
    assert combined_ids == set(experiment.assignments["unit_id"])
    assert len(control_df) + len(treatment_df) == len(experiment.assignments)


def test_design_group_sizes_close_to_expected(tmp_path):
    data = make_synthetic_data(n=20_000)
    config = make_config()
    experiment = Experiment.design(config, data, experiments_dir=tmp_path)

    counts = experiment.assignments["group"].value_counts()
    assert abs(counts["control"] - 10_000) < 50
    assert abs(counts["treatment"] - 10_000) < 50


def test_design_srm_passes_on_honest_split(tmp_path):
    data = make_synthetic_data()
    config = make_config()
    experiment = Experiment.design(config, data, experiments_dir=tmp_path)
    assert experiment.report.srm.passed


def test_design_power_results_present_for_all_metrics(tmp_path):
    data = make_synthetic_data()
    config = make_config()
    experiment = Experiment.design(config, data, experiments_dir=tmp_path)

    assert set(experiment.report.power_results.keys()) == {"revenue", "clicks"}
    revenue_power = experiment.report.power_results["revenue"]
    assert revenue_power.sample_size_per_group is not None
    assert revenue_power.mde_rel == pytest.approx(0.1)


def test_design_reload_via_load_matches(tmp_path):
    data = make_synthetic_data()
    config = make_config()
    Experiment.design(config, data, experiments_dir=tmp_path)

    reloaded = Experiment.load("exp_design_test", experiments_dir=tmp_path)
    assert reloaded.config.name == "exp_design_test"
    assert len(reloaded.assignments) == len(data)
    assert reloaded.config.seed == 42
    assert reloaded.config.computed is not None
    assert "power" in reloaded.config.computed


def test_design_rejects_duplicate_unit_col(tmp_path):
    data = make_synthetic_data()
    data = pd.concat([data, data.iloc[[0]]], ignore_index=True)
    config = make_config()
    with pytest.raises(DesignError, match="дубликаты"):
        Experiment.design(config, data, experiments_dir=tmp_path)


def test_design_rejects_missing_metric_column(tmp_path):
    data = make_synthetic_data().drop(columns=["revenue"])
    config = make_config()
    with pytest.raises(DesignError, match="revenue"):
        Experiment.design(config, data, experiments_dir=tmp_path)


def test_design_respects_isolation_from_other_experiment(tmp_path):
    other_data = make_synthetic_data(n=100, seed=1)
    other_config = make_config(name="other_exp", strata=[], metrics=[MetricConfig(name="revenue", type="continuous")])
    other_experiment = Experiment.design(other_config, other_data, experiments_dir=tmp_path)
    occupied_ids = list(other_experiment.assignments["unit_id"])[:20]

    data = make_synthetic_data(n=10_000, seed=0)
    data["user_id"] = [f"main{i}" for i in range(len(data))]
    # гарантируем реальное пересечение: подменяем 20 юзеров новых данных на занятые id
    data.loc[:19, "user_id"] = occupied_ids

    config = make_config(name="new_exp")
    experiment = Experiment.design(config, data, experiments_dir=tmp_path)

    assert experiment.report.n_excluded_by_isolation == 20
    assert experiment.report.excluded_by_experiment == {"other_exp": 20}
    assert set(experiment.assignments["unit_id"]).isdisjoint(set(occupied_ids))


def test_design_hash_split_deterministic_across_runs(tmp_path):
    data = make_synthetic_data()
    config = make_config(
        split_method="hash", name="hash_exp_1", hash_salt="fixed-salt", strata=[], isolation="off"
    )
    experiment1 = Experiment.design(config, data, experiments_dir=tmp_path)

    config2 = make_config(
        split_method="hash", name="hash_exp_2", hash_salt="fixed-salt", strata=[], isolation="off"
    )
    experiment2 = Experiment.design(config2, data, experiments_dir=tmp_path)

    merged = experiment1.assignments.merge(
        experiment2.assignments, on="unit_id", suffixes=("_1", "_2")
    )
    assert (merged["group_1"] == merged["group_2"]).all()


def test_design_uses_all_available_when_no_mde_or_sample_size(tmp_path):
    data = make_synthetic_data()
    config = make_config(mde=None, sample_size=None, name="no_size_exp")
    experiment = Experiment.design(config, data, experiments_dir=tmp_path)
    revenue_power = experiment.report.power_results["revenue"]
    assert revenue_power.mde_abs is not None
    assert revenue_power.sample_size_per_group == pytest.approx(len(data) * 0.5)


def test_design_with_ratio_metric(tmp_path):
    data = make_synthetic_data()
    config = make_config(
        name="ratio_exp",
        metrics=[MetricConfig(name="conv_rate", type="ratio", num="orders", den="sessions")],
        strata=[],
    )
    experiment = Experiment.design(config, data, experiments_dir=tmp_path)
    result = experiment.report.power_results["conv_rate"]
    assert result.baseline_std is not None and result.baseline_std > 0


def test_design_with_cuped_pre_col(tmp_path):
    rng = np.random.default_rng(0)
    n = 5000
    pre = rng.normal(100, 20, size=n)
    revenue = pre * 0.8 + rng.normal(0, 10, size=n)
    data = pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "revenue": revenue,
            "revenue_pre": pre,
        }
    )
    config = make_config(
        name="cuped_exp",
        metrics=[MetricConfig(name="revenue", type="continuous", pre_col="revenue_pre")],
        strata=[],
    )
    experiment = Experiment.design(config, data, experiments_dir=tmp_path)
    result = experiment.report.power_results["revenue"]
    assert result.rho is not None
    assert result.rho > 0.5
    assert result.sample_size_per_group_cuped < result.sample_size_per_group


def make_data_with_strata_nan(n=2000, n_nan=150, seed=0):
    data = make_synthetic_data(n=n, seed=seed)
    rng = np.random.default_rng(seed + 1)
    nan_idx = rng.choice(n, size=n_nan, replace=False)
    data.loc[nan_idx, "platform"] = None
    return data


def test_design_default_nan_strategy_does_not_raise_on_strata_nan(tmp_path):
    """Регрессия: раньше пропуски в стратификационной колонке валили дизайн с
    ошибкой 'Колонка страты содержит пропуски' — теперь дефолтная стратегия
    (separate_stratum) должна это спокойно обрабатывать."""
    data = make_data_with_strata_nan()
    config = make_config(name="nan_default", isolation="off")

    experiment = Experiment.design(config, data, experiments_dir=tmp_path)

    assert experiment.report.strata_nan_counts == {"platform": 150}
    assert experiment.report.n_dropped_for_nan_strata == 0
    assert any("150 пропусков" in w for w in experiment.report.warnings)


def test_design_nan_users_end_up_in_unknown_stratum(tmp_path):
    data = make_data_with_strata_nan()
    config = make_config(name="nan_unknown_stratum", isolation="off")

    experiment = Experiment.design(config, data, experiments_dir=tmp_path)

    nan_user_ids = set(data.loc[data["platform"].isna(), "user_id"])
    assignments = experiment.assignments
    nan_assignments = assignments[assignments["unit_id"].isin(nan_user_ids)]
    assert len(nan_assignments) == 150
    assert (nan_assignments["stratum"].str.contains("unknown")).all()


def test_design_nan_strategy_drop_removes_users_with_missing_strata(tmp_path):
    data = make_data_with_strata_nan()
    config = make_config(name="nan_drop", isolation="off", nan_strategy="drop")

    experiment = Experiment.design(config, data, experiments_dir=tmp_path)

    assert experiment.report.n_dropped_for_nan_strata == 150
    assert experiment.report.n_available == len(data) - 150
    assert len(experiment.assignments) == len(data) - 150
    nan_user_ids = set(data.loc[data["platform"].isna(), "user_id"])
    assert nan_user_ids.isdisjoint(set(experiment.assignments["unit_id"]))
    assert any("удалены из кандидатов" in w for w in experiment.report.warnings)


def test_design_nan_strategy_error_raises_with_clear_message(tmp_path):
    data = make_data_with_strata_nan()
    config = make_config(name="nan_error", isolation="off", nan_strategy="error")

    with pytest.raises(DesignError, match="пропуски"):
        Experiment.design(config, data, experiments_dir=tmp_path)


def test_design_high_nan_fraction_triggers_attention_warning(tmp_path):
    """При доле пропусков > 5% должно появиться отдельное предупреждение с
    формулировкой 'Проверьте качество данных'."""
    data = make_data_with_strata_nan(n=2000, n_nan=150)  # 7.5%
    config = make_config(name="nan_high_fraction", isolation="off")

    experiment = Experiment.design(config, data, experiments_dir=tmp_path)

    assert any("Проверьте качество данных" in w for w in experiment.report.warnings)


def test_design_low_nan_fraction_no_attention_warning(tmp_path):
    data = make_data_with_strata_nan(n=2000, n_nan=20)  # 1%
    config = make_config(name="nan_low_fraction", isolation="off")

    experiment = Experiment.design(config, data, experiments_dir=tmp_path)

    assert not any("Проверьте качество данных" in w for w in experiment.report.warnings)
