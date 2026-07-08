"""Интеграционные тесты репозиториев (abkit/db/repositories.py) против
реального Postgres — через testcontainers (или TEST_DATABASE_URL в CI).
Критерий готовности этапа D1 (DOCKER.md, раздел 12)."""

import uuid

import numpy as np
import pandas as pd
import pytest

from abkit.db.repositories import (
    AssignmentRepo,
    AuditRepo,
    BlockRepo,
    DatasetRepo,
    ExperimentRepo,
    JobRepo,
    RepoError,
    ResultRepo,
    UserRepo,
)


def _make_user(db_url, email="a@co.com", role="admin"):
    return UserRepo().create(email=email, name="A", password_hash="hash123", role=role)


def test_user_repo_create_and_get(db_url):
    user_id = _make_user(db_url, email="user1@co.com", role="editor")
    repo = UserRepo()

    by_id = repo.get_by_id(user_id)
    assert by_id is not None
    assert by_id.email == "user1@co.com"
    assert by_id.role == "editor"
    assert by_id.is_active is True
    assert by_id.failed_logins == 0

    by_email = repo.get_by_email("user1@co.com")
    assert by_email is not None
    assert by_email.id == user_id


def test_user_repo_email_is_case_insensitive_citext(db_url):
    UserRepo().create(email="Mixed@Co.com", name="M", password_hash="h", role="viewer")
    found = UserRepo().get_by_email("mixed@co.com")
    assert found is not None
    assert found.email.lower() == "mixed@co.com"


def test_user_repo_rejects_duplicate_email(db_url):
    UserRepo().create(email="dup@co.com", name="D1", password_hash="h", role="viewer")
    with pytest.raises(RepoError, match="уже существует"):
        UserRepo().create(email="dup@co.com", name="D2", password_hash="h2", role="editor")


def test_user_repo_role_and_active_updates(db_url):
    user_id = _make_user(db_url, email="u2@co.com", role="viewer")
    repo = UserRepo()

    repo.update_role(user_id, "editor")
    assert repo.get_by_id(user_id).role == "editor"

    repo.set_active(user_id, False)
    assert repo.get_by_id(user_id).is_active is False


def test_user_repo_login_failure_locks_after_max_attempts(db_url):
    user_id = _make_user(db_url, email="brute@co.com")
    repo = UserRepo()
    for _ in range(4):
        repo.record_login_failure("brute@co.com", max_attempts=5, lockout_minutes=15)
    assert repo.get_by_id(user_id).locked_until is None

    repo.record_login_failure("brute@co.com", max_attempts=5, lockout_minutes=15)
    user = repo.get_by_id(user_id)
    assert user.failed_logins == 5
    assert user.locked_until is not None


def test_user_repo_login_success_resets_failure_counter(db_url):
    user_id = _make_user(db_url, email="reset@co.com")
    repo = UserRepo()
    repo.record_login_failure("reset@co.com")
    repo.record_login_failure("reset@co.com")
    repo.record_login_success(user_id)

    user = repo.get_by_id(user_id)
    assert user.failed_logins == 0
    assert user.locked_until is None
    assert user.last_login_at is not None


def test_experiment_repo_create_get_list_and_status(db_url):
    owner_id = _make_user(db_url, email="owner1@co.com")
    exp_repo = ExperimentRepo()

    exp = exp_repo.create(
        name="exp_a", owner_id=owner_id, status="designed", config={"unit_col": "user_id"}
    )
    assert exp.name == "exp_a"
    assert exp.status == "designed"
    assert exp.config == {"unit_col": "user_id"}

    fetched = exp_repo.get_by_name("exp_a")
    assert fetched is not None
    assert fetched.id == exp.id

    exp_repo.create(name="exp_b", owner_id=owner_id, status="archived", config={})
    active = exp_repo.list_all(active_only=True)
    assert {e.name for e in active} == {"exp_a"}
    all_exps = exp_repo.list_all(active_only=False)
    assert {e.name for e in all_exps} == {"exp_a", "exp_b"}

    exp_repo.update_status("exp_a", "running")
    updated = exp_repo.get_by_name("exp_a")
    assert updated.status == "running"
    assert updated.started_at is not None


def test_experiment_repo_rejects_duplicate_name(db_url):
    owner_id = _make_user(db_url, email="owner2@co.com")
    ExperimentRepo().create(name="dup_exp", owner_id=owner_id, status="designed", config={})
    with pytest.raises(RepoError, match="уже существует"):
        ExperimentRepo().create(name="dup_exp", owner_id=owner_id, status="designed", config={})


