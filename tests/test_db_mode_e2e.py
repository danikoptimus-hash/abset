"""Design -> Analyze end-to-end в серверном режиме (ABKIT_MODE=db) — критерий
готовности этапа D1 (DOCKER.md, раздел 12): "design->analyze проходит
end-to-end в режиме db". Использует реальный Postgres (testcontainers/CI)."""

import numpy as np
import pandas as pd
import pytest

from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import Experiment


@pytest.fixture
def db_mode(db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_MODE", "db")
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    yield


def _design_data(n=2000, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.10, size=n),
        }
    )


def _config(name, n):
    return DesignConfig(
        name=name,
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[
            MetricConfig(name="revenue", type="continuous"),
            MetricConfig(name="clicks", type="binary"),
        ],
        sample_size=n,
        split_method="simple",
        seed=42,
    )


def test_design_and_analyze_end_to_end_in_db_mode(db_mode):
    data = _design_data()
    experiment = Experiment.design(_config("db_e2e_exp", len(data)), data)

    assert experiment.assignments is not None
    assert len(experiment.assignments) == len(data)
    assert set(experiment.assignments["group"].unique()) == {"control", "treatment"}
    assert experiment.report is not None

    # Перезагрузка "с нуля" -- как это делает новый процесс app.py/cli.py
    loaded = Experiment.load("db_e2e_exp")
    assert loaded.config.name == "db_e2e_exp"
    assert loaded.config.unit_col == "user_id"
    assert len(loaded.assignments) == len(data)

    rng = np.random.default_rng(5)
    n = len(loaded.assignments)
    post_data = pd.DataFrame(
        {
            "user_id": loaded.assignments["unit_id"],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.10, size=n),
        }
    )

    results = loaded.analyze(post_data)
    assert "revenue" in results.metrics
    assert "clicks" in results.metrics
    assert results["revenue"][0].method == "Welch t-test"

    report_path = results.report()
    assert report_path.exists()
    assert (loaded.path / "results.json").exists()


def test_design_detects_injected_effect_in_db_mode(db_mode):
    """Смоук на реальную статистику (не заглушка): инжектированный эффект
    должен детектироваться так же, как в файловом режиме."""
    data = _design_data(seed=1)
    experiment = Experiment.design(_config("db_effect_exp", len(data)), data)

    assignments = experiment.assignments
    rng = np.random.default_rng(9)
    n = len(assignments)
    revenue = rng.normal(100, 20, size=n)
    is_treatment = (assignments["group"] == "treatment").to_numpy()
    revenue[is_treatment] += 15
    post_data = pd.DataFrame(
        {
            "user_id": assignments["unit_id"],
            "revenue": revenue,
            "clicks": rng.binomial(1, 0.10, size=n),
        }
    )

    results = experiment.analyze(post_data)
    revenue_result = results["revenue"][0]
    assert revenue_result.effect_abs > 0
    assert revenue_result.p_value < 0.01
    assert results.verdict("revenue") == "significant_positive"


def test_db_mode_split_matches_file_mode_given_same_seed(tmp_path, monkeypatch, db_mode):
    """Один и тот же seed/данные -> идентичный сплит независимо от бэкенда
    хранения (db vs file) — доказывает, что смена стораджа не затронула
    статистическую логику сплитования."""
    data = _design_data(seed=7)

    db_experiment = Experiment.design(_config("db_vs_file_db", len(data)), data)

    monkeypatch.delenv("ABKIT_MODE", raising=False)
    file_dir = tmp_path / "file_experiments"
    file_experiment = Experiment.design(
        _config("db_vs_file_file", len(data)), data, experiments_dir=file_dir
    )

    db_assignments = db_experiment.assignments.sort_values("unit_id").reset_index(drop=True)
    file_assignments = file_experiment.assignments.sort_values("unit_id").reset_index(drop=True)
    assert list(db_assignments["group"]) == list(file_assignments["group"])
