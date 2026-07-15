import json

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import Experiment

_REPORT_SECTION_IDS = [
    "section-header",
    "section-verdicts",
    "section-full-results-table",
    "section-forest",
    "section-distributions",
    "section-segments",
    "section-cumulative",
    "section-diagnostics",
]

_DESIGN_REPORT_SECTION_IDS = [
    "section-availability",
    "section-groups",
    "section-power",
    "section-strata-balance",
    "section-srm",
    "section-pre-aa",
    "section-appendix",
]


def _demo_design(tmp_path, n=3000, strata=None, seed=1, group_descriptions=None):
    rng = np.random.default_rng(seed)
    design_data = pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "platform": rng.choice(["ios", "android"], size=n),
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.10, size=n),
            "orders": rng.integers(0, 5, size=n),
            "sessions": rng.integers(1, 10, size=n),
        }
    )
    config = DesignConfig(
        name="viz_exp",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        group_descriptions=group_descriptions or {},
        metrics=[
            MetricConfig(name="revenue", type="continuous"),
            MetricConfig(name="clicks", type="binary", role="secondary"),
            MetricConfig(name="conv", type="ratio", num="orders", den="sessions"),
        ],
        strata=strata if strata is not None else ["platform"],
        sample_size=n,
        split_method="stratified" if strata != [] else "simple",
        seed=seed,
    )
    return Experiment.design(config, design_data, experiments_dir=tmp_path)


def test_design_report_written_and_has_all_sections(tmp_path):
    experiment = _demo_design(tmp_path)
    report_path = experiment.path / "design_report.html"
    assert report_path.exists()
    html = report_path.read_text(encoding="utf-8")
    for section_id in _DESIGN_REPORT_SECTION_IDS:
        assert f'id="{section_id}"' in html, f"Секция {section_id} отсутствует в design_report.html"
    assert experiment.name in html


def test_design_report_has_flow_images_anchor_comments_even_with_no_images(tmp_path):
    """Stage 4: the splice anchor must always be present, even when no
    images exist yet (the common case at design/redesign time) — otherwise
    abkit/jobs.py::_regenerate_design_report has nothing to patch into
    later."""
    experiment = _demo_design(tmp_path)
    html = (experiment.path / "design_report.html").read_text(encoding="utf-8")
    assert "<!-- flow-images-section:start -->" in html
    assert "<!-- flow-images-section:end -->" in html
    assert 'id="section-flows"' not in html


def test_render_flow_images_section_splices_images_into_saved_report(tmp_path):
    from abkit.viz.report import render_flow_images_section

    experiment = _demo_design(tmp_path)
    report_path = experiment.path / "design_report.html"
    original_html = report_path.read_text(encoding="utf-8")

    img_path = tmp_path / "shot.png"
    Image.new("RGB", (20, 20), (255, 0, 0)).save(img_path, format="PNG")
    patched = render_flow_images_section(
        original_html,
        {"control": [{"flow_title": "Existing checkout", "file_path": str(img_path)}]},
    )
    assert 'id="section-flows"' in patched
    assert "Existing checkout" in patched
    assert "data:image/jpeg;base64," in patched
    # Everything outside the spliced block is untouched.
    assert 'id="section-availability"' in patched
    assert experiment.name in patched

    # A second splice with no images removes the section again (not just
    # leaves stale content behind).
    cleared = render_flow_images_section(patched, {})
    assert 'id="section-flows"' not in cleared
    assert "<!-- flow-images-section:start -->" in cleared


def test_render_flow_images_section_handles_grayscale_alpha_source_png(tmp_path):
    """Regression: report embedding always re-encodes to JPEG, which can't
    write "LA" (grayscale + alpha) pixel data directly — an LA-mode PNG
    (valid, and accepted fine at upload time since that step keeps PNGs as
    PNG) used to make _flow_image_data_uri raise internally, silently
    swallowed, and the whole group vanished from the report with no
    error visible anywhere."""
    from abkit.viz.report import render_flow_images_section

    experiment = _demo_design(tmp_path)
    original_html = (experiment.path / "design_report.html").read_text(encoding="utf-8")

    img_path = tmp_path / "shot_la.png"
    Image.new("LA", (20, 20), (128, 200)).save(img_path, format="PNG")
    patched = render_flow_images_section(
        original_html, {"control": [{"flow_title": "", "file_path": str(img_path)}]},
    )
    assert 'id="section-flows"' in patched
    assert "data:image/jpeg;base64," in patched


