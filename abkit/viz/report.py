"""Сборка HTML-отчетов через jinja2: report.html (анализ) и design_report.html (дизайн)."""

from __future__ import annotations

import base64
import functools
import io
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jinja2
import pandas as pd
from markupsafe import Markup
from PIL import Image

from abkit import PRODUCT_NAME, __version__ as abkit_version, checks
from abkit.config import metric_labels_by_name
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


def _format_report_date(dt: datetime | None) -> str | None:
    """Stage 2 report-header dates — static HTML has no hover/local-tz
    conversion (unlike the frontend's RelativeTime), so this shows the full
    absolute UTC date once, upfront. %-d isn't portable (Windows strftime),
    hence the explicit zero-strip instead."""
    if dt is None:
        return None
    return dt.strftime("%b %d, %Y").replace(" 0", " ")


# Stage 4 (variant flow images) report-embed width — deliberately smaller
# than the on-disk copy (abkit/flow_images.py caps uploads at 1600px, for
# the app's own thumbnail+lightbox use) since design_report.html needs to
# stay a reasonably-sized self-contained file with potentially many images
# across several groups; re-encoded at request time, never written back to
# the stored file.
_REPORT_IMAGE_MAX_WIDTH = 900


def _flow_image_data_uri(file_path: Path) -> str | None:
    """Same self-contained-report rationale as _logo_data_uri, but resized/
    re-compressed per image (not lru_cache'd — these are per-experiment user
    files, not one static bundled asset) rather than embedded as-is."""
    if not file_path.exists():
        return None
    try:
        with Image.open(file_path) as img:
            img.load()
            # JPEG only writes "L" (grayscale) and "RGB" directly — anything
            # else (RGBA/LA/P/1/CMYK/...) must be flattened first, or
            # Image.save raises (e.g. "cannot write mode LA as JPEG", hit by
            # a grayscale+alpha PNG that had sailed through upload-time
            # validation fine since that only converts ahead of a JPEG
            # SOURCE, not every mode this JPEG-only report re-encode sees).
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            if img.width > _REPORT_IMAGE_MAX_WIDTH:
                ratio = _REPORT_IMAGE_MAX_WIDTH / img.width
                img = img.resize((_REPORT_IMAGE_MAX_WIDTH, max(1, round(img.height * ratio))), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
    except Exception:
        # A missing/corrupted stored file shouldn't fail the whole report —
        # same "degrade, don't crash" choice as the missing-logo case above.
        return None
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _build_flow_image_groups(
    flow_images: dict[str, list[dict[str, Any]]] | None,
) -> list[dict[str, Any]]:
    groups = []
    for group_name, images in (flow_images or {}).items():
        data_uris = [
            uri
            for uri in (_flow_image_data_uri(Path(img["file_path"])) for img in images)
            if uri is not None
        ]
        if data_uris:
            groups.append(
                dict(group_name=group_name, flow_title=images[0].get("flow_title") or "", data_uris=data_uris)
            )
    return groups


_FLOW_IMAGES_SECTION_RE = re.compile(
    r"<!-- flow-images-section:start -->.*?<!-- flow-images-section:end -->", re.DOTALL
)


def render_flow_images_section(design_report_html: str, flow_images: dict[str, list[dict[str, Any]]]) -> str:
    """Patches an ALREADY-SAVED design_report.html in place, replacing
    everything between templates/_flow_images_section.html.j2's own
    flow-images-section:start/:end HTML comments (present in every
    design_report.html, empty or not, since that partial is always
    included) with a freshly rendered version — see
    abkit/jobs.py::_regenerate_design_report for why this splices instead
    of doing a full render_design_report() re-render."""
    template = _env.get_template("_flow_images_section.html.j2")
    rendered = template.render(flow_image_groups=_build_flow_image_groups(flow_images))
    new_html, n = _FLOW_IMAGES_SECTION_RE.subn(rendered.strip(), design_report_html)
    if n == 0:
        # Report predates this feature (no anchor comments at all) — nothing
        # safe to splice into, leave the file untouched rather than guess.
        return design_report_html
    return new_html


_DESIGN_CONTEXT_SECTION_RE = re.compile(
    r"<!-- design-context-section:start -->.*?<!-- design-context-section:end -->", re.DOTALL
)


def render_design_context_section(
    design_report_html: str,
    hypothesis: str | None,
    planned_end_date: Any = None,
) -> str:
    """Items C2/B2 — впечатывает секции Hypothesis и Planned end в УЖЕ
    СОХРАНЕННЫЙ design_report.html, между якорями
    templates/_design_context_section.html.j2.

    Зачем сплайс, а не обычный рендер: design_report.html пишется один раз,
    внутри Experiment.design(), а к этому моменту НИ ОДНОГО из двух значений
    еще не существует — гипотезу визард сохраняет отдельным вызовом ПОСЛЕ
    успешного дизайна (Step4Review.tsx::saveHypothesis), плановую дату
    проставляет run_design в колонку строки эксперимента уже после того, как
    отчет записан на диск. Обе потом еще и редактируемы. Полный ре-рендер тут
    невозможен по той же причине, что и у флоу-картинок: для него нужен объект
    DesignReport, живущий только внутри Experiment.design() и не восстановимый
    через Experiment.load(). Точная копия механики
    render_flow_images_section().

    ВАЖНО: функция всегда перерисовывает секцию ЦЕЛИКОМ из обоих переданных
    значений — вызывающий обязан передать актуальные оба, а не только то, что
    поменялось, иначе второе будет стерто.

    Отчет без якорей (сделан до этой фичи) остается нетронутым — вставлять
    вслепую некуда, и молча дописать секцию в произвольное место хуже, чем не
    трогать файл.
    """
    template = _env.get_template("_design_context_section.html.j2")
    rendered = template.render(
        hypothesis=hypothesis, planned_end_date=_format_report_date(planned_end_date)
    )
    new_html, n = _DESIGN_CONTEXT_SECTION_RE.subn(rendered.strip(), design_report_html)
    return design_report_html if n == 0 else new_html


def _lifecycle_dates(context: dict[str, Any]) -> list[tuple[str, str]]:
    labels = (("created_at", "Created"), ("started_at", "Started"), ("completed_at", "Completed"))
    out = []
    for key, label in labels:
        formatted = _format_report_date(context.get(key))
        if formatted is not None:
            out.append((label, formatted))
    # Item B2: плановая дата окончания в шапке ОБОИХ отчетов, рядом с
    # фактическими датами. Отдельной веткой, а не в labels выше: это date, а не
    # datetime, и _format_report_date ждет datetime.strftime — у date он тоже
    # есть, но смысл поля другой (план, а не факт), поэтому и подпись явная.
    planned_end = context.get("planned_end_date")
    if planned_end is not None:
        out.append(("Planned end", _format_report_date(planned_end)))
    return out


# Item C3 — одна формулировка исхода изоляции на оба отчета и на Design tab
# (TS-двойник: frontend/src/pages/experiment/isolationDisclosure.ts). Текст
# ровно тот, что перечисляет ТЗ: "excluded N overlapping users" / "proceeded
# despite N overlapping users" / "no overlap".
def isolation_disclosure(computed: dict[str, Any] | None) -> dict[str, Any] | None:
    """{"text": str, "level": "ok"|"warn", "by_experiment": {...}} или None,
    если эксперимент вообще не хранит сведений об изоляции (дизайны до этой
    фичи, external-сплит) — тогда секции просто нет, вместо того чтобы
    утверждать "пересечения не было", чего мы не знаем."""
    if not computed:
        return None
    decision_raw = computed.get("isolation_decision")
    if not decision_raw:
        # Дизайн до item C3: решения не записывали, но САМИ ЧИСЛА (сколько
        # исключено, по каким экспериментам) в computed были всегда — из них
        # исход восстанавливается однозначно, и старые отчеты тоже получают
        # честную строку вместо пустоты.
        by_experiment = computed.get("excluded_by_experiment") or {}
        n_excluded = computed.get("n_excluded_by_isolation") or 0
        if not by_experiment:
            return None
        decision_raw = {
            "decision": "excluded" if n_excluded else "proceeded",
            "n_overlap": int(n_excluded or sum(by_experiment.values())),
            "by_experiment": by_experiment,
        }
    decision = decision_raw.get("decision")
    n = int(decision_raw.get("n_overlap") or 0)
    by_experiment = decision_raw.get("by_experiment") or {}
    if decision == "excluded":
        text = f"Excluded {n} overlapping users from other active experiments."
        level = "ok"
    elif decision == "proceeded":
        text = (
            f"Proceeded despite {n} overlapping users also enrolled in other active "
            "experiments — their exposure to more than one test may confound the results."
        )
        level = "warn"
    else:
        text = "No overlap with other active experiments."
        level = "ok"
    return {"text": text, "level": level, "by_experiment": by_experiment, "decision": decision}
_env.globals["chart_warning"] = get_warning


def format_stratum_mde_abs(row: dict[str, Any]) -> str:
    """Item C4 — абсолютный MDE одной строки strata power в тех же единицах и с
    той же точностью, что и общая таблица MDE (Design tab's formatAbs /
    design_report's power table): binary — в процентных пунктах ("1.234 pp",
    потому что baseline сам по себе доля и сырые единицы дали бы 0.0123),
    continuous — в единицах метрики.

    Прочерк, а не пустота, когда: строка сохранена до item C4 (ключа нет),
    страта слишком мала (mde_abs не считался) или тип метрики неизвестен —
    "—" честно читается как "нечего показать", в отличие от "0.000".
    """
    value = row.get("mde_abs")
    if value is None:
        return "—"
    if row.get("metric_type") == "binary":
        return f"{value * 100:.3f} pp"
    return f"{value:.3f}"


_env.globals["stratum_mde_abs"] = format_stratum_mde_abs


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
    "CUPED rho": (
        "Correlation between metric and its pre-period covariate; variance reduction ≈ rho²"
    ),
    "Variance reduction": (
        "How much lower the effect estimate's variance is versus the raw (untreated) data — "
        "from CUPED, outlier removal, or post-stratification. Blank (—) when the method uses "
        "none of these techniques."
    ),
}


