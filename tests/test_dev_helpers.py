"""abkit/dev_helpers.py — CLAUDE.md, "Правило: гигиена dev-артефактов" (б):
manual debugging entity creation must force the _dev_ prefix and track for
teardown, so it can't be forgotten the way a bare convention could."""

from __future__ import annotations

import uuid

import numpy as np
import pandas as pd
import pytest

from abkit.auth.guards import CurrentUser
from abkit.config import DesignConfig, MetricConfig
from abkit.dev_helpers import DevSession, ensure_dev_prefix


@pytest.fixture
def db_env(db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_MODE", "db")
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path / "file_side"))
    yield


def _admin(db_env):
    from abkit.db.repositories import UserRepo

    user_id = UserRepo().create(email="dev_helper_admin@co.com", first_name="A", password_hash="h", role="admin")
    return CurrentUser(id=str(user_id), email="dev_helper_admin@co.com", name="A", role="admin")


def _design_data(n=100, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"user_id": [f"u{i}" for i in range(n)], "revenue": rng.normal(100, 20, size=n)})


def _config(name):
    return DesignConfig(
        name=name, unit_col="user_id", groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous")], sample_size=100,
        split_method="simple", seed=1,
    )


@pytest.mark.parametrize(
    "raw,expected",
    [("probe", "_dev_probe"), ("_dev_already", "_dev_already"), ("_dev_", "_dev_")],
)
def test_ensure_dev_prefix(raw, expected):
    assert ensure_dev_prefix(raw) == expected


def test_design_forces_prefix_and_teardown_removes_it(db_env):
    from abkit.db.repositories import ExperimentRepo

    admin = _admin(db_env)
    with DevSession() as dev:
        dev.design(admin, _config("no_prefix_probe"), _design_data(seed=1))
        assert ExperimentRepo().get_by_name("_dev_no_prefix_probe") is not None
        assert ExperimentRepo().get_by_name("no_prefix_probe") is None

    assert ExperimentRepo().get_by_name("_dev_no_prefix_probe") is None


def test_design_with_already_prefixed_name_not_double_prefixed(db_env):
    from abkit.db.repositories import ExperimentRepo

    admin = _admin(db_env)
    with DevSession() as dev:
        dev.design(admin, _config("_dev_manual"), _design_data(seed=2))
        assert ExperimentRepo().get_by_name("_dev_manual") is not None
        assert ExperimentRepo().get_by_name("_dev__dev_manual") is None


def test_dataset_forces_prefix_and_unlinks_file_on_teardown(db_env, tmp_path):
    from abkit.db.repositories import DatasetRepo

    admin = _admin(db_env)
    csv_path = tmp_path / "raw.csv"
    csv_path.write_text("a,b\n1,2\n", encoding="utf-8")

    dev = DevSession()
    dataset_id = dev.dataset(
        filename="probe.csv", kind="pre_design", n_rows=1, columns=["a", "b"],
        storage_path=str(csv_path), sha256="deadbeef", uploaded_by=uuid.UUID(admin.id),
    )
    ds = DatasetRepo().get_by_id(dataset_id)
    assert ds.filename == "_dev_probe.csv"
    assert csv_path.exists()

    removed = dev.teardown()
    assert removed["datasets"] == 1
    assert DatasetRepo().get_by_id(dataset_id) is None
    assert not csv_path.exists()


def test_teardown_is_idempotent(db_env):
    admin = _admin(db_env)
    dev = DevSession()
    dev.design(admin, _config("idempotent_probe"), _design_data(seed=3))

    first = dev.teardown()
    assert first["experiments"] == 1
    second = dev.teardown()
    assert second["experiments"] == 0  # nothing left tracked, no error re-deleting


def test_tag_forces_prefix_and_teardown_removes_it(db_env):
    """Item A3 (DB bloat package) — tags didn't exist yet when DevSession
    was first written; closing that gap."""
    from abkit.db.repositories import TagRepo

    admin = _admin(db_env)
    dev = DevSession()
    tag = dev.tag(admin, "probe")
    assert tag.name == "_dev_probe"
    assert TagRepo().get_by_id(tag.id) is not None

    removed = dev.teardown()
    assert removed["tags"] == 1
    assert TagRepo().get_by_id(tag.id) is None


def test_folder_forces_prefix_and_teardown_removes_it(db_env):
    from abkit.db.repositories import FolderRepo

    admin = _admin(db_env)
    dev = DevSession()
    folder = dev.folder(admin, "probe")
    assert folder.name == "_dev_probe"
    assert FolderRepo().get_by_id(folder.id) is not None

    removed = dev.teardown()
    assert removed["folders"] == 1
    assert FolderRepo().get_by_id(folder.id) is None