def test_design_report_shows_created_date(tmp_path):
    experiment = _demo_design(tmp_path)
    html = (experiment.path / "design_report.html").read_text(encoding="utf-8")
    assert "Created " in html


def test_design_report_shows_groups_table_with_descriptions(tmp_path):
    experiment = _demo_design(
        tmp_path,
        group_descriptions={"control": "Existing checkout flow", "treatment": "New one-click checkout"},
    )
    html = (experiment.path / "design_report.html").read_text(encoding="utf-8")
    assert 'id="section-groups"' in html
    assert "Existing checkout flow" in html
    assert "New one-click checkout" in html
    assert "50%" in html


def test_design_report_groups_table_omits_description_column_when_none_set(tmp_path):
    experiment = _demo_design(tmp_path)
    html = (experiment.path / "design_report.html").read_text(encoding="utf-8")
    assert 'id="section-groups"' in html
    assert "<th>Description</th>" not in html


def test_design_report_has_absolute_mde_columns(tmp_path):
    """5-part package pt.2: MDE (abs.) / MDE (abs., CUPED) columns, binary
    metrics in percentage points, continuous in raw units."""
    experiment = _demo_design(tmp_path)
    html = (experiment.path / "design_report.html").read_text(encoding="utf-8")
    assert "MDE (abs.)" in html
    assert "MDE (abs., CUPED)" in html
    assert "abs = rel &#215; baseline" in html or "abs = rel × baseline" in html
    # revenue is continuous (raw units, no "pp" suffix); clicks is binary
    # (percentage points).
    assert "pp</td>" in html


def test_design_report_mde_values_have_three_decimal_places(tmp_path):
    """Item 4.1/4.3: every MDE (rel./abs., with and without CUPED) value in
    the design report is formatted to exactly 3 decimal places. Scoped to
    the Power/MDE section specifically — other tables (group proportions,
    strata NaN %) use their own, unrelated precision."""
    import re

    experiment = _demo_design(tmp_path)
    html = (experiment.path / "design_report.html").read_text(encoding="utf-8")
    power_section = html.split('id="section-power"')[1].split("</section>")[0]
    # "12.345%" — 3 digits after the decimal point, not fewer/more.
    rel_matches = re.findall(r"\d+\.(\d+)%", power_section)
    assert rel_matches, "Expected at least one relative-MDE percentage in the report"
    assert all(len(digits) == 3 for digits in rel_matches)


def test_design_report_power_table_lists_primary_metrics_before_secondary(tmp_path):
    """6-part package pt.3.1: primary-metric rows come before secondary
    ones, regardless of declaration order. _demo_design declares revenue
    (primary), clicks (secondary), conv (primary, ratio) in that order — the
    rendered order must be revenue, conv, clicks (secondary last), and each
    row must carry an explicit primary/secondary role tag."""
    experiment = _demo_design(tmp_path)
    html = (experiment.path / "design_report.html").read_text(encoding="utf-8")
    power_section = html.split('id="section-power"')[1].split("</section>")[0]

    idx_revenue = power_section.index("revenue")
    idx_conv = power_section.index("conv")
    idx_clicks = power_section.index("clicks")
    assert idx_revenue < idx_conv < idx_clicks

    assert power_section.count('<span class="role-tag primary">primary</span>') == 2
    assert power_section.count('<span class="role-tag secondary">secondary</span>') == 1


def test_design_report_required_n_per_group_uses_ceil_not_round(tmp_path):
    """Item 1.1/1.2: the old 'Group size' column is renamed to 'Required n
    per group' and rounds UP (you need AT LEAST this many) — not to
    nearest. groups={control: 0.1}, sample_size=14993 -> n_control=1499.3,
    which ceil rounds to 1500 but ordinary rounding would give 1499 (chosen
    specifically so the two disagree, unlike e.g. x.5)."""
    n = 14993
    rng = np.random.default_rng(3)
    design_data = pd.DataFrame(
        {"user_id": [f"u{i}" for i in range(n)], "revenue": rng.normal(100, 20, size=n)}
    )
    config = DesignConfig(
        name="ceil_check",
        unit_col="user_id",
        groups={"control": 0.1, "treatment": 0.9},
        metrics=[MetricConfig(name="revenue", type="continuous")],
        strata=[],
        sample_size=n,
        split_method="simple",
        seed=3,
    )
    experiment = Experiment.design(config, design_data, experiments_dir=tmp_path)
    pr = experiment.report.power_results["revenue"]
    assert pr.sample_size_per_group == pytest.approx(1499.3)

    html = (experiment.path / "design_report.html").read_text(encoding="utf-8")
    assert "Group size" not in html
    assert "Required n per group" in html
    power_section = html.split('id="section-power"')[1].split("</section>")[0]
    assert "<td>1500</td>" in power_section
    assert "<td>1499</td>" not in power_section


