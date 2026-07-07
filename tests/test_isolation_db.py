"""abkit/design/isolation.py: db-режим (store=DbExperimentStore) — DOCKER.md §5
(изоляция одним SQL-запросом вместо чтения assignments.parquet). Логика
пересечения множеств в apply_isolation не менялась — тестируем только новый
путь получения occupied units."""

import pandas as pd
import pytest

from abkit.design.isolation import apply_isolation


@pytest.fixture
def db_store(db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path))
    from abkit.db.store import DbExperimentStore

    return DbExperimentStore()


def _design(store, name, unit_ids, status="running"):
    from abkit.config import DesignConfig, MetricConfig

    config = DesignConfig(
        name=name,
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous")],
        sample_size=len(unit_ids),
        seed=1,
    )
    n = len(unit_ids)
    groups_cycle = (["control", "treatment"] * (n // 2 + 1))[:n]
    assignments = pd.DataFrame(
        {
            "unit_id": unit_ids,
            "group": groups_cycle,
            "stratum": None,
            "assigned_at": pd.Timestamp.now(tz="UTC"),
        }
    )
    store.create_experiment(config, assignments)
    store.experiments.update_status(name, status)


def test_apply_isolation_db_mode_excludes_units_from_active_experiments(db_store):
    _design(db_store, "other_exp", ["u1", "u2", "u3"], status="running")

    candidates = pd.DataFrame({"user_id": ["u2", "u3", "u4", "u5"]})
    result = apply_isolation(
        data=candidates,
        unit_col="user_id",
        experiments_dir=None,
        mode="exclude",
        current_experiment_name="new_exp",
        store=db_store,
    )

    assert result.n_before == 4
    assert set(result.candidates["user_id"]) == {"u4", "u5"}
    assert result.excluded_by_experiment == {"other_exp": 2}


def test_apply_isolation_db_mode_ignores_archived_experiments(db_store):
    _design(db_store, "archived_exp", ["u1", "u2"], status="archived")

    candidates = pd.DataFrame({"user_id": ["u1", "u2", "u3"]})
    result = apply_isolation(
        data=candidates, unit_col="user_id", experiments_dir=None, mode="exclude", store=db_store
    )

    assert result.n_excluded == 0
    assert set(result.candidates["user_id"]) == {"u1", "u2", "u3"}


def test_apply_isolation_db_mode_off_skips_query_entirely(db_store):
    candidates = pd.DataFrame({"user_id": ["u1", "u2"]})
    result = apply_isolation(
        data=candidates, unit_col="user_id", experiments_dir=None, mode="off", store=db_store
    )
    assert result.n_excluded == 0
    assert len(result.candidates) == 2


def test_apply_isolation_without_store_uses_file_mode_unchanged(tmp_path):
    """store=None (дефолт) — поведение должно быть byte-for-byte как раньше."""
    candidates = pd.DataFrame({"user_id": ["u1", "u2"]})
    result = apply_isolation(
        data=candidates, unit_col="user_id", experiments_dir=tmp_path, mode="exclude"
    )
    assert result.n_excluded == 0
    assert len(result.candidates) == 2
