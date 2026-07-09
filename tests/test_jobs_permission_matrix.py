"""abkit/jobs.py — единая точка запуска мутаций, обязана применять guard'ы
независимо от UI. Критерий готовности этапа D2 (DOCKER.md §12): "Viewer не
может вызвать мутацию даже прямым вызовом сервисной функции — проверка не
только в UI". Каждая строка матрицы прав из §4.1 покрыта здесь напрямую,
минуя HTTP-транспорт целиком."""

import uuid

import numpy as np
import pandas as pd
import pytest

from abkit import jobs
from abkit.auth.guards import AuthError, CurrentUser
from abkit.config import DesignConfig, MetricConfig


@pytest.fixture
def db_env(db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_MODE", "db")
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path / "data"))
    # isolation.apply_isolation() в файловом плече читает registry.json по
    # ABKIT_EXPERIMENTS_DIR независимо от ABKIT_MODE — без изоляции тесты бы
    # зависели от того, что реально лежит в ~/ab_experiments на этой машине.
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path / "file_side"))
    yield


@pytest.fixture
def users(db_env):
    from abkit.db.repositories import UserRepo

    repo = UserRepo()
    viewer_id = repo.create(email="viewer@co.com", first_name="V", password_hash="h", role="viewer")
    editor_id = repo.create(email="editor@co.com", first_name="E", password_hash="h", role="editor")
    other_editor_id = repo.create(email="editor2@co.com", first_name="E2", password_hash="h", role="editor")
    admin_id = repo.create(email="admin@co.com", first_name="A", password_hash="h", role="admin")
    return {
        "viewer": CurrentUser(id=str(viewer_id), email="viewer@co.com", name="V", role="viewer"),
        "editor": CurrentUser(id=str(editor_id), email="editor@co.com", name="E", role="editor"),
        "other_editor": CurrentUser(
            id=str(other_editor_id), email="editor2@co.com", name="E2", role="editor"
        ),
        "admin": CurrentUser(id=str(admin_id), email="admin@co.com", name="A", role="admin"),
    }


def _design_data(n=500, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.1, size=n),
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
        seed=1,
    )


# --------------------------------------------------------------------------
# run_design — Editor+ (DOCKER.md §4.1: "Создавать эксперименты")
# --------------------------------------------------------------------------


def test_run_design_viewer_blocked(users):
    data = _design_data()
    with pytest.raises(AuthError):
        jobs.run_design(users["viewer"], _config("jobs_design_viewer", len(data)), data)


def test_run_design_editor_allowed_and_sets_owner(users):
    data = _design_data(seed=1)
    experiment = jobs.run_design(users["editor"], _config("jobs_design_editor", len(data)), data)
    assert experiment.assignments is not None

    from abkit.db.repositories import ExperimentRepo

    exp_row = ExperimentRepo().get_by_name("jobs_design_editor")
    assert str(exp_row.owner_id) == users["editor"].id


def test_run_design_admin_allowed(users):
    data = _design_data(seed=2)
    experiment = jobs.run_design(users["admin"], _config("jobs_design_admin", len(data)), data)
    assert experiment.assignments is not None


# --------------------------------------------------------------------------
# run_analyze — Editor+ на ЛЮБОМ эксперименте (без "своих/чужих")
# --------------------------------------------------------------------------


def test_run_analyze_viewer_blocked(users):
    data = _design_data(seed=3)
    experiment = jobs.run_design(users["admin"], _config("jobs_analyze_setup", len(data)), data)
    post_data = pd.DataFrame(
        {
            "user_id": experiment.assignments["unit_id"],
            "revenue": np.random.default_rng(4).normal(100, 20, size=len(data)),
            "clicks": np.random.default_rng(4).binomial(1, 0.1, size=len(data)),
        }
    )
    with pytest.raises(AuthError):
        jobs.run_analyze(users["viewer"], experiment, post_data)


def test_run_analyze_editor_allowed_on_others_experiment(users):
    """Analyze/Validate доступны Editor'у на ЛЮБОМ эксперименте — не только
    своем. Деликатное решение, зафиксированное явно при добавлении experiment_
    access/visible_roles (UX-пакет, CLAUDE.md раздел "Permissions model"):
    этот тест вызывает jobs.run_analyze() напрямую, минуя HTTP-уровень, где
    и живет единственное реальное ограничение — видимость эксперимента
    (abkit/access.py::can_view_experiment, применяется в backend/routers/
    experiments.py перед постановкой analyze/validate в очередь). Если
    редактор ВИДИТ эксперимент — jobs.run_analyze/run_validate_aa/
    run_validate_ab остаются доступны ЛЮБОМУ editor+, без ограничения по
    owner_id/experiment_access — намеренно НЕ через require_experiment_edit_
    access (см. backend/tests/test_analyze_validate_jobs.py::
    test_analyze_blocked_on_experiment_editor_cannot_see для теста на саму
    видимость)."""
    data = _design_data(seed=5)
    experiment = jobs.run_design(users["admin"], _config("jobs_analyze_others", len(data)), data)
    post_data = pd.DataFrame(
        {
            "user_id": experiment.assignments["unit_id"],
            "revenue": np.random.default_rng(6).normal(100, 20, size=len(data)),
            "clicks": np.random.default_rng(6).binomial(1, 0.1, size=len(data)),
        }
    )
    results = jobs.run_analyze(users["editor"], experiment, post_data)
    assert "revenue" in results.metrics