def test_design_report_actual_group_sizes_row_and_shortfall_warning(tmp_path):
    """Item 1.3: below the Power/MDE table, an 'Actual group sizes' line
    grounds the required-n column against the real split by name; a metric
    whose required n exceeds the smallest actual group size gets an
    explicit warning naming it. sample_size=10_000 against only 2000 actual
    candidates guarantees a shortfall (design always splits the full
    candidate pool, never subsamples down to config.sample_size)."""
    n = 2000
    rng = np.random.default_rng(5)
    design_data = pd.DataFrame(
        {"user_id": [f"u{i}" for i in range(n)], "revenue": rng.normal(100, 20, size=n)}
    )
    config = DesignConfig(
        name="shortfall_check",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous")],
        strata=[],
        sample_size=10_000,
        split_method="simple",
        seed=5,
    )
    experiment = Experiment.design(config, design_data, experiments_dir=tmp_path)
    html = (experiment.path / "design_report.html").read_text(encoding="utf-8")
    power_section = html.split('id="section-power"')[1].split("</section>")[0]
    assert "Actual group sizes:" in power_section
    assert "control" in power_section and "treatment" in power_section
    assert "revenue requires" in power_section
    assert "actual is below" in power_section


def test_design_report_flags_mde_exceeding_baseline(tmp_path):
    """Item 2: a metric whose achievable relative MDE exceeds 100% of its
    own baseline gets a soft (non-blocking) warning highlight. n=20 total
    (10 per group), baseline exactly 30% (a FIXED, non-random 6-of-20 split
    — not rng.binomial, so the baseline can't drift with the RNG draw) is
    deliberately underpowered: power.mde_binary(0.3, n_control=10, ...) ~=
    0.58 absolute, i.e. ~193% relative — comfortably over the threshold."""
    n = 20
    design_data = pd.DataFrame(
        {"user_id": [f"u{i}" for i in range(n)], "rare_event": [1] * 6 + [0] * 14}
    )
    config = DesignConfig(
        name="mde_sanity_check",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="rare_event", type="binary")],
        strata=[],
        sample_size=n,
        split_method="simple",
        seed=9,
    )
    experiment = Experiment.design(config, design_data, experiments_dir=tmp_path)
    pr = experiment.report.power_results["rare_event"]
    assert pr.mde_rel is not None and pr.mde_rel > 1

    html = (experiment.path / "design_report.html").read_text(encoding="utf-8")
    power_section = html.split('id="section-power"')[1].split("</section>")[0]
    assert 'class="mde-warn"' in power_section
    assert "unrealistic to detect" in power_section


def test_design_report_shows_strata_info_and_balance_table(tmp_path):
    """6-part package pt.10: explicit "Stratified by: ..." sentence plus a
    per-stratum-per-group balance table (the crosstab was already computed
    for the chi2 test, just never rendered beyond the pass/fail badge)."""
    experiment = _demo_design(tmp_path, strata=["platform"])
    html = (experiment.path / "design_report.html").read_text(encoding="utf-8")
    assert "Stratified by:" in html
    assert "platform" in html
    assert "strata after combination" in html
    assert "min stratum size:" in html
    # ios/android crosstab counts render as an actual table, not just chi2/p
    # (stratum values are row data; group names are the column headers).
    assert "<th>control</th>" in html and "<th>treatment</th>" in html
    assert "<td>ios</td>" in html or "<td>android</td>" in html


def test_design_report_shows_no_stratification_for_unstratified_simple_split(tmp_path):
    experiment = _demo_design(tmp_path, strata=[])
    html = (experiment.path / "design_report.html").read_text(encoding="utf-8")
    assert "No stratification" in html
    assert "Stratified by:" not in html