def test_assignment_repo_bulk_insert_and_load_roundtrip(db_url):
    owner_id = _make_user(db_url, email="owner3@co.com")
    exp = ExperimentRepo().create(name="assign_exp", owner_id=owner_id, status="designed", config={})

    n = 500
    rng = np.random.default_rng(0)
    assignments = pd.DataFrame(
        {
            "unit_id": [f"u{i}" for i in range(n)],
            "group": rng.choice(["control", "treatment"], size=n),
            "stratum": rng.choice(["a", "b"], size=n),
            "assigned_at": pd.Timestamp.now(tz="UTC"),
        }
    )

    repo = AssignmentRepo()
    repo.bulk_insert(exp.id, assignments)
    loaded = repo.load(exp.id)

    assert len(loaded) == n
    assert set(loaded.columns) == {"unit_id", "group", "stratum", "assigned_at"}
    assert set(loaded["unit_id"]) == set(assignments["unit_id"])
    assert set(loaded["group"].unique()) <= {"control", "treatment"}


def test_assignment_repo_occupied_units_only_active_experiments(db_url):
    owner_id = _make_user(db_url, email="owner4@co.com")
    exp_repo = ExperimentRepo()
    assign_repo = AssignmentRepo()

    active_exp = exp_repo.create(name="active_exp", owner_id=owner_id, status="running", config={})
    archived_exp = exp_repo.create(name="archived_exp", owner_id=owner_id, status="archived", config={})

    now = pd.Timestamp.now(tz="UTC")
    assign_repo.bulk_insert(
        active_exp.id,
        pd.DataFrame({"unit_id": ["u1", "u2"], "group": ["control", "treatment"], "stratum": [None, None], "assigned_at": now}),
    )
    assign_repo.bulk_insert(
        archived_exp.id,
        pd.DataFrame({"unit_id": ["u3"], "group": ["control"], "stratum": [None], "assigned_at": now}),
    )

    occupied = assign_repo.occupied_units_for_active_experiments()
    assert occupied == {"active_exp": {"u1", "u2"}}


def test_dataset_repo_create_and_list(db_url):
    owner_id = _make_user(db_url, email="owner5@co.com")
    exp = ExperimentRepo().create(name="dataset_exp", owner_id=owner_id, status="designed", config={})

    ds_id = DatasetRepo().create(
        experiment_id=exp.id,
        kind="pre_design",
        filename="pre.csv",
        n_rows=100,
        columns=["user_id", "revenue"],
        storage_path="/data/pre.parquet",
        sha256="abc123",
        uploaded_by=owner_id,
    )
    assert isinstance(ds_id, uuid.UUID)

    datasets = DatasetRepo().list_for_experiment(exp.id)
    assert len(datasets) == 1
    assert datasets[0].filename == "pre.csv"
    assert datasets[0].kind == "pre_design"


def test_dataset_repo_compute_sha256_deterministic():
    df1 = pd.DataFrame({"a": [1, 2, 3]})
    df2 = pd.DataFrame({"a": [1, 2, 3]})
    df3 = pd.DataFrame({"a": [1, 2, 4]})
    assert DatasetRepo.compute_sha256(df1) == DatasetRepo.compute_sha256(df2)
    assert DatasetRepo.compute_sha256(df1) != DatasetRepo.compute_sha256(df3)


def test_result_repo_create_and_latest(db_url):
    owner_id = _make_user(db_url, email="owner6@co.com")
    exp = ExperimentRepo().create(name="result_exp", owner_id=owner_id, status="running", config={})

    ResultRepo().create(experiment_id=exp.id, results={"v": 1}, report_path="/data/r1.html")
    latest_id = ResultRepo().create(experiment_id=exp.id, results={"v": 2}, report_path="/data/r2.html")

    latest = ResultRepo().latest_for_experiment(exp.id)
    assert latest is not None
    assert latest.id == latest_id
    assert latest.results == {"v": 2}


def test_experiment_repo_create_auto_creates_default_blocks(db_url):
    owner_id = _make_user(db_url, email="owner8@co.com")
    exp = ExperimentRepo().create(name="blocks_exp", owner_id=owner_id, status="designed", config={})
    assert exp.publication_status == "draft"

    blocks = BlockRepo().list_for_experiment(exp.id)
    assert [b.kind for b in blocks] == ["hypothesis", "conclusion", "decision"]
    assert all(b.content_md == "" for b in blocks)


