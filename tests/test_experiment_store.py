"""abkit/experiment_store.py: FileExperimentStore (без БД) + DbExperimentStore
(Postgres, требует db_url) + фабрика get_experiment_store()."""

import numpy as np
import pandas as pd
import pytest

from abkit.config import DesignConfig, MetricConfig
from abkit.experiment_store import FileExperimentStore, get_experiment_store


def _config(name="fs_exp"):
    return DesignConfig(
        name=name,
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous")],
        sample_size=10,
        seed=1,
    )


def _assignments(n=20, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "unit_id": [f"u{i}" for i in range(n)],
            "group": rng.choice(["control", "treatment"], size=n),
            "stratum": "s",
            "assigned_at": pd.Timestamp.now(tz="UTC"),
        }
    )


def test_file_experiment_store_roundtrip(tmp_path):
    config = _config()
    assignments = _assignments()
    store = FileExperimentStore(tmp_path)

    handle = store.create_experiment(config, assignments)
    assert handle.path.exists()
    assert (handle.path / "config.yaml").exists()
    assert (handle.path / "assignments.parquet").exists()
    assert (handle.path / "samples" / "control.csv").exists()
    assert (handle.path / "samples" / "treatment.csv").exists()

    loaded = store.load_experiment("fs_exp")
    assert loaded.config.name == "fs_exp"
    assert loaded.assignments is not None
    assert set(loaded.assignments["unit_id"]) == set(assignments["unit_id"])
    assert set(loaded.assignments["group"].unique()) <= {"control", "treatment"}


def test_file_experiment_store_load_missing_raises(tmp_path):
    from abkit import storage

    store = FileExperimentStore(tmp_path)
    with pytest.raises(storage.StorageError):
        store.load_experiment("does_not_exist")


def test_get_experiment_store_defaults_to_file(tmp_path, monkeypatch):
    monkeypatch.delenv("ABKIT_MODE", raising=False)
    store = get_experiment_store(tmp_path)
    assert isinstance(store, FileExperimentStore)


def test_get_experiment_store_db_mode_returns_db_store(monkeypatch):
    from abkit.db.store import DbExperimentStore

    monkeypatch.setenv("ABKIT_MODE", "db")
    try:
        store = get_experiment_store()
        assert isinstance(store, DbExperimentStore)
    finally:
        monkeypatch.delenv("ABKIT_MODE", raising=False)


def test_db_experiment_store_roundtrip(db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    from abkit.db.store import DbExperimentStore

    config = _config(name="db_exp")
    assignments = _assignments()
    store = DbExperimentStore()

    handle = store.create_experiment(config, assignments)
    assert handle.path == (tmp_path / "db_exp").resolve()
    assert handle.path.exists()

    loaded = store.load_experiment("db_exp")
    assert loaded.config.name == "db_exp"
    assert loaded.config.unit_col == "user_id"
    assert set(loaded.assignments["unit_id"]) == set(assignments["unit_id"])
    assert set(loaded.assignments["group"].unique()) <= {"control", "treatment"}


def test_db_experiment_store_load_missing_raises(db_url, tmp_path, monkeypatch):
    from abkit import storage
    from abkit.db.store import DbExperimentStore

    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    store = DbExperimentStore()
    with pytest.raises(storage.StorageError):
        store.load_experiment("does_not_exist")


def test_db_experiment_store_duplicate_name_raises(db_url, tmp_path, monkeypatch):
    from abkit import storage
    from abkit.db.store import DbExperimentStore

    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    store = DbExperimentStore()
    store.create_experiment(_config(name="dup"), _assignments())
    with pytest.raises(storage.StorageError):
        store.create_experiment(_config(name="dup"), _assignments())


def test_db_experiment_store_bootstraps_system_user_when_owner_not_given(db_url, tmp_path, monkeypatch):
    from abkit.db.repositories import UserRepo
    from abkit.db.store import DbExperimentStore, _SYSTEM_USER_EMAIL

    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    store = DbExperimentStore()
    store.create_experiment(_config(name="sys_owner_exp"), _assignments())

    system_user = UserRepo().get_by_email(_SYSTEM_USER_EMAIL)
    assert system_user is not None
    exp = store.experiments.get_by_name("sys_owner_exp")
    assert exp.owner_id == system_user.id
