from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest

from abkit import storage
from abkit.config import DesignConfig, MetricConfig


def make_config(name="exp1"):
    return DesignConfig(
        name=name,
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous")],
        mde=0.05,
    )


def test_create_experiment_dir_creates_structure(tmp_path):
    path = storage.create_experiment_dir(tmp_path, "exp1")
    assert path == tmp_path / "exp1"
    assert path.is_dir()
    assert (path / "logs").is_dir()


def test_create_experiment_dir_collision_raises(tmp_path):
    storage.create_experiment_dir(tmp_path, "exp1")
    with pytest.raises(storage.StorageError, match="already exists"):
        storage.create_experiment_dir(tmp_path, "exp1")


def test_save_and_load_config_roundtrip(tmp_path):
    path = storage.create_experiment_dir(tmp_path, "exp1")
    config = make_config()
    storage.save_config(path, config)
    assert (path / "config.yaml").exists()

    loaded = storage.load_config(path)
    assert loaded == config


def test_load_config_missing_raises(tmp_path):
    path = tmp_path / "no_config"
    path.mkdir()
    with pytest.raises(storage.StorageError, match="config.yaml not found"):
        storage.load_config(path)


def test_save_and_load_assignments_roundtrip(tmp_path):
    path = storage.create_experiment_dir(tmp_path, "exp1")
    df = pd.DataFrame(
        {
            "unit_id": [1, 2, 3],
            "group": ["control", "treatment", "control"],
            "stratum": ["a", "a", "b"],
            "assigned_at": pd.Timestamp.now(),
        }
    )
    storage.save_assignments(path, df)
    loaded = storage.load_assignments(path)
    pd.testing.assert_frame_equal(loaded, df)


def test_load_assignments_missing_raises(tmp_path):
    path = tmp_path / "no_assignments"
    path.mkdir()
    with pytest.raises(storage.StorageError, match="assignments.parquet not found"):
        storage.load_assignments(path)


def test_register_experiment_and_read_registry(tmp_path):
    path = storage.experiment_path(tmp_path, "exp1")
    storage.register_experiment(tmp_path, "exp1", path)
    registry = storage.read_registry(tmp_path)
    assert "exp1" in registry
    assert registry["exp1"]["status"] == "designed"
    assert registry["exp1"]["path"] == str(path)
    assert registry["exp1"]["started_at"] is None


def test_register_experiment_duplicate_name_raises(tmp_path):
    path = storage.experiment_path(tmp_path, "exp1")
    storage.register_experiment(tmp_path, "exp1", path)
    with pytest.raises(storage.StorageError, match="already registered"):
        storage.register_experiment(tmp_path, "exp1", path)


def test_update_status_valid_transitions(tmp_path):
    path = storage.experiment_path(tmp_path, "exp1")
    storage.register_experiment(tmp_path, "exp1", path)

    storage.update_status(tmp_path, "exp1", "running")
    registry = storage.read_registry(tmp_path)
    assert registry["exp1"]["status"] == "running"
    assert registry["exp1"]["started_at"] is not None

    storage.update_status(tmp_path, "exp1", "completed")
    registry = storage.read_registry(tmp_path)
    assert registry["exp1"]["status"] == "completed"
    assert registry["exp1"]["completed_at"] is not None

    storage.update_status(tmp_path, "exp1", "archived")
    registry = storage.read_registry(tmp_path)
    assert registry["exp1"]["status"] == "archived"


def test_update_status_invalid_transition_raises(tmp_path):
    path = storage.experiment_path(tmp_path, "exp1")
    storage.register_experiment(tmp_path, "exp1", path)
    with pytest.raises(storage.StorageError, match="Invalid status transition"):
        storage.update_status(tmp_path, "exp1", "completed")


def test_update_status_unknown_status_raises(tmp_path):
    path = storage.experiment_path(tmp_path, "exp1")
    storage.register_experiment(tmp_path, "exp1", path)
    with pytest.raises(storage.StorageError, match="Unknown status"):
        storage.update_status(tmp_path, "exp1", "bogus")


def test_update_status_missing_experiment_raises(tmp_path):
    with pytest.raises(storage.StorageError, match="not found"):
        storage.update_status(tmp_path, "ghost", "running")


def test_list_experiments_active_only(tmp_path):
    for i, status in enumerate(["designed", "running", "completed", "archived"]):
        name = f"exp{i}"
        path = storage.experiment_path(tmp_path, name)
        storage.register_experiment(tmp_path, name, path)
        # продвигаем статус пошагово, если нужно
        order = ["designed", "running", "completed", "archived"]
        for step in order[1 : order.index(status) + 1]:
            storage.update_status(tmp_path, name, step)

    all_experiments = storage.list_experiments(tmp_path)
    assert len(all_experiments) == 4

    active = storage.list_experiments(tmp_path, active_only=True)
    assert set(active.keys()) == {"exp0", "exp1"}


def test_concurrent_registry_writes_all_succeed(tmp_path):
    """Много потоков одновременно регистрируют разные эксперименты —
    ни одна запись не должна потеряться (проверка атомарности + filelock)."""
    n = 25

    def register(i: int) -> None:
        name = f"exp_{i}"
        path = storage.experiment_path(tmp_path, name)
        storage.register_experiment(tmp_path, name, path)

    with ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(register, range(n)))

    registry = storage.read_registry(tmp_path)
    assert len(registry) == n
    for i in range(n):
        assert f"exp_{i}" in registry


def test_concurrent_status_updates_are_consistent(tmp_path):
    """Конкурентные обновления разных экспериментов не должны терять записи друг друга."""
    n = 15
    for i in range(n):
        name = f"exp_{i}"
        path = storage.experiment_path(tmp_path, name)
        storage.register_experiment(tmp_path, name, path)

    def promote(i: int) -> None:
        storage.update_status(tmp_path, f"exp_{i}", "running")

    with ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(promote, range(n)))

    registry = storage.read_registry(tmp_path)
    assert len(registry) == n
    for i in range(n):
        assert registry[f"exp_{i}"]["status"] == "running"


def test_get_experiments_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path / "custom"))
    result = storage.get_experiments_dir()
    assert result == (tmp_path / "custom").resolve()


def test_get_experiments_dir_from_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("ABKIT_EXPERIMENTS_DIR", raising=False)
    settings = {"experiments_dir": str(tmp_path / "from_settings")}
    result = storage.get_experiments_dir(settings)
    assert result == (tmp_path / "from_settings").resolve()
