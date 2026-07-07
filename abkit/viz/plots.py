"""Plotly-графики для отчетов: forest plot, распределения, кумулятивный лифт."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from statsmodels.stats.proportion import proportion_confint

from abkit.analysis.results import TestResult

_DESIGNED_COLOR = "#2E7D32"
_OTHER_COLOR = "#90A4AE"


def fig_to_html_div(fig: go.Figure, include_js: bool = False) -> str:
    """Рендерит figure в HTML-фрагмент. include_js=True встраивает plotly.js целиком
    (используется один раз на страницу для полностью офлайн-отчета)."""
    return fig.to_html(
        full_html=False,
        include_plotlyjs="inline" if include_js else False,
        config={"displaylogo": False},
    )


def _forest_traces(labels: list[str], effects: list[float], lo: list[float], hi: list[float], designed: list[bool]) -> go.Scatter:
    colors = [_DESIGNED_COLOR if d else _OTHER_COLOR for d in designed]
    err_plus = [h - e for h, e in zip(hi, effects)]
    err_minus = [e - l for l, e in zip(lo, effects)]
    return go.Scatter(
        x=effects,
        y=labels,
        mode="markers",
        marker=dict(color=colors, size=11, symbol="diamond"),
        error_x=dict(type="data", symmetric=False, array=err_plus, arrayminus=err_minus),
        showlegend=False,
    )


def forest_plot(results: list[TestResult], value: str = "rel", title: str = "") -> go.Figure:
    """Forest plot по цепочкам методов: designed-цепочка выделена цветом, ноль — вертикаль."""
    labels = [f"{r.method} ({r.treatment_group})" for r in results]
    if value == "rel":
        effects = [r.effect_rel * 100 for r in results]
        lo = [r.ci_rel[0] * 100 for r in results]
        hi = [r.ci_rel[1] * 100 for r in results]
        x_title = "Эффект, %"
    else:
        effects = [r.effect_abs for r in results]
        lo = [r.ci_abs[0] for r in results]
        hi = [r.ci_abs[1] for r in results]
        x_title = "Эффект (абс.)"

    designed = [r.is_designed_method for r in results]
    fig = go.Figure()
    fig.add_trace(_forest_traces(labels, effects, lo, hi, designed))
    fig.add_vline(x=0, line_dash="dash", line_color="gray")
    fig.update_layout(
        title=title,
        xaxis_title=x_title,
        height=max(250, 70 * len(results) + 100),
        margin=dict(l=200),
    )
    return fig


def segment_forest_plot(segment_results: list[tuple[str, TestResult]], title: str = "") -> go.Figure:
    """Forest plot эффекта в разрезе страт (segment_results: [(stratum_name, TestResult)])."""
    labels = [f"{name}" for name, _r in segment_results]
    results = [r for _name, r in segment_results]
    designed = [False] * len(results)  # сегменты всегда exploratory
    effects = [r.effect_rel * 100 for r in results]
    lo = [r.ci_rel[0] * 100 for r in results]
    hi = [r.ci_rel[1] * 100 for r in results]

    fig = go.Figure()
    fig.add_trace(_forest_traces(labels, effects, lo, hi, designed))
    fig.add_vline(x=0, line_dash="dash", line_color="gray")
    fig.update_layout(
        title=title,
        xaxis_title="Эффект, %",
        height=max(250, 70 * len(results) + 100),
        margin=dict(l=200),
    )
    return fig


def _wilson_proportion_plot(
    control: pd.Series, treatment: pd.Series, metric_name: str, control_name: str, treat_name: str
) -> go.Figure:
    """Bar-chart долей с усами Wilson score interval — гистограмма непригодна для 0/1."""
    names = [control_name, treat_name]
    series_by_name = {control_name: control, treat_name: treatment}
    colors = {control_name: _OTHER_COLOR, treat_name: _DESIGNED_COLOR}

    props, err_plus, err_minus, texts = [], [], [], []
    for name in names:
        values = series_by_name[name].dropna()
        n = len(values)
        count = int(values.sum())
        p = count / n if n else 0.0
        lo, hi = proportion_confint(count, n, alpha=0.05, method="wilson") if n else (0.0, 0.0)
        props.append(p * 100)
        err_plus.append((hi - p) * 100)
        err_minus.append((p - lo) * 100)
        texts.append(f"{p * 100:.1f}% ± {(hi - lo) / 2 * 100:.1f}%")

    fig = go.Figure(
        go.Bar(
            x=names,
            y=props,
            error_y=dict(type="data", symmetric=False, array=err_plus, arrayminus=err_minus),
            text=texts,
            textposition="outside",
            marker_color=[colors[n] for n in names],
        )
    )
    fig.update_layout(
        title=f"Доля {metric_name} (binary): {control_name} vs {treat_name}" if metric_name else "Доля (binary)",
        yaxis_title="Доля, %",
        showlegend=False,
        height=450,
    )
    return fig


def distribution_plot(
    control: pd.Series,
    treatment: pd.Series,
    metric_name: str = "",
    metric_type: str = "continuous",
    control_name: str = "control",
    treat_name: str = "treatment",
    trim_threshold: float | None = None,
) -> go.Figure:
    """Распределение метрики по группам — вид зависит от типа метрики:
    binary -> bar-chart долей с Wilson-ДИ (гистограмма непригодна для 0/1);
    continuous/ratio -> наложенные гистограммы + ECDF (для ratio исключаются
    юзеры с нулевым знаменателем — они уже NaN в values на этот момент)."""
    if metric_type == "binary":
        return _wilson_proportion_plot(control, treatment, metric_name, control_name, treat_name)

    control_clean = control.dropna()
    treatment_clean = treatment.dropna()
    n_excluded = (len(control) - len(control_clean)) + (len(treatment) - len(treatment_clean))
    n = len(control_clean) + len(treatment_clean)
    nbins = max(5, min(50, int(np.sqrt(max(n, 1)))))

    fig = make_subplots(rows=2, cols=1, subplot_titles=["Распределение", "ECDF"])

    fig.add_trace(
        go.Histogram(
            x=control_clean, name=control_name, opacity=0.55, histnorm="probability density", nbinsx=nbins
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Histogram(
            x=treatment_clean, name=treat_name, opacity=0.55, histnorm="probability density", nbinsx=nbins
        ),
        row=1, col=1,
    )
    fig.update_layout(barmode="overlay")

    for name, series in ((control_name, control_clean), (treat_name, treatment_clean)):
        values = np.sort(series.to_numpy())
        if len(values) == 0:
            continue
        ecdf = np.arange(1, len(values) + 1) / len(values)
        fig.add_trace(go.Scatter(x=values, y=ecdf, mode="lines", name=f"{name} ECDF"), row=2, col=1)

    if trim_threshold is not None:
        fig.add_vline(x=trim_threshold, line_dash="dot", line_color="red", row=1, col=1)

    title = f"Распределение {metric_name} ({metric_type})" if metric_name else "Распределение"
    if metric_type == "ratio" and n_excluded > 0:
        title += f" — {n_excluded} юзеров исключены (нулевой знаменатель)"

    all_values = pd.concat([control_clean, treatment_clean]) if n else pd.Series(dtype=float)
    skew = float(all_values.skew()) if len(all_values) > 2 else 0.0
    if skew > 3 and len(all_values) and all_values.min() > 0:
        fig.update_layout(
            updatemenus=[
                dict(
                    type="buttons",
                    direction="right",
                    x=1.0, y=1.15, xanchor="right",
                    buttons=[
                        dict(label="Линейная шкала X", method="relayout", args=[{"xaxis.type": "linear"}]),
                        dict(label="Лог-шкала X (перекошено)", method="relayout", args=[{"xaxis.type": "log"}]),
                    ],
                )
            ]
        )

    fig.update_layout(title=title, height=650)
    return fig


def cumulative_lift_plot(daily: pd.DataFrame, title: str = "") -> go.Figure:
    """Кумулятивный лифт с ДИ по дням. daily: колонки date, effect_rel, ci_lower, ci_upper."""
    fig = go.Figure()
    dates = list(daily["date"])
    fig.add_trace(
        go.Scatter(
            x=dates + dates[::-1],
            y=list(daily["ci_upper"]) + list(daily["ci_lower"])[::-1],
            fill="toself",
            fillcolor="rgba(46,125,50,0.15)",
            line=dict(color="rgba(255,255,255,0)"),
            name="ДИ",
            showlegend=False,
        )
    )
    fig.add_trace(
        go.Scatter(x=dates, y=list(daily["effect_rel"]), mode="lines+markers", name="Кумулятивный лифт, %")
    )
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    fig.update_layout(title=title, xaxis_title="Дата", yaxis_title="Лифт, %")
    return fig
