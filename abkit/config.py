"""Конфигурационные модели дизайна эксперимента."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class MetricConfig(BaseModel):
    """Описание одной метрики эксперимента."""

    name: str
    type: Literal["continuous", "binary", "ratio"]
    role: Literal["primary", "secondary"] = "primary"
    pre_col: str | None = None
    num: str | None = None
    den: str | None = None
    default_methods: list[str] | None = None

    @model_validator(mode="after")
    def _check_ratio_columns(self) -> "MetricConfig":
        if self.type == "ratio" and (self.num is None or self.den is None):
            raise ValueError(
                f"Metric '{self.name}' of type 'ratio' must set num and den"
            )
        return self


class DesignConfig(BaseModel):
    """Полный конфиг дизайна эксперимента."""

    name: str
    unit_col: str
    groups: dict[str, float]
    metrics: list[MetricConfig]
    alpha: float = 0.05
    power: float = 0.8
    mde: float | None = None
    mde_abs_input: float | None = None
    """Введенный пользователем абсолютный MDE (в единицах метрики) — если
    задан, mde (относительный) вычислен из него как mde_abs_input /
    baseline_mean метрики mde_source_metric. Хранится только для
    трассируемости UI-ввода — расчет мощности всегда идет через mde
    (относительный), это поле не влияет на логику."""
    mde_source_metric: str | None = None
    """Имя метрики, чей baseline использовался для перевода mde_abs_input в
    относительный mde (см. mde_abs_input)."""
    sample_size: int | None = None
    split_method: Literal["simple", "stratified", "hash"] = "stratified"
    strata: list[str] = Field(default_factory=list)
    n_buckets_continuous: int = 4
    min_stratum_size: int = 20
    nan_strategy: Literal["separate_stratum", "drop", "error"] = "separate_stratum"
    """Что делать с пропусками в стратификационных колонках: separate_stratum —
    выделить в отдельную страту 'unknown' (по умолчанию); drop — удалить таких
    юзеров из кандидатов; error — упасть с ошибкой (старое поведение)."""
    hash_salt: str | None = None
    isolation: Literal["exclude", "warn", "off", "exclude_selected"] = "exclude"
    exclude_experiments: Literal["all_active"] | list[str] = "all_active"
    isolation_selected_experiments: list[str] = Field(default_factory=list)
    """Имена экспериментов для isolation="exclude_selected" — учитываются
    только они (а не "все активные кроме них", как в exclude_experiments)."""
    seed: int | None = None
    computed: dict[str, Any] | None = None
    """Величины, вычисленные на этапе дизайна: изоляция, мощность, проверки сплита."""

    @model_validator(mode="after")
    def _check_groups_sum_to_one(self) -> "DesignConfig":
        if not self.groups:
            raise ValueError("groups cannot be empty")
        total = sum(self.groups.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"The sum of groups proportions must equal 1, got {total}")
        return self

    @model_validator(mode="after")
    def _check_exactly_one_size_spec(self) -> "DesignConfig":
        specified = [v is not None for v in (self.mde, self.sample_size)]
        if sum(specified) > 1:
            raise ValueError(
                "Set exactly one of mde / sample_size (or neither — then "
                "all available data is used)"
            )
        return self

    @model_validator(mode="after")
    def _check_metric_names_unique(self) -> "DesignConfig":
        names = [m.name for m in self.metrics]
        if len(names) != len(set(names)):
            raise ValueError("Metric names must be unique")
        return self