def test_experiment_repo_publication_status_toggle(db_url):
    owner_id = _make_user(db_url, email="owner9@co.com")
    ExperimentRepo().create(name="pub_exp", owner_id=owner_id, status="designed", config={})

    ExperimentRepo().update_publication_status("pub_exp", "published")
    assert ExperimentRepo().get_by_name("pub_exp").publication_status == "published"

    ExperimentRepo().update_publication_status("pub_exp", "draft")
    assert ExperimentRepo().get_by_name("pub_exp").publication_status == "draft"


def test_experiment_repo_rename(db_url):
    owner_id = _make_user(db_url, email="owner10@co.com")
    ExperimentRepo().create(name="old_name", owner_id=owner_id, status="designed", config={})

    ExperimentRepo().rename("old_name", "new_name")
    assert ExperimentRepo().get_by_name("old_name") is None
    assert ExperimentRepo().get_by_name("new_name") is not None


def test_experiment_repo_rename_rejects_existing_name(db_url):
    owner_id = _make_user(db_url, email="owner11@co.com")
    ExperimentRepo().create(name="taken", owner_id=owner_id, status="designed", config={})
    ExperimentRepo().create(name="to_rename", owner_id=owner_id, status="designed", config={})

    with pytest.raises(RepoError, match="уже существует"):
        ExperimentRepo().rename("to_rename", "taken")


def test_block_repo_upsert_updates_existing_and_adds_custom(db_url):
    owner_id = _make_user(db_url, email="owner12@co.com")
    exp = ExperimentRepo().create(name="upsert_exp", owner_id=owner_id, status="designed", config={})
    repo = BlockRepo()

    initial = repo.list_for_experiment(exp.id)
    hypothesis = next(b for b in initial if b.kind == "hypothesis")

    # id строкой, не uuid.UUID — так его реально шлет HTTP (BlockIn.id: str),
    # см. abkit/db/models через Pydantic; раньше lookup по строке против
    # словаря, ключующегося UUID-объектами, всегда промахивался и создавал
    # дубликат вместо обновления (найдено e2e-тестом фронта, не этим тестом).
    updated = repo.upsert_many(
        exp.id,
        [
            {"id": str(hypothesis.id), "kind": "hypothesis", "title": "H", "content_md": "новая гипотеза", "position": 0},
            {"kind": "custom", "title": "Доп. блок", "content_md": "текст", "position": 3},
        ],
        updated_by=owner_id,
    )
    kinds = [b.kind for b in updated]
    assert kinds.count("hypothesis") == 1
    assert "custom" in kinds

    # На ВОЗВРАЩЕННОМ значении, а не на отдельном свежем select() ниже — роутер
    # сериализует в HTTP-ответ именно то, что вернул upsert_many(). Регрессия:
    # s.refresh(r) без предшествующего s.flush() перечитывал апдейт существующих
    # блоков ИЗ БД ДО коммита изменений, молча стирая их в возвращаемых объектах
    # (сама БД была корректна — только ответ API показывал старые данные).
    updated_hyp = next(b for b in updated if b.kind == "hypothesis")
    assert updated_hyp.content_md == "новая гипотеза"
    assert updated_hyp.id == hypothesis.id

    all_blocks = repo.list_for_experiment(exp.id)
    assert len(all_blocks) == 4  # hypothesis(обновлен)+conclusion+decision+новый custom
    hyp = next(b for b in all_blocks if b.kind == "hypothesis")
    assert hyp.id == hypothesis.id  # тот же ряд обновлен, не создан новый
    assert hyp.content_md == "новая гипотеза"


def test_block_repo_upsert_returns_updated_content_when_no_new_blocks_created(db_url):
    """Регрессия: когда payload содержит ТОЛЬКО существующие блоки (нет ни
    одного нового), explicit s.flush() в ветке создания нового блока не
    вызывается вообще — без отдельного s.flush() перед финальным refresh()
    апдейт существующих блоков не попадал в БД до refresh(), и возвращаемые
    объекты показывали старые данные (нашлось e2e-тестом фронта: правка
    только "Гипотезы" без добавления custom-блока — самый частый сценарий)."""
    owner_id = _make_user(db_url, email="owner16@co.com")
    exp = ExperimentRepo().create(name="update_only_exp", owner_id=owner_id, status="designed", config={})
    repo = BlockRepo()

    initial = repo.list_for_experiment(exp.id)
    updated = repo.upsert_many(
        exp.id,
        [
            {"id": str(b.id), "kind": b.kind, "title": b.title, "content_md": f"текст {b.kind}", "position": b.position}
            for b in initial
        ],
        updated_by=owner_id,
    )
    assert len(updated) == 3
    for b in updated:
        assert b.content_md == f"текст {b.kind}"


