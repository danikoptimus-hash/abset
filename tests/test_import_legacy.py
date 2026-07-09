"""abkit/db/import_legacy.py — импорт файлового (легаси) реестра экспериментов
в серверный режим (DOCKER.md §9). Критерий готовности этапа D5: "импорт
реальной папки из текущей установки пользователя проходит"."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from abkit.config import DesignConfig, MetricConfig
from abkit.db.import_legacy import LegacyImportError, import_legacy_dir
from abkit.db.repositories import AssignmentRepo, ExperimentRepo, ResultRepo, UserRepo
from abkit.experiment import Experiment


@pytest.fixture
def db_env(db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path / "data"))
    yield


@pytest.fixture
def owner(db_env):
    UserRepo().create(email="owner@co.com", first_name="Owner", password_hash="x", role="admin")
    return "owner@co.com"


def _build_legacy_experiment(tmp_path, name="legacy_exp", n=300, seed=0, analyze=True):
    """Строит настоящий файловый (ABKIT_MODE=file, дефолт) эксперимент —
    design() (+ analyze()+report(), если analyze=True) — так же, как это делал
    бы пользователь до перехода на серверный режим."""
    legacy_dir = tmp_path / "legacy"
    rng = np.random.default_rng(seed)
    data = pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.1, size=n),
        }
    )
    config = DesignConfig(
        name=name,
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[
            MetricConfig(name="revenue", type="continuous"),
            MetricConfig(name="clicks", type="binary"),
        ],
        sample_size=n,
        split_method="simple",
        seed=1,
    )
    experiment = Experiment.design(config, data, experiments_dir=legacy_dir)
    if analyze:
        post_data = pd.DataFrame(
            {
                "user_id": experiment.assignments["unit_id"],
                "revenue": rng.normal(100, 20, size=n),
                "clicks": rng.binomial(1, 0.1, size=n),
            }
        )
        results = experiment.analyze(post_data)
        results.report()
    return legacy_dir


def test_import_unknown_owner_raises(db_env, tmp_path):
    legacy_dir = _build_legacy_experiment(tmp_path)
    with pytest.raises(LegacyImportError, match="not found"):
        import_legacy_dir(legacy_dir, "nobody@co.com")


def test_import_creates_experiment_with_correct_owner_and_status(owner, tmp_path):
    legacy_dir = _build_legacy_experiment(tmp_path, name="legacy_basic")

    report = import_legacy_dir(legacy_dir, owner)
    assert report.imported == ["legacy_basic"]
    assert report.skipped_existing == []
    assert report.failed == {}

    exp_row = ExperimentRepo().get_by_name("legacy_basic")
    assert exp_row is not None
    assert exp_row.status == "designed"
    owner_row = UserRepo().get_by_email(owner)
    assert exp_row.owner_id == owner_row.id


def test_import_copies_assignments_correctly(owner, tmp_path):
    legacy_dir = _build_legacy_experiment(tmp_path, name="legacy_assignments", n=250)

    import_legacy_dir(legacy_dir, owner)

    exp_row = ExperimentRepo().get_by_name("legacy_assignments")
    loaded = AssignmentRepo().load(exp_row.id)
    assert len(loaded) == 250
    assert set(loaded["group"].unique()) <= {"control", "treatment"}


def test_import_copies_analysis_results_and_report_file(owner, tmp_path):
    legacy_dir = _build_legacy_experiment(tmp_path, name="legacy_analyzed", analyze=True)

    import_legacy_dir(legacy_dir, owner)

    exp_row = ExperimentRepo().get_by_name("legacy_analyzed")
    result_row = ResultRepo().latest_for_experiment(exp_row.id)
    assert result_row is not None
    assert "results" in result_row.results
    assert Path(result_row.report_path).exists()
    assert Path(result_row.report_path).read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_import_without_analyze_has_no_result_row(owner, tmp_path):
    """Дизайн без анализа (results.json не создавался) — не должно быть строки
    в analysis_results, только сам эксперимент + assignments."""
    legacy_dir = _build_legacy_experiment(tmp_path, name="legacy_design_only", analyze=False)

    import_legacy_dir(legacy_dir, owner)

    exp_row = ExperimentRepo().get_by_name("legacy_design_only")
    assert exp_row is not None
    assert ResultRepo().latest_for_experiment(exp_row.id) is None


def test_import_is_idempotent(owner, tmp_path):
    legacy_dir = _build_legacy_experiment(tmp_path, name="legacy_idempotent")

    report1 = import_legacy_dir(legacy_dir, owner)
    assert report1.imported == ["legacy_idempotent"]

    report2 = import_legacy_dir(legacy_dir, owner)
    assert report2.imported == []
    assert report2.skipped_existing == ["legacy_idempotent"]

    # ровно одна строка эксперимента и ровно один комплект assignments — не задвоилось
    exp_row = ExperimentRepo().get_by_name("legacy_idempotent")
    assert AssignmentRepo().load(exp_row.id).shape[0] > 0


def test_import_multiple_experiments_in_one_registry(owner, tmp_path):
    """Одна общая registry.json, несколько экспериментов — все импортируются."""
    legacy_dir = tmp_path / "legacy"
    for i in range(3):
        rng = np.random.default_rng(i)
        n = 100
        # префикс юнитов разный на каждой итерации (u/v/w) — иначе изоляция по
        # умолчанию (mode="exclude") обнулила бы кандидатов 2-го и 3-го
        # экспериментов, т.к. увидела бы их unit_id уже занятыми 1-м.
        prefix = "uvw"[i]
        data = pd.DataFrame(
            {
                "user_id": [f"{prefix}{j}" for j in range(n)],
                "revenue": rng.normal(100, 20, size=n),
            }
        )
        config = DesignConfig(
            name=f"multi_exp_{i}",
            unit_col="user_id",
            groups={"control": 0.5, "treatment": 0.5},
            metrics=[MetricConfig(name="revenue", type="continuous")],
            sample_size=n,
            split_method="simple",
            seed=1,
        )
        Experiment.design(config, data, experiments_dir=legacy_dir)

    report = import_legacy_dir(legacy_dir, owner)
    assert set(report.imported) == {"multi_exp_0", "multi_exp_1", "multi_exp_2"}


def test_import_partial_failure_does_not_block_other_experiments(owner, tmp_path):
    """Один сломанный эксперимент (например удален assignments.parquet вручную)
    не должен прерывать импорт остальных."""
    legacy_dir = _build_legacy_experiment(tmp_path, name="legacy_good")

    broken_config = DesignConfig(
        name="legacy_broken",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous")],
        sample_size=50,
        split_method="simple",
        seed=1,
    )
    rng = np.random.default_rng(5)
    data = pd.DataFrame(
        {"user_id": [f"v{i}" for i in range(50)], "revenue": rng.normal(100, 20, size=50)}
    )
    Experiment.design(broken_config, data, experiments_dir=legacy_dir)
    (legacy_dir / "legacy_broken" / "assignments.parquet").unlink()

    report = import_legacy_dir(legacy_dir, owner)
    assert "legacy_good" in report.imported
    assert "legacy_broken" in report.failed
    assert ExperimentRepo().get_by_name("legacy_good") is not None
    assert ExperimentRepo().get_by_name("legacy_broken") is None
