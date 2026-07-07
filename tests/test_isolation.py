import pandas as pd

from abkit import storage
from abkit.design import isolation


def _setup_other_experiment(experiments_dir, name, unit_ids, status="running"):
    path = storage.create_experiment_dir(experiments_dir, name)
    storage.register_experiment(experiments_dir, name, path, status="designed")
    if status != "designed":
        storage.update_status(experiments_dir, name, status)
    assignments = pd.DataFrame(
        {
            "unit_id": unit_ids,
            "group": ["control"] * len(unit_ids),
            "stratum": ["_all_"] * len(unit_ids),
            "assigned_at": pd.Timestamp.now(),
        }
    )
    storage.save_assignments(path, assignments)
    return path


def make_candidates(n):
    return pd.DataFrame({"user_id": [f"u{i}" for i in range(n)]})


def test_isolation_excludes_overlapping_units(tmp_path):
    _setup_other_experiment(tmp_path, "other_exp", [f"u{i}" for i in range(10, 20)])
    data = make_candidates(30)

    result = isolation.apply_isolation(data, "user_id", tmp_path, mode="exclude")

    assert result.n_before == 30
    assert result.n_excluded == 10
    assert result.n_available == 20
    assert result.excluded_by_experiment == {"other_exp": 10}
    remaining = set(result.candidates["user_id"])
    assert remaining.isdisjoint({f"u{i}" for i in range(10, 20)})


def test_isolation_off_mode_does_not_filter(tmp_path):
    _setup_other_experiment(tmp_path, "other_exp", [f"u{i}" for i in range(10, 20)])
    data = make_candidates(30)

    result = isolation.apply_isolation(data, "user_id", tmp_path, mode="off")

    assert result.n_excluded == 0
    assert len(result.candidates) == 30


def test_isolation_warn_mode_reports_but_does_not_filter(tmp_path):
    _setup_other_experiment(tmp_path, "other_exp", [f"u{i}" for i in range(10, 20)])
    data = make_candidates(30)

    result = isolation.apply_isolation(data, "user_id", tmp_path, mode="warn")

    assert result.excluded_by_experiment == {"other_exp": 10}
    assert len(result.candidates) == 30  # не фильтруем, только сообщаем
    assert result.n_excluded == 0


def test_isolation_ignores_completed_and_archived_experiments(tmp_path):
    _setup_other_experiment(tmp_path, "completed_exp", [f"u{i}" for i in range(0, 5)])
    storage.update_status(tmp_path, "completed_exp", "completed")
    _setup_other_experiment(tmp_path, "archived_exp", [f"u{i}" for i in range(5, 10)], status="running")
    storage.update_status(tmp_path, "archived_exp", "archived")

    data = make_candidates(30)
    result = isolation.apply_isolation(data, "user_id", tmp_path, mode="exclude")

    assert result.excluded_by_experiment == {}
    assert result.n_excluded == 0


def test_isolation_respects_exclude_experiments_list(tmp_path):
    _setup_other_experiment(tmp_path, "exp_a", [f"u{i}" for i in range(10, 15)])
    _setup_other_experiment(tmp_path, "exp_b", [f"u{i}" for i in range(20, 25)])

    data = make_candidates(30)
    result = isolation.apply_isolation(
        data, "user_id", tmp_path, mode="exclude", exclude_experiments=["exp_a"]
    )

    # exp_a исключен из проверки изоляции -> его юзеры не должны быть отфильтрованы
    assert "exp_a" not in result.excluded_by_experiment
    assert result.excluded_by_experiment == {"exp_b": 5}
    remaining = set(result.candidates["user_id"])
    assert "u10" in remaining
    assert "u20" not in remaining


def test_isolation_skips_current_experiment(tmp_path):
    _setup_other_experiment(tmp_path, "self_exp", [f"u{i}" for i in range(10, 15)])
    data = make_candidates(30)

    result = isolation.apply_isolation(
        data, "user_id", tmp_path, mode="exclude", current_experiment_name="self_exp"
    )

    assert result.excluded_by_experiment == {}
    assert result.n_excluded == 0


def test_isolation_no_active_experiments_no_exclusion(tmp_path):
    data = make_candidates(10)
    result = isolation.apply_isolation(data, "user_id", tmp_path, mode="exclude")
    assert result.n_excluded == 0
    assert result.n_available == 10
