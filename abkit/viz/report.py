"""Сборка HTML-отчетов через jinja2: report.html (анализ) и design_report.html (дизайн)."""

from __future__ import annotations

import base64
import functools
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jinja2
import pandas as pd
import yaml
from markupsafe import Markup

from abkit import PRODUCT_NAME, __version__ as abkit_version
from abkit.viz.help_texts import get_warning, render_help_html
from abkit.viz.plots import (
    cumulative_lift_plot,
    distribution_plot,
    fig_to_html_div,
    forest_plot,
    p99_clip_stats,
    segment_forest_plot,
)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=jinja2.select_autoescape(["html"]),
)
_env.globals["help_details"] = lambda chart_type, table=False: Markup(
    render_help_html(chart_type, table=table)
)
_env.globals["chart_warning"] = get_warning


@functools.lru_cache(maxsize=1)
def _logo_data_uri() -> str | None:
    """Whale logo (brand п.4), inlined as base64 so report.html/design_report.html
    stay single self-contained files — reports get emailed/shared as one .html,
    an external <img src> would break as soon as it leaves the machine that
    generated it. None if the asset is missing (report still renders, just
    without the logo in the header) rather than failing the whole report."""
    logo_path = _TEMPLATES_DIR / "logo.png"
    if not logo_path.exists():
        return None
    encoded = base64.b64encode(logo_path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"

# Column-header tooltips for the detailed results table (UX package, 5.2) —
# keep the wording in sync with the React copy,
# frontend/src/pages/experiment/DetailedResultsTable.tsx.
DETAILED_COLUMN_TOOLTIPS: dict[str, str] = {
    "Effect (abs.)": "Absolute difference in metric units (test − control)",
    "Lift %": "Relative effect: (test − control) / control",
    "95% CI of lift": "Confidence interval of the relative effect (lift), not of the metric itself",
    "p-value (adj.)": (
        "p-value adjusted for multiple comparisons (see Correction). Decision is made on "
        "this value. Equals raw p-value when there is only one primary hypothesis"
    ),
}


def render_analysis_report(results: Any, context: dict[str, Any]) -> str:
    """Строит report.html: 8 секций из DESIGN.md (раздел 8)."""
    config = context["config"]
    control_name = context["control_name"]

    raw_values: dict = context.get("raw_values", {})
    segment_results: dict = context.get("segment_results", {})
    daily_results: dict = context.get("daily_results", {})

    metrics_by_name = {m.name: m for m in config.metrics}
    first_fig = True
    metric_sections = []

    for metric_name in results.metrics:
        metric_results = results[metric_name]
        metric_config = metrics_by_name.get(metric_name)
        role = metric_config.role if metric_config else "primary"

        forest_html = fig_to_html_div(
            forest_plot(metric_results, title=f"{metric_name}: forest plot"), include_js=first_fig
        )
        first_fig = False

        distribution_htmls = []
        metric_raw = raw_values.get(metric_name, {})
        for treat_name, treat_series in metric_raw.items():
            if treat_name == control_name:
                continue
            control_series = metric_raw.get(control_name)
            if control_series is None:
                continue
            metric_type = metric_config.type if metric_config else "continuous"
            fig = distribution_plot(
                control_series,
                treat_series,
                metric_name=metric_name,
                metric_type=metric_type,
                control_name=control_name,
                treat_name=treat_name,
            )
            caption = None
            if metric_type != "binary":
                combined = pd.concat([control_series.dropna(), treat_series.dropna()])
                threshold, n_above, pct_above = p99_clip_stats(combined)
                if n_above > 0:
                    caption = (
                        f"For clarity the axis is clipped at the 99th percentile ({threshold:.4g}). "
                        f"{n_above} observations ({pct_above:.1f}%) above the threshold are "
                        "collected into the last bin."
                    )
            distribution_htmls.append((treat_name, fig_to_html_div(fig), caption))

        segment_htmls = []
        for treat_name, seg_list in segment_results.get(metric_name, {}).items():
            if not seg_list:
                continue
            fig = segment_forest_plot(
                seg_list, title=f"{metric_name} by stratum: {control_name} vs {treat_name}"
            )
            segment_htmls.append((treat_name, fig_to_html_div(fig)))

        daily_htmls = []
        for treat_name, daily_df in daily_results.get(metric_name, {}).items():
            if daily_df is None or daily_df.empty:
                continue
            fig = cumulative_lift_plot(
                daily_df, title=f"{metric_name}: cumulative lift {control_name} vs {treat_name}"
            )
            daily_htmls.append((treat_name, fig_to_html_div(fig)))

        verdicts = {
            r.treatment_group: results.verdict(metric_name, treatment_group=r.treatment_group)
            for r in metric_results
            if r.is_designed_method
        }

        metric_sections.append(
            dict(
                name=metric_name,
                role=role,
                type=metric_config.type if metric_config else "continuous",
                forest_html=forest_html,
                distribution_htmls=distribution_htmls,
                segment_htmls=segment_htmls,
                daily_htmls=daily_htmls,
                verdicts=verdicts,
                results=metric_results,
            )
        )

    detailed_rows = results.detailed_display_rows(control_name)
    detailed_columns = list(detailed_rows[0].keys()) if detailed_rows else []
    # detailed_display_rows() no longer carries a "Designed" column (UX
    # package, 5.1) — the designed-method row is still bolded, using the
    # flag from the internal (non-display) detailed_rows(), same order.
    detailed_designed_flags = [row["designed"] for row in results.detailed_rows(control_name)]

    template = _env.get_template("report.html.j2")
    return template.render(
        experiment_name=context["experiment_name"],
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        config=config,
        control_name=control_name,
        group_sizes=context["group_sizes"],
        srm=context["srm"],
        loss=context["loss"],
        correction=context["correction"],
        global_warnings=results.global_warnings,
        metric_sections=metric_sections,
        detailed_columns=detailed_columns,
        detailed_rows=detailed_rows,
        detailed_designed_flags=detailed_designed_flags,
        detailed_column_tooltips=DETAILED_COLUMN_TOOLTIPS,
        abkit_version=abkit_version,
        product_name=PRODUCT_NAME,
        logo_data_uri=_logo_data_uri(),
        seed=config.seed,
        config_yaml=yaml.safe_dump(config.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
    )


def render_design_report(experiment: Any) -> str:
    """Строит design_report.html: упрощенный вариант (доступность, MDE, баланс, SRM, pre-A/A)."""
    config = experiment.config
    report = experiment.report

    power_rows = [
        dict(
            metric=name,
            mde_rel=pr.mde_rel,
            mde_rel_cuped=pr.mde_rel_cuped,
            sample_size=pr.sample_size_per_group,
            rho=pr.rho,
            warnings=pr.warnings,
        )
        for name, pr in report.power_results.items()
    ]

    nan_pool = report.n_available + report.n_dropped_for_nan_strata
    strata_nan_rows = [
        dict(
            column=col,
            count=count,
            pct=(count / nan_pool * 100) if nan_pool else 0.0,
        )
        for col, count in report.strata_nan_counts.items()
        if count > 0
    ]

    template = _env.get_template("design_report.html.j2")
    return template.render(
        experiment_name=config.name,
        config=config,
        n_candidates_total=report.n_candidates_total,
        n_excluded_by_isolation=report.n_excluded_by_isolation,
        n_available=report.n_available,
        excluded_by_experiment=report.excluded_by_experiment,
        group_sizes=report.group_sizes,
        power_rows=power_rows,
        srm=report.srm,
        strata_balance=report.strata_balance,
        pre_period_aa=report.pre_period_aa,
        strata_nan_rows=strata_nan_rows,
        n_dropped_for_nan_strata=report.n_dropped_for_nan_strata,
        nan_strategy=config.nan_strategy,
        warnings=report.warnings,
        abkit_version=abkit_version,
        product_name=PRODUCT_NAME,
        logo_data_uri=_logo_data_uri(),
        seed=config.seed,
    )
