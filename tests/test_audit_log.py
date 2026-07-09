"""Критерий готовности этапа D3 (DOCKER.md §12): "каждая мутирующая сервисная
функция пишет audit-запись". Для каждого действия из схемы audit_log
(DOCKER.md §5) — отдельный тест, проверяющий, что соответствующая функция
абкit/jobs.py или abkit/auth/service.py действительно пишет запись с нужным
action/user/object. В конце — чек-лист по полному списку действий."""

import numpy as np
import pandas as pd
import pytest

from abkit import jobs
from abkit.auth.guards import AuthError, CurrentUser
from abkit.auth.passwords import hash_password
from abkit.config import DesignConfig, MetricConfig
from abkit.db.repositories import AuditRepo, UserRepo


@pytest.fixture
def db_env(db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_MODE", "db")
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ABKIT_SECRET_KEY", "a-real-generated-secret-for-audit-tests")
    # isolation.apply_isolation() в файловом плече читает registry.json по
    # ABKIT_EXPERIMENTS_DIR независимо от ABKIT_MODE — без изоляции тест словил
    # бы коллизию unit_id с реальными экспериментами на диске этой машины.
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path / "file_side"))
    yield


@pytest.fixture
def editor(db_env):
    user_id = UserRepo().create(
        email="editor@co.com", first_name="Editor", password_hash=hash_password("pw"), role="editor"
    )
    return CurrentUser(id=str(user_id), email="editor@co.com", name="Editor", role="editor")


@pytest.fixture
def admin(db_env):
    user_id = UserRepo().create(
        email="admin@co.com", first_name="Admin", password_hash=hash_password("pw"), role="admin"
    )
    return CurrentUser(id=str(user_id), email="admin@co.com", name="Admin", role="admin")


def _design_data(n=300, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.1, size=n),
        }
    )


def _config(name, n):
    # isolation="off": несколько тестовых экспериментов в этом файле намеренно
    # переиспользуют одинаковые диапазоны unit_id (u0..uN) — изоляция между
    # ними тут не то, что проверяется, и по умолчанию (mode="exclude") иначе
    # обнулила бы кандидатов второго эксперимента.
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
        seed=1,
        isolation="off",
    )


def _last_action(**filters):
    entries = AuditRepo().list_recent(limit=1, **filters)
    return entries[0] if entries else None


def test_run_design_writes_experiment_create_audit(editor):
    data = _design_data()
    jobs.run_design(editor, _config("audit_design_exp", len(data)), data)

    entry = _last_action(action="experiment.create")
    assert entry is not None
    assert entry.user_email == "editor@co.com"
    assert entry.object_name == "audit_design_exp"
    assert entry.object_type == "experiment"


def test_run_analyze_writes_analysis_run_audit(editor):
    data = _design_data(seed=2)
    experiment = jobs.run_design(editor, _config("audit_analyze_exp", len(data)), data)
    post_data = pd.DataFrame(
        {
            "user_id": experiment.assignments["unit_id"],
            "revenue": np.random.default_rng(3).normal(100, 20, size=len(data)),
            "clicks": np.random.default_rng(3).binomial(1, 0.1, size=len(data)),
        }
    )
    jobs.run_analyze(editor, experiment, post_data)

    entry = _last_action(action="analysis.run")
    assert entry is not None
    assert entry.object_name == "audit_analyze_exp"


def test_run_validate_aa_writes_validation_run_audit(editor):
    data = _design_data(seed=4, n=200)
    jobs.run_validate_aa(
        editor, data, _config("audit_val_aa_exp", len(data)), n_sims=5, show_progress=False
    )

    entry = _last_action(action="validation.run")
    assert entry is not None
    assert entry.object_name == "audit_val_aa_exp"
    assert entry.details == {"kind": "aa"}


def test_run_validate_ab_writes_validation_run_audit(editor):
    data = _design_data(seed=5, n=200)
    jobs.run_validate_ab(
        editor, data, _config("audit_val_ab_exp", len(data)), n_sims=5, effect=0.1, show_progress=False
    )

    entry = _last_action(action="validation.run")
    assert entry is not None
    assert entry.object_name == "audit_val_ab_exp"
    assert entry.details == {"kind": "ab"}


def test_run_update_status_writes_status_change_audit_with_from_to(editor):
    data = _design_data(seed=6)
    jobs.run_design(editor, _config("audit_status_exp", len(data)), data)
    jobs.run_update_status(editor, "audit_status_exp", "running")

    entry = _last_action(action="experiment.status_change")
    assert entry is not None
    assert entry.object_name == "audit_status_exp"
    assert entry.details == {"from": "designed", "to": "running"}


def test_run_delete_experiment_writes_delete_audit(editor, admin):
    data = _design_data(seed=7)
    jobs.run_design(editor, _config("audit_delete_exp", len(data)), data)
    jobs.run_delete_experiment(admin, "audit_delete_exp")

    entry = _last_action(action="experiment.delete")
    assert entry is not None
    assert entry.user_email == "admin@co.com"
    assert entry.object_name == "audit_delete_exp"