# --------------------------------------------------------------------------
# run_update_status — Editor только СВОИ, Admin любые
# --------------------------------------------------------------------------


def test_run_update_status_viewer_blocked(users):
    data = _design_data(seed=7)
    jobs.run_design(users["editor"], _config("jobs_status_viewer", len(data)), data)
    with pytest.raises(AuthError):
        jobs.run_update_status(users["viewer"], "jobs_status_viewer", "running")


def test_run_update_status_editor_own_experiment_ok(users):
    data = _design_data(seed=8)
    jobs.run_design(users["editor"], _config("jobs_status_own", len(data)), data)
    jobs.run_update_status(users["editor"], "jobs_status_own", "running")

    from abkit.db.repositories import ExperimentRepo

    assert ExperimentRepo().get_by_name("jobs_status_own").status == "running"


def test_run_update_status_editor_others_experiment_blocked(users):
    data = _design_data(seed=9)
    jobs.run_design(users["editor"], _config("jobs_status_others", len(data)), data)
    with pytest.raises(AuthError, match="only edit your own"):
        jobs.run_update_status(users["other_editor"], "jobs_status_others", "running")


def test_run_update_status_admin_any_experiment_ok(users):
    data = _design_data(seed=10)
    jobs.run_design(users["editor"], _config("jobs_status_admin", len(data)), data)
    jobs.run_update_status(users["admin"], "jobs_status_admin", "running")

    from abkit.db.repositories import ExperimentRepo

    assert ExperimentRepo().get_by_name("jobs_status_admin").status == "running"


# --------------------------------------------------------------------------
# run_delete_experiment — владелец ИЛИ Admin (require_owner_or_admin, та же
# политика, что у update_status). Изменено по явному запросу пользователя —
# раньше было Admin-only без исключений; см. jobs.py.
# --------------------------------------------------------------------------


def test_run_delete_experiment_viewer_blocked(users):
    data = _design_data(seed=11)
    jobs.run_design(users["admin"], _config("jobs_delete_viewer", len(data)), data)
    with pytest.raises(AuthError):
        jobs.run_delete_experiment(users["viewer"], "jobs_delete_viewer")


def test_run_delete_experiment_owner_editor_allowed(users):
    data = _design_data(seed=12)
    jobs.run_design(users["editor"], _config("jobs_delete_own_editor", len(data)), data)
    jobs.run_delete_experiment(users["editor"], "jobs_delete_own_editor")

    from abkit.db.repositories import ExperimentRepo

    assert ExperimentRepo().get_by_name("jobs_delete_own_editor") is None


def test_run_delete_experiment_non_owner_editor_blocked(users):
    data = _design_data(seed=14)
    jobs.run_design(users["editor"], _config("jobs_delete_others", len(data)), data)
    with pytest.raises(AuthError, match="only edit your own"):
        jobs.run_delete_experiment(users["other_editor"], "jobs_delete_others")

    from abkit.db.repositories import ExperimentRepo

    assert ExperimentRepo().get_by_name("jobs_delete_others") is not None


def test_run_delete_experiment_admin_allowed(users):
    data = _design_data(seed=13)
    jobs.run_design(users["editor"], _config("jobs_delete_admin", len(data)), data)
    jobs.run_delete_experiment(users["admin"], "jobs_delete_admin")

    from abkit.db.repositories import ExperimentRepo

    assert ExperimentRepo().get_by_name("jobs_delete_admin") is None


def test_get_experiment_deletion_summary_counts_assignments(users):
    data = _design_data(n=250, seed=15)
    jobs.run_design(users["editor"], _config("jobs_delete_summary", len(data)), data)

    summary = jobs.get_experiment_deletion_summary(users["editor"], "jobs_delete_summary")
    assert summary["assignments"] == 250
    assert summary["datasets"] == 0
    assert summary["results"] == 0


def test_get_experiment_deletion_summary_non_owner_editor_blocked(users):
    data = _design_data(seed=16)
    jobs.run_design(users["editor"], _config("jobs_delete_summary_others", len(data)), data)
    with pytest.raises(AuthError, match="only edit your own"):
        jobs.get_experiment_deletion_summary(users["other_editor"], "jobs_delete_summary_others")


# --------------------------------------------------------------------------
# run_validate_aa/run_validate_ab — Editor+ (тот же смысл, что и Analyze)
# --------------------------------------------------------------------------


def test_run_validate_aa_viewer_blocked(users):
    data = _design_data(seed=14)
    with pytest.raises(AuthError):
        jobs.run_validate_aa(users["viewer"], data, _config("jobs_val_viewer", len(data)), n_sims=5)


def test_run_validate_aa_editor_allowed(users):
    data = _design_data(seed=15, n=300)
    report = jobs.run_validate_aa(
        users["editor"], data, _config("jobs_val_editor", len(data)), n_sims=5, show_progress=False
    )
    assert len(report.methods) > 0


def test_ids_are_real_uuids_not_placeholder_strings(users):
    """Sanity: owner_id используемый в тестах — настоящий UUID пользователя, а
    не служебный системный юзер (иначе тесты выше молчаливо проверяли бы не то)."""
    assert uuid.UUID(users["editor"].id)
    assert uuid.UUID(users["admin"].id)