def _sample_size_summary(config: Any) -> str:
    """Item C2 — как задавался размер выборки, одной фразой (тот же смысл, что
    formatSizeMode на Design tab), плюс сколько кандидатов реально осталось.
    Отдельная функция, а не логика в шаблоне: три взаимоисключающих режима с
    условиями — ровно то, что в jinja читается хуже всего."""
    computed = getattr(config, "computed", None) or {}
    if config.mde_abs_input is not None:
        target = f"Target absolute MDE {config.mde_abs_input:g}"
        if config.mde_source_metric:
            target += f" (on {config.mde_source_metric})"
    elif config.mde is not None:
        target = f"Target relative MDE {config.mde * 100:.1f}%"
    elif config.sample_size is not None:
        target = f"Target sample size {config.sample_size}"
    else:
        target = "All available data"
    available = computed.get("n_available")
    if available is not None:
        return f"{target} · candidates available after isolation: {available}"
    return target


def render_analysis_report(results: Any, context: dict[str, Any]) -> str:
    """Строит report.html: 8 секций из DESIGN.md (раздел 8)."""
    config = context["config"]
    control_name = context["control_name"]

    raw_values: dict = context.get("raw_values", {})
    # Item 3 (per-dimension segment analysis): segment_results_by_dimension
    # already includes the combined (cross-product) dimension under its own
    # label (abkit/experiment.py::Experiment.analyze()).
    segment_results_by_dimension: dict = context.get("segment_results_by_dimension", {})
    daily_results: dict = context.get("daily_results", {})

    metrics_by_name = {m.name: m for m in config.metrics}
    # Item A1 — подпись метрики для ВСЕГО, что видит читатель отчета:
    # заголовки секций, подписи графиков (forest/distribution/segment/daily),
    # таблица результатов. Ключ (metric_name) при этом везде остается
    # техническим именем — по нему ходят results[...] и metrics_by_name.
    labels = metric_labels_by_name(config.metrics)

    def label_of(metric_name: str) -> str:
        return labels.get(metric_name, metric_name)

    first_fig = True
    metric_sections = []

    for metric_name in results.metrics:
        metric_results = results[metric_name]
        metric_config = metrics_by_name.get(metric_name)
        role = metric_config.role if metric_config else "primary"
        metric_label_text = label_of(metric_name)

        forest_html = fig_to_html_div(
            forest_plot(metric_results, title=f"{metric_label_text}: forest plot"),
            include_js=first_fig,
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
                metric_name=metric_label_text,
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

        # Item 3.2: one subsection per stratification dimension (plus the
        # combined cross-product, itself just another entry under its own
        # " × "-joined label) — same exploratory framing as before, just no
        # longer limited to the combined breakdown alone.
        # External split rework (§3): dimensions chosen ad-hoc at analyze time
        # (not declared as strata at design) are tagged in the report.
        ad_hoc_dimensions = set(context.get("ad_hoc_segment_dimensions", []))
        # Bugfix (ad-hoc segment columns must not be silently dropped): requested
        # cuts that produced no breakdown, rendered as a visible notice below the
        # segment plots instead of vanishing. Global (metric-independent), shown
        # in each metric's segment section for self-containedness.
        segment_skips = context.get("segment_skips", [])
        segment_sections = []
        for dim_label, dim_results in segment_results_by_dimension.items():
            dim_htmls = []
            n_segments = 0
            for treat_name, seg_list in dim_results.get(metric_name, {}).items():
                if not seg_list:
                    continue
                # §3: number of strata in this dimension drives the > 12
                # collapse threshold — same rule as the balance/power tables.
                n_segments = max(n_segments, len(seg_list))
                fig = segment_forest_plot(
                    seg_list,
                    title=f"{metric_label_text} by {dim_label}: {control_name} vs {treat_name}",
                )
                dim_htmls.append((treat_name, fig_to_html_div(fig)))
            if dim_htmls:
                segment_sections.append(
                    (dim_label, dim_htmls, dim_label in ad_hoc_dimensions, n_segments)
                )

        daily_htmls = []
        for treat_name, daily_df in daily_results.get(metric_name, {}).items():
            if daily_df is None or daily_df.empty:
                continue
            fig = cumulative_lift_plot(
                daily_df,
                title=f"{metric_label_text}: cumulative lift {control_name} vs {treat_name}",
            )
            daily_htmls.append((treat_name, fig_to_html_div(fig)))

        verdicts = {
            r.treatment_group: results.verdict(metric_name, treatment_group=r.treatment_group, alpha=config.alpha)
            for r in metric_results
            if r.is_designed_method
        }

        metric_sections.append(
            dict(
                name=metric_label_text,
                # Техническое имя колонки — рядом, мелким серым (item A1:
                # "column name stays visible secondarily where it matters
                # technically"); шаблон показывает его, только если оно
                # отличается от подписи.
                column=metric_name,
                role=role,
                type=metric_config.type if metric_config else "continuous",
                description=metric_config.description if metric_config else None,
                forest_html=forest_html,
                distribution_htmls=distribution_htmls,
                segment_sections=segment_sections,
                segment_skips=segment_skips,
                daily_htmls=daily_htmls,
                verdicts=verdicts,
                results=metric_results,
            )
        )

    detailed_rows = results.detailed_display_rows(
        control_name, alpha=config.alpha, metric_labels=labels
    )
    detailed_columns = list(detailed_rows[0].keys()) if detailed_rows else []
    # detailed_display_rows() no longer carries a "Designed" column (UX
    # package, 5.1) — the designed-method row is still bolded, using the
    # flag from the internal (non-display) detailed_rows(), same order.
    detailed_designed_flags = [row["designed"] for row in results.detailed_rows(control_name, alpha=config.alpha)]

    template = _env.get_template("report.html.j2")
    return template.render(
        experiment_name=context["experiment_name"],
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        lifecycle_dates=_lifecycle_dates(context),
        config=config,
        # Item C2 — полный дизайн-контекст (см. секцию section-design-context
        # в templates/report.html.j2).
        hypothesis=context.get("hypothesis"),
        design_metrics=[
            {
                "name": m.name, "label": labels.get(m.name, m.name), "type": m.type,
                "role": m.role, "description": m.description,
                "pre_col": m.pre_col, "num": m.num, "den": m.den,
            }
            for m in config.metrics
        ],
        sample_size_summary=_sample_size_summary(config),
        isolation_disclosure=isolation_disclosure(getattr(config, "computed", None)),
        flow_image_groups=_build_flow_image_groups(context.get("flow_images")),
        control_name=control_name,
        group_sizes=context["group_sizes"],
        srm=context["srm"],
        loss=context["loss"],
        correction=context["correction"],
        # External split rework (§2a): strata balance on the analyzed users —
        # None for a design with no strata (section not rendered then).
        strata_balance=context.get("strata_balance"),
        strata_balance_rows=(
            checks.strata_balance_rows(context["strata_balance"])
            if context.get("strata_balance") is not None else []
        ),
        strata_balance_groups=(
            checks.strata_balance_groups(context["strata_balance"])
            if context.get("strata_balance") is not None else []
        ),
        # Strata power check (visibility package §2): the DESIGN's power check,
        # sourced from the stored design snapshot on config.computed. Redesign
        # deletes analysis results (jobs.py::run_redesign), so any surviving run
        # was computed against the CURRENT design → config.computed IS this
        # run's design snapshot; no stale-snapshot risk. None (section omitted)
        # when the design has no strata, the design predates this feature, or
        # the experiment is external-split (external stores no power/MDE).
        strata_power=strata_power_view(
            config.computed.get("strata_power") if getattr(config, "computed", None) else None
        ),
        global_warnings=results.global_warnings,
        metric_sections=metric_sections,
        detailed_columns=detailed_columns,
        detailed_rows=detailed_rows,
        detailed_designed_flags=detailed_designed_flags,
        detailed_column_tooltips=DETAILED_COLUMN_TOOLTIPS,
        product_name=PRODUCT_NAME,
        logo_data_uri=_logo_data_uri(),
    )


def strata_power_view(strata_power: dict[str, list[dict[str, Any]]] | None) -> dict[str, Any] | None:
    """Transform the stored strata power check ({dimension: [row]}, from
    config.computed['strata_power']) into the per-metric block structure the
    Design tab and both HTML reports render, plus collapse metadata. One block
    per metric (matching the design report's per-metric MDE structure), each
    with a table per stratification dimension. Returns None when there's no
    strata power to show (no strata / external split / pre-feature design).

    n_rows = total power-check rows (= strata for the common single-metric/
    single-group case) → drives the >12 collapse threshold, shared by the app
    (AntD Collapse) and the reports (details/summary) so they read identically.
    n_weak = rows whose status isn't "ok" (weak or insufficient)."""
    if not strata_power:
        return None
    all_rows = [r for rows in strata_power.values() for r in rows]
    if not all_rows:
        return None
    multi_group = len({r["treatment_group"] for r in all_rows}) > 1
    metrics: list[str] = []
    for r in all_rows:
        if r["metric"] not in metrics:
            metrics.append(r["metric"])
    blocks = []
    for metric in metrics:
        dims = [
            {"label": label, "rows": [r for r in rows if r["metric"] == metric]}
            for label, rows in strata_power.items()
            if any(r["metric"] == metric for r in rows)
        ]
        blocks.append({"metric": metric, "dimensions": dims})
    return {
        "blocks": blocks,
        "n_rows": len(all_rows),
        "n_weak": sum(1 for r in all_rows if r.get("status") != "ok"),
        "multi_group": multi_group,
    }


def render_design_report(
    experiment: Any,
    created_at: datetime | None = None,
    flow_images: dict[str, list[dict[str, Any]]] | None = None,
    planned_end_date: Any = None,
    hypothesis: str | None = None,
) -> str:
    """Строит design_report.html: упрощенный вариант (доступность, MDE, баланс, SRM, pre-A/A).

    created_at: Stage 2 (report header dates) — optional; design_report is
    always generated at design time, so started_at/completed_at don't apply
    yet (status is always "designed" at this point) — only "Created" is
    shown. Passed explicitly by the caller (Experiment.design(), right after
    the experiment row is created) rather than read off `experiment`, since
    the in-memory Experiment class has no DB-row timestamp fields.

    flow_images: Stage 4 — {group_name: [{"flow_title": str, "file_path": str}, ...]},
    already ordered by position; optional because design_report.html is
    generated at design/redesign time, BEFORE the wizard's post-submit
    flow-image upload step ever runs (see abkit/jobs.py::run_set_flow_image_group_order,
    which regenerates this file once images actually exist). Absent/empty ->
    no Variant flows section, not an empty one."""
    config = experiment.config
    report = experiment.report

    metric_descriptions = {m.name: m.description for m in experiment.config.metrics}
    # Item A1 — подпись метрики (display_name, иначе имя колонки); колонка
    # остается видна отдельным полем `metric`.
    metric_display = metric_labels_by_name(experiment.config.metrics)
    power_rows = [
        dict(
            metric=name,
            metric_label=metric_display.get(name, name),
            mde_rel=pr.mde_rel,
            mde_rel_cuped=pr.mde_rel_cuped,
            mde_abs=pr.mde_abs,
            mde_abs_cuped=pr.mde_abs_cuped,
            metric_type=pr.metric_type,
            sample_size=pr.sample_size_per_group,
            rho=pr.rho,
            warnings=pr.warnings,
            metric_role=pr.metric_role,
            metric_description=metric_descriptions.get(name),
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
        created_at=_format_report_date(created_at),
        # Items B2/C2 — та же строка дат, что и в отчете анализа (одна функция
        # на оба, чтобы форматы не разъехались): здесь из фактических дат есть
        # только Created (дизайн всегда в статусе designed), плюс плановая дата
        # окончания, если объявлена.
        lifecycle_dates=_lifecycle_dates(
            {"created_at": created_at, "planned_end_date": planned_end_date}
        ),
        hypothesis=hypothesis,
        isolation_disclosure=isolation_disclosure(
            {
                "isolation_decision": report.isolation_decision,
                "excluded_by_experiment": report.excluded_by_experiment,
                "n_excluded_by_isolation": report.n_excluded_by_isolation,
            }
        ),
        flow_image_groups=_build_flow_image_groups(flow_images),
        config=config,
        n_candidates_total=report.n_candidates_total,
        n_excluded_by_isolation=report.n_excluded_by_isolation,
        n_available=report.n_available,
        excluded_by_experiment=report.excluded_by_experiment,
        group_sizes=report.group_sizes,
        power_rows=power_rows,
        srm=report.srm,
        strata_balance=report.strata_balance,
        # 6-part package pt.10: per-stratum-per-group counts + column order,
        # derived from the same crosstab strata_balance.chi2 was computed
        # from — plus the distinct stratum count for the "Stratified by: ..."
        # sentence.
        strata_balance_rows=checks.strata_balance_rows(report.strata_balance),
        strata_balance_groups=checks.strata_balance_groups(report.strata_balance),
        n_strata=len(report.strata_balance.table.index),
        # Strata power check (visibility package) — from the stored computed
        # snapshot; None when no strata (section then omitted).
        strata_power=strata_power_view(config.computed.get("strata_power") if config.computed else None),
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