def test_login_success_writes_auth_login_audit(db_env):
    from abkit.auth.service import login

    UserRepo().create(
        email="loginaudit@co.com", first_name="L", password_hash=hash_password("realpw"), role="viewer"
    )
    login("loginaudit@co.com", "realpw")

    entry = _last_action(action="auth.login")
    assert entry is not None
    assert entry.user_email == "loginaudit@co.com"


def test_login_failure_writes_auth_login_failed_audit(db_env):
    from abkit.auth.service import login

    UserRepo().create(
        email="failaudit@co.com", first_name="F", password_hash=hash_password("realpw"), role="viewer"
    )
    with pytest.raises(AuthError):
        login("failaudit@co.com", "wrongpw")

    entry = _last_action(action="auth.login_failed")
    assert entry is not None
    assert entry.user_email == "failaudit@co.com"
    assert entry.details == {"reason": "wrong_password"}


def test_admin_create_user_writes_user_create_audit(admin):
    from abkit.auth.service import admin_create_user

    admin_create_user(admin, email="created@co.com", first_name="C", role="viewer")

    entry = _last_action(action="user.create")
    assert entry is not None
    assert entry.user_email == "admin@co.com"
    assert entry.object_name == "created@co.com"


def test_admin_create_user_via_cli_writes_audit_with_cli_actor(db_env):
    """DOCKER.md §6.2: аудит пишется на уровне сервисной функции, чтобы
    CLI-действия (acting_user=None, доверенный abkit-admin) тоже логировались."""
    from abkit.auth.service import admin_create_user

    admin_create_user(None, email="clicreated@co.com", first_name="CLI", role="admin")

    entry = _last_action(action="user.create", object_name="clicreated@co.com")
    assert entry is not None
    assert entry.user_id is None
    assert entry.user_email == "cli:abkit-admin"


def test_admin_reset_password_writes_audit(admin):
    from abkit.auth.service import admin_create_user, admin_reset_password

    admin_create_user(admin, email="resetme@co.com", first_name="R", role="viewer", password="initial")
    admin_reset_password(admin, target_email="resetme@co.com")

    entry = _last_action(action="user.password_reset")
    assert entry is not None
    assert entry.object_name == "resetme@co.com"


def test_admin_set_role_writes_audit_with_from_to(admin):
    from abkit.auth.service import admin_create_user, admin_set_role

    admin_create_user(admin, email="rolechange@co.com", first_name="RC", role="viewer", password="pw")
    admin_set_role(admin, target_email="rolechange@co.com", role="editor")

    entry = _last_action(action="user.role_change")
    assert entry is not None
    assert entry.details == {"from": "viewer", "to": "editor"}


def test_admin_set_active_writes_audit(admin):
    from abkit.auth.service import admin_create_user, admin_set_active

    admin_create_user(admin, email="toggle@co.com", first_name="T", role="viewer", password="pw")
    admin_set_active(admin, target_email="toggle@co.com", is_active=False)

    entry = _last_action(action="user.active_change")
    assert entry is not None
    assert entry.details == {"is_active": False}


def test_self_register_writes_user_create_audit(db_env, monkeypatch):
    from abkit.auth.service import self_register

    monkeypatch.setenv("ABKIT_ALLOW_SELF_REGISTRATION", "true")
    self_register(email="selfaudit@co.com", first_name="S", password="pw12345")

    entry = _last_action(action="user.create", object_name="selfaudit@co.com")
    assert entry is not None
    assert entry.details["self_registered"] is True


# --------------------------------------------------------------------------
# AuditRepo.list_recent / count — фильтры и пагинация (нужны UI-страницам
# «История»/«Аудит»)
# --------------------------------------------------------------------------


def test_audit_repo_filters_by_object_name(editor):
    data1 = _design_data(seed=10)
    data2 = _design_data(seed=11)
    jobs.run_design(editor, _config("audit_filter_a", len(data1)), data1)
    jobs.run_design(editor, _config("audit_filter_b", len(data2)), data2)

    entries = AuditRepo().list_recent(object_name="audit_filter_a")
    assert len(entries) == 1
    assert entries[0].object_name == "audit_filter_a"


def test_audit_repo_pagination_with_count(editor):
    for i in range(5):
        data = _design_data(seed=20 + i, n=50)
        jobs.run_design(editor, _config(f"audit_page_{i}", len(data)), data)

    total = AuditRepo().count(action="experiment.create")
    assert total >= 5

    page1 = AuditRepo().list_recent(action="experiment.create", limit=2, offset=0)
    page2 = AuditRepo().list_recent(action="experiment.create", limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert {e.id for e in page1}.isdisjoint({e.id for e in page2})