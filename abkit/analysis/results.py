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
            raise KeyError(f"Нет результатов для метрики '{metric}'")
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
            raise KeyError(f"Нет designed-результата для метрики '{metric}'")
        if len(candidates) > 1:
            raise ValueError(
                f"Для метрики '{metric}' несколько treatment-групп: "
                f"{[r.treatment_group for r in candidates]}. Укажите treatment_group явно."
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
                "AnalysisResults не привязан к эксперименту (нет контекста для отчета); "
                "используйте Experiment.analyze(), а не создавайте AnalysisResults напрямую"
            )
        from abkit.viz.report import render_analysis_report  # локальный импорт: избегаем цикла

        target_dir = Path(path) if path else self._context["path"]
        html = render_analysis_report(self, self._context)
        report_path = target_dir / "report.html"
        report_path.write_text(html, encoding="utf-8")
        (target_dir / "results.json").write_text(self.to_json(), encoding="utf-8")
        return report_path

    def summary(self) -> None:
        """Печатает консольную таблицу результатов (rich)."""
        console = Console(legacy_windows=False)
        table = Table(title="Результаты анализа")
        table.add_column("Метрика")
        table.add_column("Группа")
        table.add_column("Метод")
        table.add_column("Эффект (абс)")
        table.add_column("Эффект (отн, %)")
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
                "да" if r.is_designed_method else "нет",
            )
        console.print(table)
        if self.global_warnings:
            console.print("[yellow]Предупреждения:[/yellow]")
            for w in self.global_warnings:
                console.print(f"  - {w}")

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
                    "warnings": r.warnings,
                    "is_designed_method": r.is_designed_method,
                    "role": r.role,
                }
                for r in self._results
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)