def test_design_report_opens_offline_no_external_deps(tmp_path):
    experiment = _demo_design(tmp_path)
    html = (experiment.path / "design_report.html").read_text(encoding="utf-8")
    assert "http://" not in html
    assert "https://" not in html


def _demo_post_data(experiment, n, rng, with_date=False):
    assignments = experiment.assignments
    data = pd.DataFrame(
        {
            "user_id": assignments["unit_id"],
            "revenue": rng.normal(100, 20, size=n),
            "clicks": rng.binomial(1, 0.1, size=n),
            "orders": rng.integers(0, 5, size=n),
            "sessions": rng.integers(1, 10, size=n),
        }
    )
    if with_date:
        data["event_date"] = pd.to_datetime("2024-01-01") + pd.to_timedelta(
            rng.integers(0, 14, size=n), unit="D"
        )
    return data


def test_analysis_report_has_all_sections_and_writes_results_json(tmp_path):
    experiment = _demo_design(tmp_path)
    rng = np.random.default_rng(2)
    post_data = _demo_post_data(experiment, len(experiment.assignments), rng, with_date=True)

    results = experiment.analyze(post_data, compare_methods=True, date_col="event_date")
    report_path = results.report()

    assert report_path.exists()
    assert report_path == experiment.path / "report.html"
    html = report_path.read_text(encoding="utf-8")
    for section_id in _REPORT_SECTION_IDS:
        assert f'id="{section_id}"' in html, f"Секция {section_id} отсутствует в report.html"
    # Item 1: Appendix (config_yaml dump + version/seed) removed from the
    # analysis report — the same details are already reachable in the UI.
    assert 'id="section-appendix"' not in html
    assert "Appendix" not in html

    results_json_path = experiment.path / "results.json"
    assert results_json_path.exists()
    payload = json.loads(results_json_path.read_text(encoding="utf-8"))
    assert "results" in payload
    assert len(payload["results"]) > 0

    # Stage 2 item 2.3: no created_at/started_at/completed_at was passed to
    # analyze() here, so the lifecycle-dates line is absent, not blank.
    assert "Created " not in html
    assert "Started " not in html
    assert "Completed " not in html


def test_analysis_report_shows_lifecycle_dates_when_provided(tmp_path):
    import datetime as dt

    experiment = _demo_design(tmp_path)
    rng = np.random.default_rng(2)
    post_data = _demo_post_data(experiment, len(experiment.assignments), rng)

    results = experiment.analyze(
        post_data,
        created_at=dt.datetime(2026, 7, 2, tzinfo=dt.timezone.utc),
        started_at=dt.datetime(2026, 7, 5, tzinfo=dt.timezone.utc),
        completed_at=None,
    )
    html = results.report().read_text(encoding="utf-8")
    assert "Created Jul 2, 2026" in html
    assert "Started Jul 5, 2026" in html
    assert "Completed " not in html


def test_analysis_report_shows_p99_clip_caption_for_continuous_metric(tmp_path):
    """UX10: гистограммы continuous-метрик визуально обрезаны по P99 — под
    графиком должна быть подпись с порогом и числом отсеченных наблюдений."""
    experiment = _demo_design(tmp_path)
    rng = np.random.default_rng(5)
    post_data = _demo_post_data(experiment, len(experiment.assignments), rng)

    results = experiment.analyze(post_data)
    report_path = results.report()
    html = report_path.read_text(encoding="utf-8")

    assert "clip-caption" in html
    assert "99th percentile" in html
    assert "last bin" in html


def test_design_report_embeds_logo_as_inline_base64(tmp_path):
    """Brand п.4: the header logo is inlined (base64), not an external <img
    src> — design_report.html must stay a single self-contained file."""
    experiment = _demo_design(tmp_path)
    html = (experiment.path / "design_report.html").read_text(encoding="utf-8")
    assert 'class="report-brand"' in html
    assert '<img src="data:image/png;base64,' in html


def test_analysis_report_opens_offline_plotly_inline(tmp_path):
    experiment = _demo_design(tmp_path)
    rng = np.random.default_rng(3)
    post_data = _demo_post_data(experiment, len(experiment.assignments), rng)

    results = experiment.analyze(post_data)
    report_path = results.report()
    html = report_path.read_text(encoding="utf-8")

    # библиотека должна быть встроена целиком, а не подгружаться скриптом с CDN
    assert '<script src="https://cdn.plot.ly' not in html
    assert '<script src="https://cdnjs' not in html
    assert "Plotly.newPlot" in html  # встроенный plotly.js реально присутствует


