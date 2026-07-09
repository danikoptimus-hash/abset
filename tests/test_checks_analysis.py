import pandas as pd
import pytest

from abkit.checks import AnalysisError, check_data_loss, check_no_duplicates, join_with_assignments


def make_assignments(n=100):
    return pd.DataFrame(
        {
            "unit_id": [f"u{i}" for i in range(n)],
            "group": ["control"] * (n // 2) + ["treatment"] * (n // 2),
            "stratum": ["_all_"] * n,
            "assigned_at": pd.Timestamp.now(),
        }
    )


def test_check_no_duplicates_passes_on_clean_data():
    data = pd.DataFrame({"user_id": ["a", "b", "c"]})
    check_no_duplicates(data, "user_id")  # не должно бросать


def test_check_no_duplicates_raises_on_dupes():
    data = pd.DataFrame({"user_id": ["a", "b", "a"]})
    with pytest.raises(AnalysisError, match="duplicate"):
        check_no_duplicates(data, "user_id")


def test_join_with_assignments_inner_join():
    assignments = make_assignments(10)
    data = pd.DataFrame({"user_id": [f"u{i}" for i in range(5, 15)], "revenue": range(10)})
    merged = join_with_assignments(assignments, data, "user_id")
    assert len(merged) == 5  # только u5..u9 пересекаются
    assert "group" in merged.columns
    assert "revenue" in merged.columns


def test_join_with_assignments_raises_on_duplicate_data():
    assignments = make_assignments(10)
    data = pd.DataFrame({"user_id": ["u1", "u1", "u2"], "revenue": [1, 2, 3]})
    with pytest.raises(AnalysisError):
        join_with_assignments(assignments, data, "user_id")


def test_check_data_loss_no_loss_is_symmetric():
    assignments = make_assignments(100)
    present_ids = pd.Series(assignments["unit_id"])
    result = check_data_loss(assignments, present_ids)
    assert result.missing == {"control": 0, "treatment": 0}
    assert result.symmetric


def test_check_data_loss_symmetric_loss_passes():
    assignments = make_assignments(1000)
    # теряем 10% из каждой группы поровну
    control_ids = assignments.loc[assignments["group"] == "control", "unit_id"]
    treat_ids = assignments.loc[assignments["group"] == "treatment", "unit_id"]
    present_ids = pd.concat([control_ids.iloc[: int(len(control_ids) * 0.9)], treat_ids.iloc[: int(len(treat_ids) * 0.9)]])

    result = check_data_loss(assignments, present_ids)
    assert result.symmetric
    assert result.missing_rate["control"] == pytest.approx(0.1, abs=0.02)
    assert result.missing_rate["treatment"] == pytest.approx(0.1, abs=0.02)


def test_check_data_loss_asymmetric_loss_fails():
    assignments = make_assignments(1000)
    control_ids = assignments.loc[assignments["group"] == "control", "unit_id"]
    treat_ids = assignments.loc[assignments["group"] == "treatment", "unit_id"]
    # control теряет 50%, treatment не теряет ничего -> асимметрия
    present_ids = pd.concat([control_ids.iloc[: int(len(control_ids) * 0.5)], treat_ids])

    result = check_data_loss(assignments, present_ids)
    assert not result.symmetric


def test_check_data_loss_reports_assigned_and_present_counts():
    assignments = make_assignments(100)
    present_ids = pd.Series(assignments["unit_id"].iloc[:80])
    result = check_data_loss(assignments, present_ids)
    assert sum(result.assigned.values()) == 100
    assert sum(result.present.values()) == 80
    assert sum(result.missing.values()) == 20