def test_block_repo_upsert_deletes_omitted_custom_block(db_url):
    owner_id = _make_user(db_url, email="owner13@co.com")
    exp = ExperimentRepo().create(name="del_block_exp", owner_id=owner_id, status="designed", config={})
    repo = BlockRepo()

    repo.upsert_many(exp.id, [{"kind": "custom", "title": "Temp", "content_md": "x", "position": 5}])
    assert any(b.kind == "custom" for b in repo.list_for_experiment(exp.id))

    # Пустой список custom-блоков в payload -> существующий custom удаляется,
    # фиксированные (hypothesis/conclusion/decision) остаются нетронутыми.
    repo.upsert_many(exp.id, [])
    remaining = repo.list_for_experiment(exp.id)
    assert [b.kind for b in remaining] == ["hypothesis", "conclusion", "decision"]


def test_dataset_repo_create_without_experiment_then_attach(db_url):
    owner_id = _make_user(db_url, email="owner14@co.com")
    ds_id = DatasetRepo().create(
        kind="pre_design", filename="pre.csv", n_rows=10, columns=["a"],
        storage_path="/data/pre.csv", sha256="x", uploaded_by=owner_id,
    )
    assert DatasetRepo().get_by_id(ds_id).experiment_id is None

    exp = ExperimentRepo().create(name="attach_exp", owner_id=owner_id, status="designed", config={})
    DatasetRepo().attach_to_experiment(ds_id, exp.id)
    assert DatasetRepo().get_by_id(ds_id).experiment_id == exp.id


def test_job_repo_lifecycle(db_url):
    owner_id = _make_user(db_url, email="owner15@co.com")
    repo = JobRepo()

    job = repo.create(type="design", created_by=owner_id)
    assert job.status == "pending"

    repo.mark_running(job.id)
    assert repo.get_by_id(job.id).status == "running"

    repo.update_progress(job.id, {"stage": "Считаем мощность...", "pct": 40})
    assert repo.get_by_id(job.id).progress == {"stage": "Считаем мощность...", "pct": 40}

    repo.mark_completed(job.id, {"experiment_name": "exp_a"})
    completed = repo.get_by_id(job.id)
    assert completed.status == "completed"
    assert completed.result_ref == {"experiment_name": "exp_a"}
    assert completed.finished_at is not None


def test_job_repo_mark_failed_and_requires_confirmation(db_url):
    repo = JobRepo()

    failed_job = repo.create(type="analyze")
    repo.mark_failed(failed_job.id, "что-то пошло не так")
    assert repo.get_by_id(failed_job.id).status == "failed"
    assert repo.get_by_id(failed_job.id).error == "что-то пошло не так"

    confirm_job = repo.create(type="design")
    repo.mark_requires_confirmation(confirm_job.id, {"overlap": 5, "by_experiment": {"x": 5}})
    confirmed = repo.get_by_id(confirm_job.id)
    assert confirmed.status == "requires_confirmation"
    assert confirmed.result_ref == {"overlap": 5, "by_experiment": {"x": 5}}


def test_job_repo_list_unfinished(db_url):
    repo = JobRepo()
    pending = repo.create(type="design")
    running = repo.create(type="analyze")
    repo.mark_running(running.id)
    done = repo.create(type="validate_aa")
    repo.mark_completed(done.id)

    unfinished_ids = {j.id for j in repo.list_unfinished()}
    assert unfinished_ids == {pending.id, running.id}


def test_audit_repo_log_and_filter(db_url):
    owner_id = _make_user(db_url, email="owner7@co.com")
    audit = AuditRepo()

    audit.log(action="auth.login", user_id=owner_id, user_email="owner7@co.com")
    audit.log(action="experiment.create", user_id=owner_id, object_type="experiment", object_name="x")
    audit.log(action="auth.login_failed", user_email="someone@else.com")

    recent = audit.list_recent(limit=10)
    assert len(recent) == 3

    only_login = audit.list_recent(action="auth.login")
    assert len(only_login) == 1
    assert only_login[0].action == "auth.login"

    only_owner = audit.list_recent(user_id=owner_id)
    assert len(only_owner) == 2
