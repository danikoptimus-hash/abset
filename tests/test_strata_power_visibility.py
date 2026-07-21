"""Visibility package: the strata power check is stored at design time (for the
Design tab + analysis report), rendered in both HTML reports, collapsible past
12 strata, and absent for external-split experiments."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import Experiment


def _design(tmp_path, n_strata, n_per=40):
    rng = np.random.default_rng(n_strata)
    rows = []
    for i in range(n_strata):
        seg = f"s{i:02d}"  # string → categorical → one stratum per value
        for _ in range(n_per):
            rows.append({"user_id": f"u{len(rows)}", "revenue": float(rng.normal(100, 20)), "seg": seg})
    df = pd.DataFrame(rows)
    cfg = DesignConfig(
        name=f"sp{n_strata}", unit_col="user_id", groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous")],
        strata=["seg"], sample_size=len(rows), split_method="stratified", seed=0,
    )
    return Experiment.design(cfg, df, experiments_dir=Path(tmp_path))


def _power_section(html):
    m = re.search(r'<section id="section-strata-power">(.*?)</section>', html, re.DOTALL)
    return m.group(1) if m else None


def test_design_stores_strata_power_in_computed(tmp_path):
    exp = _design(tmp_path, 5)
    sp = exp.config.computed["strata_power"]
    assert "seg" in sp
    assert len(sp["seg"]) == 5  # one row per stratum value (single metric/group)
    assert all(set(r) >= {"stratum", "n_control", "n_treatment", "mde_rel", "mde_rel_cuped", "status"} for r in sp["seg"])


def test_design_report_has_power_check_section(tmp_path):
    html = (_design(tmp_path, 5).path / "design_report.html").read_text(encoding="utf-8")
    section = _power_section(html)
    assert section is not None
    assert "Strata power check: 5 strata" in section
    assert "By seg" in section
    assert "revenue" in section


def test_power_check_details_open_for_few_strata(tmp_path):
    html = (_design(tmp_path, 11).path / "design_report.html").read_text(encoding="utf-8")
    section = _power_section(html)
    assert "Strata power check: 11 strata" in section
    assert 'class="strata-details" open>' in section


def test_power_check_details_collapsed_for_many_strata(tmp_path):
    html = (_design(tmp_path, 13).path / "design_report.html").read_text(encoding="utf-8")
    section = _power_section(html)
    assert "Strata power check: 13 strata" in section
    # collapsed: the details has no `open` attribute
    assert 'class="strata-details">' in section
    assert 'class="strata-details" open>' not in section


def test_analysis_report_includes_design_power_check(tmp_path):
    exp = _design(tmp_path, 4)
    rng = np.random.default_rng(1)
    n = len(exp.assignments)
    post = pd.DataFrame({
        "user_id": exp.assignments["unit_id"].to_numpy(),
        "revenue": rng.normal(102, 20, n),
    })
    html = exp.analyze(post).report().read_text(encoding="utf-8")
    section = _power_section(html)
    assert section is not None
    assert "Strata power check: 4 strata" in section


def test_external_split_has_no_power_check(tmp_path):
    cfg = DesignConfig(
        name="ext_sp", unit_col="", groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="conversion", type="binary")],
        split_source="external", isolation="off", strata=["country"],
    )
    exp = Experiment.design_external(cfg, experiments_dir=Path(tmp_path))
    # External design stores no power/MDE — nothing to source the table from.
    assert not (exp.config.computed or {}).get("strata_power")

    rng = np.random.default_rng(0)
    rows = []
    for country in ("US", "UK"):
        for _ in range(60):
            rows.append({"variant": "A", "conversion": int(rng.binomial(1, 0.1)), "country": country})
            rows.append({"variant": "B", "conversion": int(rng.binomial(1, 0.2)), "country": country})
    data = pd.DataFrame(rows)
    html = exp.analyze(
        data, correction="none", group_column="variant",
        group_mapping={"A": "control", "B": "treatment"},
    ).report().read_text(encoding="utf-8")
    assert _power_section(html) is None
    assert "Strata power check" not in html
