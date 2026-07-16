"""abkit-admin cleanup-dev (CLAUDE.md, "Правило: гигиена dev-артефактов") —
abkit.jobs.run_cleanup_dev directly, same no-HTTP-context pattern as
tests/test_jobs_permission_matrix.py (this is a trusted CLI-only sweep, not
gated by CurrentUser/require_role)."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from abkit import jobs
from abkit.auth.guards import CurrentUser
from abkit.config import DesignConfig, MetricConfig


@pytest.fixture
def db_env(db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_MODE", "db")
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path / "file_side"))
    yield


def _make_user(email: str, role: str = "editor"):
    from abkit.db.repositories import UserRepo

    user_id = UserRepo().create(email=email, first_name="U", password_hash="h", role=role)
    return CurrentUser(id=str(user_id), email=email, name="U", role=role)


def _design_data(n=200, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"user_id": [f"u{i}" for i in range(n)], "revenue": rng.normal(100, 20, size=n)})


def _config(name):
    return DesignConfig(
        name=name, unit_col="user_id", groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous")], sample_size=200,
        split_method="simple", seed=1,
    )


def _backdate_created_at(model_cls, name_col, name_val, hours_ago: float) -> None:
    """Test-only: run_cleanup_dev's age guard needs rows with a controlled
    age, and no repo exposes created_at as a create() param — reach into the
    session directly instead of adding a param no real caller would ever use."""
    from datetime import datetime, timedelta, timezone

    from abkit.db.engine import session_scope

    with session_scope() as s:
        row = s.query(model_cls).filter(getattr(model_cls, name_col) == name_val).one()
        row.created_at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)


def test_dry_run_lists_without_deleting(db_env):
    from abkit.db.repositories import ExperimentRepo

    admin = _make_user("admin@co.com", "admin")
    jobs.run_design(admin, _config("_dev_probe"), _design_data(seed=1))

    result = jobs.run_cleanup_dev(dry_run=True)
    assert "_dev_probe" in result["experiments"]
    assert ExperimentRepo().get_by_name("_dev_probe") is not None


def test_dev_prefixed_experiment_deleted_regardless_of_age(db_env):
    from abkit.db.models import Experiment
    from abkit.db.repositories import ExperimentRepo

    admin = _make_user("admin2@co.com", "admin")
    jobs.run_design(admin, _config("_dev_old_probe"), _design_data(seed=2))
    _backdate_created_at(Experiment, "name", "_dev_old_probe", hours_ago=999)

    result = jobs.run_cleanup_dev(dry_run=False, min_age_hours=1)
    assert "_dev_old_probe" in result["experiments"]
    assert ExperimentRepo().get_by_name("_dev_old_probe") is None


def test_e2e_test_owned_experiment_protected_when_fresh(db_env):
    from abkit.db.repositories import ExperimentRepo

    e2e_user = _make_user("probe@e2e.test", "editor")
    jobs.run_design(e2e_user, _config("fresh_e2e_probe"), _design_data(seed=3))

    result = jobs.run_cleanup_dev(dry_run=False, min_age_hours=1)
    assert "fresh_e2e_probe" not in result["experiments"]
    assert ExperimentRepo().get_by_name("fresh_e2e_probe") is not None


def test_e2e_test_owned_experiment_swept_once_old_enough(db_env):
    from abkit.db.models import Experiment
    from abkit.db.repositories import ExperimentRepo

    e2e_user = _make_user("probe2@e2e.test", "editor")
    jobs.run_design(e2e_user, _config("stale_e2e_probe"), _design_data(seed=4))
    _backdate_created_at(Experiment, "name", "stale_e2e_probe", hours_ago=2)

    result = jobs.run_cleanup_dev(dry_run=False, min_age_hours=1)
    assert "stale_e2e_probe" in result["experiments"]
    assert ExperimentRepo().get_by_name("stale_e2e_probe") is None


def test_real_user_experiment_never_touched(db_env):
    from abkit.db.models import Experiment
    from abkit.db.repositories import ExperimentRepo

    real_user = _make_user("real.analyst@company.com", "editor")
    jobs.run_design(real_user, _config("quarterly_pricing_test"), _design_data(seed=5))
    _backdate_created_at(Experiment, "name", "quarterly_pricing_test", hours_ago=999)

    result = jobs.run_cleanup_dev(dry_run=False, min_age_hours=1)
    assert "quarterly_pricing_test" not in result["experiments"]
    assert ExperimentRepo().get_by_name("quarterly_pricing_test") is not None


def test_e2e_fixture_accounts_never_deactivated_but_their_experiments_are_swept(db_env):
    from abkit.db.models import Experiment, User
    from abkit.db.repositories import ExperimentRepo, UserRepo

    fixture = _make_user("admin@e2e.test", "admin")
    jobs.run_design(fixture, _config("fixture_owned_probe"), _design_data(seed=6))
    _backdate_created_at(Experiment, "name", "fixture_owned_probe", hours_ago=2)
    _backdate_created_at(User, "email", "admin@e2e.test", hours_ago=2)

    result = jobs.run_cleanup_dev(dry_run=False, min_age_hours=1)
    assert "fixture_owned_probe" in result["experiments"]
    assert ExperimentRepo().get_by_name("fixture_owned_probe") is None
    assert "admin@e2e.test" not in result["users_deactivated"]
    assert UserRepo().get_by_email("admin@e2e.test").is_active is True


def test_stale_e2e_user_account_deactivated_not_deleted(db_env):
    from abkit.db.models import User
    from abkit.db.repositories import UserRepo

    _make_user("stale_probe@e2e.test", "viewer")
    _backdate_created_at(User, "email", "stale_probe@e2e.test", hours_ago=2)

    result = jobs.run_cleanup_dev(dry_run=False, min_age_hours=1)
    assert "stale_probe@e2e.test" in result["users_deactivated"]
    u = UserRepo().get_by_email("stale_probe@e2e.test")
    assert u is not None  # deactivated, not deleted — no user-delete function exists
    assert u.is_active is False


def test_dataset_matching_e2e_pattern_deletes_row_and_unlinks_file(db_env, tmp_path):
    from abkit.db.models import Dataset
    from abkit.db.repositories import DatasetRepo

    e2e_user = _make_user("uploader@e2e.test", "editor")
    csv_path = tmp_path / "probe.csv"
    csv_path.write_text("a,b\n1,2\n", encoding="utf-8")
    dataset_id = DatasetRepo().create(
        kind="pre_design", filename="probe.csv", n_rows=1, columns=["a", "b"],
        storage_path=str(csv_path), sha256="deadbeef", uploaded_by=uuid.UUID(e2e_user.id),
    )
    # Dataset's age column is uploaded_at, not created_at — backdate it directly.
    from datetime import datetime, timedelta, timezone

    from abkit.db.engine import session_scope

    with session_scope() as s:
        row = s.get(Dataset, dataset_id)
        row.uploaded_at = datetime.now(timezone.utc) - timedelta(hours=2)

    assert csv_path.exists()
    result = jobs.run_cleanup_dev(dry_run=False, min_age_hours=1)
    assert "probe.csv" in result["datasets"]
    assert DatasetRepo().get_by_id(dataset_id) is None
    assert not csv_path.exists()


def test_cleanup_dev_vacuums_after_a_real_delete(db_env):
    """Item A2 (DB bloat package): a cleanup-dev run that actually deleted
    something triggers a VACUUM of every table it could have touched — the
    root cause of the 2+ GB assignments bloat this package fixes was
    exactly that no delete path ever did this."""
    admin = _make_user("vacuum_admin@co.com", "admin")
    jobs.run_design(admin, _config("_dev_vacuum_probe"), _design_data(seed=7))

    with patch("abkit.db.maintenance.vacuum_tables") as mock_vacuum:
        result = jobs.run_cleanup_dev(dry_run=False, min_age_hours=1)

    assert "_dev_vacuum_probe" in result["experiments"]
    mock_vacuum.assert_called_once()
    (vacuumed_tables,) = mock_vacuum.call_args.args
    assert "assignments" in vacuumed_tables
    assert "experiments" in vacuumed_tables
    assert "datasets" in vacuumed_tables


def test_cleanup_dev_dry_run_never_vacuums(db_env):
    admin = _make_user("vacuum_dry_admin@co.com", "admin")
    jobs.run_design(admin, _config("_dev_vacuum_dry_probe"), _design_data(seed=8))

    with patch("abkit.db.maintenance.vacuum_tables") as mock_vacuum:
        result = jobs.run_cleanup_dev(dry_run=True)

    assert "_dev_vacuum_dry_probe" in result["experiments"]
    mock_vacuum.assert_not_called()


def test_cleanup_dev_skips_vacuum_when_nothing_was_deleted(db_env):
    _make_user("vacuum_noop_admin@co.com", "admin")

    with patch("abkit.db.maintenance.vacuum_tables") as mock_vacuum:
        result = jobs.run_cleanup_dev(dry_run=False, min_age_hours=1)

    assert all(v == [] for v in result.values())
    mock_vacuum.assert_not_called()
