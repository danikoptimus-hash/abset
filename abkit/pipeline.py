"""Пайплайн шагов анализа одной метрики: preprocess -> variance_reduction -> test."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, Literal

import pandas as pd

from abkit.analysis.results import TestResult

_STAGE_ORDER = {"preprocess": 0, "variance_reduction": 1, "test": 2}

# методологически спорные, но не запрещенные комбинации шагов -> предупреждение, не ошибка
QUESTIONABLE_COMBINATIONS: dict[tuple[str, str], str] = {
    ("CUPED", "MannWhitney"): (
        "MannWhitney после CUPED методологически спорен: ранговый критерий не "
        "предполагает работу с преобразованной CUPED величиной"
    ),
}


@dataclass
class MetricContext:
    """Данные и метаданные метрики, передаваемые между шагами пайплайна."""

    metric_name: str
    metric_type: Literal["continuous", "binary", "ratio"]
    control_name: str
    treatment_name: str
    values: pd.Series
    group: pd.Series
    alpha: float = 0.05
    stratum: pd.Series | None = None
    covariate: pd.Series | None = None
    num: pd.Series | None = None
    den: pd.Series | None = None
    is_designed_method: bool = True
    role: Literal["primary", "secondary"] = "primary"
    applied_steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    n_removed: dict[str, int] = field(default_factory=dict)
    variance_reduction: float | None = None
    result: TestResult | None = None


class Step(ABC):
    """Базовый класс шага пайплайна."""

    stage: ClassVar[Literal["preprocess", "variance_reduction", "test"]]

    @abstractmethod
    def apply(self, ctx: MetricContext) -> MetricContext: ...

    @property
    def name(self) -> str:
        return type(self).__name__


class PipelineError(Exception):
    """Некорректная конфигурация цепочки шагов (нарушение порядка стадий и т.п.)."""


def method_display_name(ctx: MetricContext, own_name: str) -> str:
    """Человекочитаемое имя цепочки: preprocess/variance_reduction шаги + сам test-шаг.

    Например "CUPED + Welch t-test" вместо голого "Welch t-test" — иначе Welch после
    CUPED, после обрезки выбросов и голый Welch неотличимы в отчете/сводке.
    """
    prefix = ctx.applied_steps[:-1]  # все шаги до текущего (test-шаг уже добавлен в applied_steps)
    return " + ".join([*prefix, own_name]) if prefix else own_name


@dataclass
class Pipeline:
    """Валидированная цепочка шагов: preprocess* -> variance_reduction* -> test (ровно один)."""

    steps: list[Step]

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        stages = [s.stage for s in self.steps]
        test_count = stages.count("test")
        if test_count != 1:
            raise PipelineError(
                f"Пайплайн должен содержать ровно один test-шаг, получено {test_count}"
            )
        seen_max = -1
        for stage in stages:
            order = _STAGE_ORDER[stage]
            if order < seen_max:
                raise PipelineError(
                    "Нарушен порядок стадий пайплайна: должно быть "
                    "preprocess -> variance_reduction -> test"
                )
            seen_max = max(seen_max, order)

    @property
    def method_name(self) -> str:
        return " + ".join(step.name for step in self.steps)

    def questionable_warnings(self) -> list[str]:
        names = [step.name for step in self.steps]
        warnings = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                msg = QUESTIONABLE_COMBINATIONS.get((names[i], names[j]))
                if msg:
                    warnings.append(msg)
        return warnings

    def _validate_against_context(self, ctx: MetricContext) -> None:
        if ctx.metric_type == "ratio":
            test_step = next(s for s in self.steps if s.stage == "test")
            if test_step.name != "DeltaMethodTTest":
                raise PipelineError(
                    f"Для ratio-метрики '{ctx.metric_name}' единственный допустимый test-шаг — "
                    "DeltaMethodTTest: единица анализа может не совпадать с единицей "
                    f"рандомизации, наивный тест по строкам запрещен. Получено '{test_step.name}'"
                )

    def run(self, ctx: MetricContext) -> MetricContext:
        self._validate_against_context(ctx)
        ctx.warnings.extend(self.questionable_warnings())
        for step in self.steps:
            ctx.applied_steps.append(step.name)
            ctx = step.apply(ctx)
        return ctx
