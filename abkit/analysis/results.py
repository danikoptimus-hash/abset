"""Единый формат результата статистического критерия и агрегация по эксперименту."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from rich.console import Console
from rich.table import Table


@dataclass
class TestResult:
    """Результат одного статистического критерия для одной пары control/treatment."""

    metric: str
    method: str
    effect_abs: float
    effect_rel: float
    ci_abs: tuple[float, float]
    ci_rel: tuple[float, float]
    p_value: float
    p_value_adjusted: float | None
    n: dict[str, int]
    n_removed: dict[str, int]
    variance_reduction: float | None
    warnings: list[str]
    is_designed_method: bool
    treatment_group: str
    role: Literal["primary", "secondary"] = "primary"
    # Correlation between the metric and its pre-period covariate — only set
    # when CUPED was applied (variance_reduction ≈ cuped_rho²). None
    # otherwise, incl. for methods that don't use a covariate at all.
    cuped_rho: float | None = None


class AnalysisResults:
    """Агрегирует TestResult по метрикам, дает вердикты, сводку и JSON-экспорт."""

    def __init__(self, results: list[TestResult], global_warnings: list[str] | None = None):
        self._results = list(results)
        self.global_warnings = list(global_warnings or [])
        self._by_metric: dict[str, list[TestResult]] = {}
        for r in self._results:
            self._by_metric.setdefault(r.metric, []).append(r)
        self._context: dict[str, Any] | None = None

    def __getitem__(self, metric: str) -> list[TestResult]:
        return self._by_metric[metric]

    def __contains__(self, metric: str) -> bool:
        return metric in self._by_metric

    @property
    def results(self) -> list[TestResult]:
        return list(self._results)

    @property
    def metrics(self) -> list[str]:
        return list(self._by_metric.keys())

    def _designed_results(self, metric: str, treatment_group: str | None = None) -> list[TestResult]:
        if metric not in self._by_metric:
            raise KeyError(f"No results for metric '{metric}'")
        candidates = [r for r in self._by_metric[metric] if r.is_designed_method]
        if treatment_group is not None:
            candidates = [r for r in candidates if r.treatment_group == treatment_group]
        return candidates

    def verdict(self, metric: str, treatment_group: str | None = None, alpha: float = 0.05) -> str:
        """significant_positive / significant_negative / no_effect_detected по designed-цепочке.

        Если для метрики несколько treatment-групп, нужно явно указать treatment_group.
        """
        candidates = self._designed_results(metric, treatment_group)
        if not candidates:
            raise KeyError(f"No designed result for metric '{metric}'")
        if len(candidates) > 1:
            raise ValueError(
                f"Metric '{metric}' has several treatment groups: "
                f"{[r.treatment_group for r in candidates]}. Specify treatment_group explicitly."
            )
        r = candidates[0]
        p = r.p_value_adjusted if r.p_value_adjusted is not None else r.p_value
        if p < alpha and r.effect_abs > 0:
            return "significant_positive"
        if p < alpha and r.effect_abs < 0:
            return "significant_negative"
        return "no_effect_detected"

    def verdicts(self, alpha: float = 0.05) -> dict[tuple[str, str], str]:
        """Вердикты по всем designed-результатам: (metric, treatment_group) -> вердикт."""
        out: dict[tuple[str, str], str] = {}
        for r in self._results:
            if not r.is_designed_method:
                continue
            p = r.p_value_adjusted if r.p_value_adjusted is not None else r.p_value
            if p < alpha and r.effect_abs > 0:
                out[(r.metric, r.treatment_group)] = "significant_positive"
            elif p < alpha and r.effect_abs < 0:
                out[(r.metric, r.treatment_group)] = "significant_negative"
            else:
                out[(r.metric, r.treatment_group)] = "no_effect_detected"
        return out

    def attach_context(self, **context: Any) -> None:
        """Прикрепляет контекст эксперимента (данные для report()); вызывается Experiment.analyze()."""
        self._context = context

    @property
    def context(self) -> dict[str, Any] | None:
        """Контекст эксперимента (config, raw_values, segment_results, daily_results, ...),
        если результаты получены через Experiment.analyze(). Используется UI-слоями
        (app.py) для построения тех же графиков, что и в report()."""
        return self._context

    def report(self, path: Path | str | None = None) -> Path:
        """Рендерит report.html + results.json в папку эксперимента (или в path).

        Требует, чтобы результаты были получены через Experiment.analyze() (там
        прикрепляется контекст через attach_context) — при создании AnalysisResults
        напрямую report() недоступен.
        """
        if self._context is None:
            raise RuntimeError(
                "AnalysisResults is not attached to an experiment (no context for the report); "
                "use Experiment.analyze() rather than constructing AnalysisResults directly"
            )
        from abkit.viz.report import render_analysis_report  # локальный импорт: избегаем цикла

        target_dir = Path(path) if path else self._context["path"]
        html = render_analysis_report(self, self._context)
        report_path = target_dir / "report.html"
        report_path.write_text(html, encoding="utf-8")
        (target_dir / "results.json").write_text(self.to_json(), encoding="utf-8")
        self._write_detailed_results_csv(target_dir / "detailed_results.csv")
        return report_path

    def _write_detailed_results_csv(self, path: Path) -> None:
        import csv

        control_name = self._context.get("control_name", "") if self._context else ""
        alpha = self._context["config"].alpha if self._context else 0.05
        rows = self.detailed_display_rows(control_name, alpha=alpha)
        if not rows:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    def summary(self) -> None:
        """Печатает консольную таблицу результатов (rich)."""
        console = Console(legacy_windows=False)
        table = Table(title="Analysis results")
        table.add_column("Metric")
        table.add_column("Group")
        table.add_column("Method")
        table.add_column("Effect (abs)")
        table.add_column("Effect (rel, %)")
        table.add_column("p-value")
        table.add_column("p-adj")
        table.add_column("Designed")
        for r in self._results:
            table.add_row(
                r.metric,
                r.treatment_group,
                r.method,
                f"{r.effect_abs:.4g}",
                f"{r.effect_rel * 100:.2f}%" if r.effect_rel == r.effect_rel else "n/a",
                f"{r.p_value:.4g}",
                f"{r.p_value_adjusted:.4g}" if r.p_value_adjusted is not None else "-",
                "yes" if r.is_designed_method else "no",
            )
        console.print(table)
        if self.global_warnings:
            console.print("[yellow]Warnings:[/yellow]")
            for w in self.global_warnings:
                console.print(f"  - {w}")

    def detailed_rows(self, control_name: str, alpha: float = 0.05) -> list[dict[str, Any]]:
        """Строки для "Детальная таблица результатов" (UI/HTML-отчет) — ВСЕ
        вычисленные сравнения (designed и exploratory-методы), не только
        designed-цепочка, как в verdict()/verdicts(). Вердикт считается по
        КАЖДОЙ строке отдельно (тем же правилом, что verdict()). Сортировка:
        метрика, затем метод."""
        correction = self._context.get("correction") if self._context else None
        # Дедупликация: compare_methods=True иногда пересчитывает ТУ ЖЕ
        # designed-цепочку еще раз как одну из "альтернативных" (например,
        # designed-метод метрики с pre_col — CUPED+Welch, а альтернативные
        # цепочки для CUPED-метрик тоже включают CUPED+Welch) — совпадающие
        # (metric, method, treatment_group) с designed-строкой схлопываются в
        # одну (designed) строку; собственно разные альтернативные методы не
        # трогаются (UX-пакет, дедуп Detailed Results Table).
        designed_keys = {(r.metric, r.method, r.treatment_group) for r in self._results if r.is_designed_method}
        rows: list[dict[str, Any]] = []
        for r in self._results:
            if not r.is_designed_method and (r.metric, r.method, r.treatment_group) in designed_keys:
                continue
            p = r.p_value_adjusted if r.p_value_adjusted is not None else r.p_value
            if p < alpha and r.effect_abs > 0:
                verdict = "significant_positive"
            elif p < alpha and r.effect_abs < 0:
                verdict = "significant_negative"
            else:
                verdict = "no_effect_detected"

            if r.variance_reduction is None:
                variance_reduction_label = "—"
            else:
                if "CUPED" in r.method:
                    technique = "CUPED"
                elif "PostStratification" in r.method:
                    technique = "PostStrat"
                else:
                    technique = "yes"
                variance_reduction_label = f"{technique} ({r.variance_reduction:.1%})"

            rows.append(
                {
                    "metric": r.metric,
                    "group": f"{r.treatment_group} vs {control_name}",
                    "method": r.method,
                    "designed": r.is_designed_method,
                    "effect_abs": r.effect_abs,
                    "effect_rel": r.effect_rel,
                    "ci_rel_lo": r.ci_rel[0],
                    "ci_rel_hi": r.ci_rel[1],
                    "p_value": r.p_value,
                    "p_value_adjusted": r.p_value_adjusted,
                    "correction_method": correction or "none",
                    "n_control": r.n.get(control_name),
                    "n_test": r.n.get(r.treatment_group),
                    "variance_reduction": variance_reduction_label,
                    "cuped_rho": r.cuped_rho,
                    "verdict": verdict,
                }
            )
        rows.sort(key=lambda row: (row["metric"], row["method"]))
        return rows

    def detailed_display_rows(self, control_name: str, alpha: float = 0.05) -> list[dict[str, Any]]:
        """Как detailed_rows(), но с готовыми к показу заголовками колонок и
        отформатированными значениями — единый источник для HTML-отчета
        (report.py) и CSV-выгрузки (report()). React-UI использует свою копию
        (frontend/src/pages/experiment/DetailedResultsTable.tsx) — держит
        английские заголовки в синхроне с этими вручную.

        Без колонки "Designed" (UX-пакет, п.5.1) — designed-метод при
        нескольких методах (compare_methods) выделяется жирной строкой
        (report.py передает designed-флаг в шаблон отдельно от этого dict,
        через detailed_rows(); React-таблица — через rowClassName), отдельная
        колонка с галкой избыточна."""
        rows = self.detailed_rows(control_name, alpha=alpha)
        return [
            {
                "Metric": row["metric"],
                "Comparison group": row["group"],
                "Method": row["method"],
                "Effect (abs.)": row["effect_abs"],
                "Lift %": (
                    row["effect_rel"] * 100 if row["effect_rel"] == row["effect_rel"] else None
                ),
                "95% CI of lift": f"[{row['ci_rel_lo'] * 100:.2f}%, {row['ci_rel_hi'] * 100:.2f}%]",
                "p-value": row["p_value"],
                "p-value (adj.)": row["p_value_adjusted"],
                "Correction": row["correction_method"],
                "n (control)": row["n_control"],
                "n (test)": row["n_test"],
                "Variance reduction": row["variance_reduction"],
                "CUPED rho": row["cuped_rho"] if row["cuped_rho"] is None else round(row["cuped_rho"], 3),
                "Verdict": row["verdict"],
            }
            for row in rows
        ]

    def to_json(self) -> str:
        from abkit import __version__ as abkit_version  # локальный импорт: избегаем цикла

        payload = {
            "abkit_version": abkit_version,
            "seed": self._context["config"].seed if self._context else None,
            "correction": self._context["correction"] if self._context else None,
            "global_warnings": self.global_warnings,
            "results": [
                {
                    "metric": r.metric,
                    "method": r.method,
                    "treatment_group": r.treatment_group,
                    "effect_abs": r.effect_abs,
                    "effect_rel": r.effect_rel,
                    "ci_abs": list(r.ci_abs),
                    "ci_rel": list(r.ci_rel),
                    "p_value": r.p_value,
                    "p_value_adjusted": r.p_value_adjusted,
                    "n": r.n,
                    "n_removed": r.n_removed,
                    "variance_reduction": r.variance_reduction,
                    "cuped_rho": r.cuped_rho,
                    "warnings": r.warnings,
                    "is_designed_method": r.is_designed_method,
                    "role": r.role,
                }
                for r in self._results
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)
