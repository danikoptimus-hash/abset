"""Данные для ECharts на фронте (FRONTEND.md §5.2, секция «Анализ») —
собираются из AnalysisResults.context (raw_values/segment_results/
daily_results), который Experiment.analyze() уже вычисляет и прикрепляет
через attach_context(). Ядро (abkit/analysis, abkit/design, abkit/pipeline)
не трогается: тут только пересборка УЖЕ посчитанных чисел в JSON-формат для
графиков, никакой новой статистики. Числа сравнения (forest plot) уже есть
в AnalysisResults.to_json()["results"] — здесь только то, чего там нет:
распределения (гистограмма+ECDF или Wilson-доли), сегменты, дневной лифт.

Результат мержится в основной results.json на уровне backend (не в
AnalysisResults.to_json(), который остается неизменным и используется CLI/
Streamlit как раньше) — см. routers/experiments.py::_save_analysis."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from statsmodels.stats.proportion import proportion_confint

from abkit.analysis.results import AnalysisResults
from abkit.viz.plots import p99_clip_stats

_MAX_ECDF_POINTS = 200


def sanitize_json_floats(obj: Any) -> Any:
    """NaN/Infinity — валидные токены для json.dumps() (allow_nan=True по
    умолчанию), но НЕ валидный JSON по спецификации: Postgres JSONB отклоняет
    их при INSERT ("Token \"NaN\" is invalid"). NaN у эффекта — не баг, а
    законный результат на вырожденных сегментах (нулевая дисперсия в
    страте) — заменяем на None рекурсивно перед сохранением, а не в
    AnalysisResults.to_json() (ядро не трогаем)."""
    if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        return None
    if isinstance(obj, dict):
        return {k: sanitize_json_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_json_floats(v) for v in obj]
    return obj


def _downsample_ecdf(values: np.ndarray) -> list[list[float]]:
    """Сортированные значения -> до _MAX_ECDF_POINTS точек (x, cumulative_fraction).
    Без даунсэмплинга ECDF на 5000+ наблюдений раздувает JSON без видимой
    пользы (визуально неотличимо от прореженной кривой)."""
    values = np.sort(values)
    n = len(values)
    if n == 0:
        return []
    if n <= _MAX_ECDF_POINTS:
        idx = np.arange(n)
    else:
        idx = np.linspace(0, n - 1, _MAX_ECDF_POINTS).astype(int)
    return [[float(values[i]), float((i + 1) / n)] for i in idx]


def _binary_distribution(control: pd.Series, treatment: pd.Series) -> dict[str, Any]:
    def _prop_ci(values: pd.Series) -> dict[str, Any]:
        clean = values.dropna()
        n = len(clean)
        count = int(clean.sum())
        prop = count / n if n else 0.0
        lo, hi = proportion_confint(count, n, alpha=0.05, method="wilson") if n else (0.0, 0.0)
        return {"prop": prop, "ci_lo": float(lo), "ci_hi": float(hi), "n": n}

    return {"kind": "binary", "control": _prop_ci(control), "treatment": _prop_ci(treatment)}


def _histogram_pair(
    control: pd.Series, treatment: pd.Series, lo: float, hi: float, nbins: int, clip_upper: float | None
) -> tuple[list[float], list[float], list[float]]:
    hi = max(hi, lo + 1e-9)
    bin_edges = np.linspace(lo, hi, nbins + 1)
    control_vals = control.clip(upper=clip_upper) if clip_upper is not None else control
    treatment_vals = treatment.clip(upper=clip_upper) if clip_upper is not None else treatment
    control_hist, _ = np.histogram(control_vals, bins=bin_edges, density=True)
    treatment_hist, _ = np.histogram(treatment_vals, bins=bin_edges, density=True)
    return [float(x) for x in bin_edges], [float(x) for x in control_hist], [float(x) for x in treatment_hist]


def _continuous_distribution(control: pd.Series, treatment: pd.Series) -> dict[str, Any]:
    control_clean = control.dropna()
    treatment_clean = treatment.dropna()
    combined = pd.concat([control_clean, treatment_clean])
    n = len(combined)

    if n == 0:
        empty_hist = {"bin_edges": [], "control_counts": [], "treatment_counts": []}
        return {
            "kind": "continuous", "clipped": dict(empty_hist), "full_range": dict(empty_hist),
            "control_ecdf": [], "treatment_ecdf": [], "p99_threshold": None, "n_above_p99": 0, "pct_above_p99": 0.0,
        }

    threshold, n_above, pct_above = p99_clip_stats(combined)
    p99_threshold = threshold if n_above > 0 else None
    lo = float(combined.min())
    full_max = float(combined.max())
    nbins = max(5, min(50, int(np.sqrt(max(n, 1)))))

    # toggle "полный диапазон" (FRONTEND.md §5.2) — два независимых биннинга:
    # P99-обрезанный (дефолт, наглядность) и по полным данным (без обрезки).
    clipped_edges, clipped_control, clipped_treatment = _histogram_pair(
        control_clean, treatment_clean, lo, p99_threshold if p99_threshold is not None else full_max,
        nbins, p99_threshold,
    )
    full_edges, full_control, full_treatment = _histogram_pair(
        control_clean, treatment_clean, lo, full_max, nbins, None,
    )

    return {
        "kind": "continuous",
        "clipped": {"bin_edges": clipped_edges, "control_counts": clipped_control, "treatment_counts": clipped_treatment},
        "full_range": {"bin_edges": full_edges, "control_counts": full_control, "treatment_counts": full_treatment},
        "control_ecdf": _downsample_ecdf(control_clean.to_numpy()),
        "treatment_ecdf": _downsample_ecdf(treatment_clean.to_numpy()),
        "p99_threshold": p99_threshold,
        "n_above_p99": n_above,
        "pct_above_p99": pct_above,
    }


def build_chart_data(results: AnalysisResults) -> dict[str, Any]:
    """None, если результаты получены не через Experiment.analyze() (нет
    context) — вызывающая сторона (analyze job) всегда работает через
    Experiment.analyze(), так что на практике context есть всегда."""
    context = results.context
    if context is None:
        return {}

    config = context["config"]
    control_name = context["control_name"]
    raw_values: dict = context.get("raw_values", {})
    segment_results: dict = context.get("segment_results", {})
    daily_results: dict = context.get("daily_results", {})
    metrics_by_name = {m.name: m for m in config.metrics}

    chart_data: dict[str, Any] = {}
    for metric_name in results.metrics:
        metric_config = metrics_by_name.get(metric_name)
        metric_type = metric_config.type if metric_config else "continuous"
        metric_raw = raw_values.get(metric_name, {})
        control_series = metric_raw.get(control_name)

        distributions: dict[str, Any] = {}
        if control_series is not None:
            for treat_name, treat_series in metric_raw.items():
                if treat_name == control_name:
                    continue
                distributions[treat_name] = (
                    _binary_distribution(control_series, treat_series)
                    if metric_type == "binary"
                    else _continuous_distribution(control_series, treat_series)
                )

        segments: dict[str, Any] = {}
        for treat_name, seg_list in segment_results.get(metric_name, {}).items():
            if not seg_list:
                continue
            segments[treat_name] = [
                {
                    "stratum": stratum_name,
                    "effect_rel": r.effect_rel,
                    "ci_rel": list(r.ci_rel),
                }
                for stratum_name, r in seg_list
            ]

        daily: dict[str, Any] = {}
        for treat_name, daily_df in daily_results.get(metric_name, {}).items():
            if daily_df is None or daily_df.empty:
                continue
            daily[treat_name] = [
                {
                    "date": str(row.date),
                    "effect_rel": float(row.effect_rel),
                    "ci_lower": float(row.ci_lower),
                    "ci_upper": float(row.ci_upper),
                }
                for row in daily_df.itertuples()
            ]

        chart_data[metric_name] = {
            "metric_type": metric_type,
            "control_name": control_name,
            "distributions": distributions,
            "segments": segments,
            "daily": daily,
        }

    srm = context.get("srm")
    loss = context.get("loss")
    return {
        # SRM/потери данных на АНАЛИЗЕ (не путать с design-time SRM/balance,
        # уже доступными через config["computed"] — это отдельная проверка на
        # фактических пост-данных, context["srm"]/["loss"] не персистятся
        # больше нигде: без этого честность анализа на фронте не проверить).
        "checks": {
            "srm": {"chi2": srm.chi2, "p_value": srm.p_value, "passed": srm.passed} if srm else None,
            "loss": {
                "chi2": loss.chi2, "p_value": loss.p_value, "symmetric": loss.symmetric,
                "missing_rate": loss.missing_rate,
            }
            if loss
            else None,
        },
        "metrics": chart_data,
    }
