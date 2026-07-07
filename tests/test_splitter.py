import numpy as np
import pandas as pd
import pytest

from abkit.design import splitter


def make_unit_ids(n: int) -> pd.Series:
    return pd.Series([f"u{i}" for i in range(n)], name="unit_id")


def test_simple_split_group_sizes_match_proportions_exactly():
    unit_ids = make_unit_ids(10_000)
    groups = {"control": 0.5, "treatment": 0.5}
    labels = splitter.simple_split(unit_ids, groups, seed=42)
    counts = labels.value_counts()
    assert counts["control"] == 5000
    assert counts["treatment"] == 5000


def test_simple_split_uneven_proportions_sum_to_n():
    unit_ids = make_unit_ids(999)
    groups = {"a": 0.34, "b": 0.33, "c": 0.33}
    labels = splitter.simple_split(unit_ids, groups, seed=1)
    counts = labels.value_counts()
    assert counts.sum() == 999
    # largest remainder method: доли должны быть в пределах 1 юнита от идеала
    for name, p in groups.items():
        assert abs(counts[name] - p * 999) <= 1


def test_simple_split_deterministic_with_same_seed():
    unit_ids = make_unit_ids(500)
    groups = {"control": 0.5, "treatment": 0.5}
    labels1 = splitter.simple_split(unit_ids, groups, seed=7)
    labels2 = splitter.simple_split(unit_ids, groups, seed=7)
    pd.testing.assert_series_equal(labels1, labels2)


def test_simple_split_different_seeds_differ():
    unit_ids = make_unit_ids(500)
    groups = {"control": 0.5, "treatment": 0.5}
    labels1 = splitter.simple_split(unit_ids, groups, seed=1)
    labels2 = splitter.simple_split(unit_ids, groups, seed=2)
    assert not labels1.equals(labels2)


def test_stratified_split_preserves_proportions_within_stratum():
    rng = np.random.default_rng(0)
    n = 4000
    strata = pd.Series(rng.choice(["a", "b", "c"], size=n), name="stratum")
    unit_ids = make_unit_ids(n)
    groups = {"control": 0.5, "treatment": 0.5}
    labels = splitter.stratified_split(unit_ids, strata, groups, seed=123)

    for stratum_value in ["a", "b", "c"]:
        mask = strata == stratum_value
        counts = labels[mask].value_counts()
        stratum_n = mask.sum()
        for name, p in groups.items():
            # largest remainder внутри страты -> отклонение максимум на 1 юнит
            assert abs(counts.get(name, 0) - p * stratum_n) <= 1


def test_stratified_split_deterministic_with_same_seed():
    rng = np.random.default_rng(0)
    n = 1000
    strata = pd.Series(rng.choice(["a", "b"], size=n), name="stratum")
    unit_ids = make_unit_ids(n)
    groups = {"control": 0.5, "treatment": 0.5}
    labels1 = splitter.stratified_split(unit_ids, strata, groups, seed=5)
    labels2 = splitter.stratified_split(unit_ids, strata, groups, seed=5)
    pd.testing.assert_series_equal(labels1, labels2)


def test_hash_split_deterministic_same_salt():
    unit_ids = make_unit_ids(2000)
    groups = {"control": 0.5, "treatment": 0.5}
    labels1 = splitter.hash_split(unit_ids, groups, salt="fixed-salt")
    labels2 = splitter.hash_split(unit_ids, groups, salt="fixed-salt")
    pd.testing.assert_series_equal(labels1, labels2)


def test_hash_split_different_salt_gives_different_assignment():
    unit_ids = make_unit_ids(2000)
    groups = {"control": 0.5, "treatment": 0.5}
    labels1 = splitter.hash_split(unit_ids, groups, salt="salt-a")
    labels2 = splitter.hash_split(unit_ids, groups, salt="salt-b")
    assert not labels1.equals(labels2)


def test_hash_split_proportions_approximately_correct():
    unit_ids = make_unit_ids(50_000)
    groups = {"control": 0.3, "treatment": 0.7}
    labels = splitter.hash_split(unit_ids, groups, salt="some-salt")
    fracs = labels.value_counts(normalize=True)
    assert abs(fracs["control"] - 0.3) < 0.02
    assert abs(fracs["treatment"] - 0.7) < 0.02


def test_hash_split_same_unit_always_same_group_regardless_of_others():
    groups = {"control": 0.5, "treatment": 0.5}
    full_ids = make_unit_ids(1000)
    full = splitter.hash_split(full_ids, groups, salt="s")
    subset_ids = full_ids[::2].reset_index(drop=True)
    subset = splitter.hash_split(subset_ids, groups, salt="s")

    full_by_id = dict(zip(full_ids, full))
    for uid, grp in zip(subset_ids, subset):
        assert full_by_id[uid] == grp


def test_split_dispatch_simple(monkeypatch=None):
    data = pd.DataFrame({"unit_id": [f"u{i}" for i in range(100)]})
    result = splitter.split(data, "unit_id", {"control": 0.5, "treatment": 0.5}, method="simple", seed=1)
    assert result.salt is None
    assert len(result.group) == 100


def test_split_dispatch_stratified_requires_stratum():
    data = pd.DataFrame({"unit_id": [f"u{i}" for i in range(100)]})
    with pytest.raises(ValueError, match="stratum"):
        splitter.split(data, "unit_id", {"control": 0.5, "treatment": 0.5}, method="stratified", seed=1)


def test_split_dispatch_hash_generates_salt_if_missing():
    data = pd.DataFrame({"unit_id": [f"u{i}" for i in range(100)]})
    result = splitter.split(data, "unit_id", {"control": 0.5, "treatment": 0.5}, method="hash")
    assert result.salt is not None
    assert len(result.salt) > 0


def test_split_dispatch_hash_with_strata_warns():
    data = pd.DataFrame({"unit_id": [f"u{i}" for i in range(100)]})
    stratum = pd.Series(["a"] * 50 + ["b"] * 50)
    result = splitter.split(
        data, "unit_id", {"control": 0.5, "treatment": 0.5}, method="hash", stratum=stratum
    )
    assert any("баланс страт" in w for w in result.warnings)


def test_split_dispatch_unknown_method_raises():
    data = pd.DataFrame({"unit_id": [f"u{i}" for i in range(10)]})
    with pytest.raises(ValueError, match="Неизвестный метод"):
        splitter.split(data, "unit_id", {"control": 0.5, "treatment": 0.5}, method="bogus")
