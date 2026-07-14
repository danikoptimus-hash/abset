import math

import numpy as np
import pandas as pd
import pytest

from abkit import storage
from abkit.auth.guards import AuthError, CurrentUser
from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import Experiment
from abkit.jobs import preview_sample_size


def _user(role="editor"):
    return CurrentUser(id="00000000-0000-0000-0000-000000000001", email="e@co.com", name="E", role=role)


def make_data(n=200_000, p=0.17, seed=1):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "converted": rng.binomial(1, p, size=n),
        }
    )


def test_preview_requires_editor(tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    data = make_data(n=100)
    with pytest.raises(AuthError):
        preview_sample_size(
            _user(role="viewer"), data, unit_col="user_id", group_names=["control", "treatment"],
            metrics=[MetricConfig(name="converted", type="binary")], alpha=0.05, power_=0.8,
            mde=0.05, isolation_mode="off", exclude_experiments="all_active",
            isolation_selected_experiments=[], experiment_name=None,
        )


def test_preview_requires_at_least_two_groups(tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    data = make_data(n=100)
    with pytest.raises(storage.StorageError):
        preview_sample_size(
            _user(), data, unit_col="user_id", group_names=["control"],
            metrics=[MetricConfig(name="converted", type="binary")], alpha=0.05, power_=0.8,
            mde=0.05, isolation_mode="off", exclude_experiments="all_active",
            isolation_selected_experiments=[], experiment_name=None,
        )


def test_preview_no_mde_returns_eligible_n_only(tmp_path, monkeypatch):
    """sizeMode 'all'/'sample_size' in the wizard — no MDE target, so the
    preview can still say how many candidates are eligible after isolation
    without pretending to know a required size."""
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    data = make_data(n=500)
    result = preview_sample_size(
        _user(), data, unit_col="user_id", group_names=["control", "treatment"],
        metrics=[MetricConfig(name="converted", type="binary")], alpha=0.05, power_=0.8,
        mde=None, isolation_mode="off", exclude_experiments="all_active",
        isolation_selected_experiments=[], experiment_name=None,
    )
    assert result["eligible_n"] == 500
    assert result["required_n_per_group"] is None
    assert result["per_metric"] == []


def test_preview_matches_direct_power_calc_for_equal_split(tmp_path, monkeypatch):
    """Sanity-checks the whole preview against the exact number a real
    design() would compute for an equal 50/50 split — an equal split always
    gives the power formula a treatment/control ratio of 1 regardless of
    group count, so the equal-groups assumption this preview makes (item 3
    docstring, abkit/jobs.py::preview_sample_size) must match EXACTLY, not
    just approximately."""
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    data = make_data(n=200_000, p=0.17)
    abs_mde = 0.01
    actual_baseline = float(data["converted"].mean())
    rel_mde = abs_mde / actual_baseline

    result = preview_sample_size(
        _user(), data, unit_col="user_id", group_names=["control", "treatment"],
        metrics=[MetricConfig(name="converted", type="binary")], alpha=0.05, power_=0.8,
        mde=rel_mde, isolation_mode="off", exclude_experiments="all_active",
        isolation_selected_experiments=[], experiment_name=None,
    )
    assert result["eligible_n"] == 200_000

    config = DesignConfig(
        name="direct", unit_col="user_id", groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="converted", type="binary")], alpha=0.05, power=0.8, mde=rel_mde,
    )
    experiment = Experiment.design(config, data, experiments_dir=tmp_path / "direct")
    direct_n = experiment.report.power_results["converted"].sample_size_per_group
    assert result["required_n_per_group"] == math.ceil(direct_n)
    assert result["per_metric"][0]["warnings"] == []


def test_preview_ignores_secondary_metrics_for_required_n(tmp_path, monkeypatch):
    """required_n_per_group is the max across PRIMARY metrics only — a
    secondary metric needing more data (here, a much rarer event) doesn't
    inflate the headline number, but still shows up in per_metric for the
    user to see."""
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    rng = np.random.default_rng(4)
    n = 200_000
    data = pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "converted": rng.binomial(1, 0.5, size=n),  # common — small required n
            "rare_event": rng.binomial(1, 0.01, size=n),  # rare — large required n
        }
    )
    result = preview_sample_size(
        _user(), data, unit_col="user_id",
        group_names=["control", "treatment"],
        metrics=[
            MetricConfig(name="converted", type="binary", role="primary"),
            MetricConfig(name="rare_event", type="binary", role="secondary"),
        ],
        alpha=0.05, power_=0.8, mde=0.5, isolation_mode="off", exclude_experiments="all_active",
        isolation_selected_experiments=[], experiment_name=None,
    )
    per_metric_by_name = {m["metric"]: m for m in result["per_metric"]}
    assert per_metric_by_name["rare_event"]["required_n_per_group"] > per_metric_by_name["converted"]["required_n_per_group"]
    # The secondary's much larger requirement must NOT win — only "converted"
    # (primary) determines the headline number.
    assert result["required_n_per_group"] == per_metric_by_name["converted"]["required_n_per_group"]


def test_preview_excludes_units_occupied_by_other_active_experiment(tmp_path, monkeypatch):
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    other_data = make_data(n=100, seed=2)
    other_config = DesignConfig(
        name="other_exp", unit_col="user_id", groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="converted", type="binary")],
    )
    other_experiment = Experiment.design(other_config, other_data, experiments_dir=tmp_path)
    occupied_ids = list(other_experiment.assignments["unit_id"])[:20]

    # Distinct id prefix from other_data's "u{i}" — otherwise the new
    # dataset's own first 100 rows would naturally coincide with ALL of
    # other_exp's (fully-assigned) candidates, not just the 20 explicitly
    # overwritten below (found the hard way: this test originally asserted
    # 980 and got 900).
    data = make_data(n=1000, seed=3)
    data["user_id"] = [f"main{i}" for i in range(len(data))]
    data.loc[:19, "user_id"] = occupied_ids

    result = preview_sample_size(
        _user(), data, unit_col="user_id", group_names=["control", "treatment"],
        metrics=[MetricConfig(name="converted", type="binary")], alpha=0.05, power_=0.8,
        mde=None, isolation_mode="exclude", exclude_experiments="all_active",
        isolation_selected_experiments=[], experiment_name="new_exp",
    )
    assert result["eligible_n"] == 980