def test_analysis_report_embeds_logo_as_inline_base64(tmp_path):
    experiment = _demo_design(tmp_path)
    rng = np.random.default_rng(3)
    post_data = _demo_post_data(experiment, len(experiment.assignments), rng)
    results = experiment.analyze(post_data)
    html = results.report().read_text(encoding="utf-8")
    assert 'class="report-brand"' in html
    assert '<img src="data:image/png;base64,' in html


def test_analysis_report_shows_cumulative_lift_when_date_col_given(tmp_path):
    experiment = _demo_design(tmp_path)
    rng = np.random.default_rng(4)
    post_data = _demo_post_data(experiment, len(experiment.assignments), rng, with_date=True)

    results = experiment.analyze(post_data, date_col="event_date")
    html = results.report().read_text(encoding="utf-8")
    assert "No date column was passed" not in html


def test_analysis_report_placeholder_when_no_date_col(tmp_path):
    experiment = _demo_design(tmp_path)
    rng = np.random.default_rng(5)
    post_data = _demo_post_data(experiment, len(experiment.assignments), rng, with_date=False)

    results = experiment.analyze(post_data)
    html = results.report().read_text(encoding="utf-8")
    assert "No date column was passed" in html


def test_analysis_report_shows_segments_when_strata_present(tmp_path):
    experiment = _demo_design(tmp_path, strata=["platform"])
    rng = np.random.default_rng(6)
    post_data = _demo_post_data(experiment, len(experiment.assignments), rng)

    results = experiment.analyze(post_data)
    html = results.report().read_text(encoding="utf-8")
    assert "Not enough strata" not in html


def test_analysis_report_placeholder_when_no_strata(tmp_path):
    experiment = _demo_design(tmp_path, strata=[])
    rng = np.random.default_rng(7)
    post_data = _demo_post_data(experiment, len(experiment.assignments), rng)

    results = experiment.analyze(post_data)
    html = results.report().read_text(encoding="utf-8")
    assert "Not enough strata" in html


def test_analysis_report_has_help_expanders_for_all_chart_types(tmp_path):
    """Каждый график/таблица в отчете должен сопровождаться свернутым
    <details><summary>❓ Как читать...?</summary> — структура не должна ломаться
    (валидный HTML: количество <details> == количеству </details>)."""
    experiment = _demo_design(tmp_path)
    rng = np.random.default_rng(20)
    post_data = _demo_post_data(experiment, len(experiment.assignments), rng, with_date=True)

    results = experiment.analyze(post_data, date_col="event_date")
    html = results.report().read_text(encoding="utf-8")

    assert html.count("<details>") == html.count("</details>")
    assert html.count("<details>") > 0
    assert "❓ How do I read this chart?" in html
    assert "❓ How do I read this table?" in html
    # текст помощи для binary-метрики (bar chart) должен быть про Wilson-ДИ, а не про гистограмму
    assert "Wilson score interval" in html
    assert "post-hoc diagnostics" in html  # предупреждение про peeking над cumulative
    assert "Segment breakdowns" in html  # предупреждение над segment-графиками


def test_design_report_has_mde_table_help_expander(tmp_path):
    experiment = _demo_design(tmp_path)
    html = (experiment.path / "design_report.html").read_text(encoding="utf-8")
    assert html.count("<details>") == html.count("</details>")
    assert "❓ How do I read this table?" in html


def test_report_raises_without_context_when_constructed_directly():
    from abkit.analysis.results import AnalysisResults

    results = AnalysisResults([])
    with pytest.raises(RuntimeError, match="is not attached"):
        results.report()


def test_report_can_target_custom_path(tmp_path):
    experiment = _demo_design(tmp_path)
    rng = np.random.default_rng(8)
    post_data = _demo_post_data(experiment, len(experiment.assignments), rng)

    results = experiment.analyze(post_data)
    custom_dir = tmp_path / "custom_report_dir"
    custom_dir.mkdir()
    report_path = results.report(path=custom_dir)
    assert report_path == custom_dir / "report.html"
    assert report_path.exists()
