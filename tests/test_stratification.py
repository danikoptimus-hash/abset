import numpy as np
import pandas as pd

from abkit.design.stratification import build_strata, nan_counts_by_column


def test_no_strata_cols_returns_single_bucket():
    data = pd.DataFrame({"x": range(10)})
    stratum = build_strata(data, strata_cols=[])
    assert (stratum == "_all_").all()


def test_categorical_strata_used_directly():
    data = pd.DataFrame(
        {"platform": ["ios"] * 30 + ["android"] * 30, "unit_id": range(60)}
    )
    stratum = build_strata(data, strata_cols=["platform"], min_stratum_size=5)
    assert set(stratum.unique()) == {"ios", "android"}
    assert (stratum[data["platform"] == "ios"] == "ios").all()


def test_continuous_strata_bucketed_into_quantiles():
    rng = np.random.default_rng(0)
    data = pd.DataFrame({"age": rng.uniform(18, 80, size=400)})
    stratum = build_strata(data, strata_cols=["age"], n_buckets_continuous=4, min_stratum_size=5)
    assert stratum.nunique() <= 4


def test_cartesian_product_of_multiple_strata():
    data = pd.DataFrame(
        {
            "platform": ["ios", "android"] * 50,
            "country": (["us"] * 50 + ["de"] * 50),
        }
    )
    stratum = build_strata(data, strata_cols=["platform", "country"], min_stratum_size=5)
    assert stratum.nunique() <= 4


def test_small_strata_merged_into_other():
    data = pd.DataFrame({"platform": ["ios"] * 95 + ["rare"] * 5})
    stratum = build_strata(data, strata_cols=["platform"], min_stratum_size=20)
    assert "_other_" in stratum.unique()
    assert (stratum[data["platform"] == "rare"] == "_other_").all()
    assert (stratum[data["platform"] == "ios"] == "ios").all()


def test_no_small_strata_no_other_bucket():
    data = pd.DataFrame({"platform": ["ios"] * 50 + ["android"] * 50})
    stratum = build_strata(data, strata_cols=["platform"], min_stratum_size=20)
    assert "_other_" not in stratum.unique()


def test_nan_in_categorical_strata_becomes_unknown_stratum():
    data = pd.DataFrame({"gender": ["male"] * 40 + ["female"] * 40 + [None] * 20})
    stratum = build_strata(data, strata_cols=["gender"], min_stratum_size=5)
    assert set(stratum.unique()) == {"male", "female", "unknown"}
    assert (stratum[data["gender"].isna()] == "unknown").all()
    assert (stratum[data["gender"] == "male"] == "male").all()


def test_nan_does_not_raise_and_does_not_produce_literal_nan_string():
    data = pd.DataFrame({"platform": ["ios"] * 30 + [np.nan] * 30 + ["android"] * 30})
    stratum = build_strata(data, strata_cols=["platform"], min_stratum_size=5)
    assert "nan" not in stratum.unique()
    assert "unknown" in stratum.unique()


def test_nan_in_combined_strata_marks_only_affected_column_unknown():
    data = pd.DataFrame(
        {
            "platform": ["ios"] * 30 + [None] * 30 + ["android"] * 30,
            "country": ["us"] * 90,
        }
    )
    stratum = build_strata(data, strata_cols=["platform", "country"], min_stratum_size=5)
    assert set(stratum.unique()) == {"ios|us", "unknown|us", "android|us"}


def test_small_unknown_stratum_merged_into_other():
    data = pd.DataFrame({"platform": ["ios"] * 95 + [None] * 5})
    stratum = build_strata(data, strata_cols=["platform"], min_stratum_size=20)
    assert "_other_" in stratum.unique()
    assert (stratum[data["platform"].isna()] == "_other_").all()


def test_nan_in_continuous_strata_becomes_unknown():
    rng = np.random.default_rng(1)
    values = rng.uniform(18, 80, size=400)
    values[:20] = np.nan
    data = pd.DataFrame({"age": values})
    stratum = build_strata(data, strata_cols=["age"], n_buckets_continuous=4, min_stratum_size=5)
    assert (stratum[pd.isna(data["age"])] == "unknown").all()


def test_nan_counts_by_column():
    data = pd.DataFrame(
        {
            "gender": ["male", None, "female", None],
            "platform": ["ios", "android", None, "ios"],
        }
    )
    counts = nan_counts_by_column(data, ["gender", "platform"])
    assert counts == {"gender": 2, "platform": 1}
