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
    group_descriptions: dict[str, str] = Field(default_factory=dict)
    """Опциональное описание группы ("что показывает/делает этот вариант") —
    Stage 3, чисто для отображения (Design tab, design_report), не влияет ни
    на сплит, ни на анализ. Сиблинг-словарь к groups (те же ключи-имена),
    а не переход groups на dict[str, GroupConfig] — иначе пришлось бы менять
    каждого потребителя groups как dict[str, float] (splitter.py,
    experiment.py, demo_data.py, validation/simulation.py) и ломать JSON уже
    существующих в БД экспериментов. Ключи, отсутствующие здесь (старые
    эксперименты, группа без описания) — трактуются как "нет описания", не
    ошибка. Редактируется только через Redesign (design-wizard/
    Step2GroupsMetrics.tsx) — отдельного edit-флоу нет."""
    metrics: list[MetricConfig]
    split_source: Literal["abkit", "external"] = "abkit"
    """"abkit" (default): the usual flow — ABSet picks candidates, splits
    them, stores assignments. "external" (item 12): the split happens in an
    outside system (Firebase A/B Testing and similar) — ABSet only stores
    the declared groups/metrics for analysis; no dataset, no isolation, no
    power calculation (nothing to compute variance from), no assignments.
    See Experiment.design_external()/analyze()'s split_source=="external"
    branch."""
    alpha: float = 0.05
    power: float = 0.8
    mde: float | None = None
    """Относительный MDE — доля (fraction), НЕ процент: 0.05 = +5% relative
    lift. Единственное поле, которое реально участвует в расчете мощности
    (abkit/experiment.py::_compute_power_results) — mde_abs_input ниже
    только для трассируемости UI-ввода, само по себе на расчет не влияет."""
    mde_abs_input: float | None = None
    """Введенный пользователем абсолютный MDE, В ЕДИНИЦАХ МЕТРИКИ — контракт
    единиц (item 1, баг с перепутанными единицами абсолютного MDE):
    - continuous/ratio: те же единицы, что и сама метрика (доллары,
      секунды, ...) — без конвертации.
    - binary: ДОЛЯ [0..1], не процент и не процентный пункт как целое число
      — 1 п.п. (percentage point) абсолютного изменения = 0.01, НЕ 1. Это
      тот же масштаб, что и baseline_mean для binary-метрики (тоже доля
      0..1, напр. 0.17 = 17%). UI-слой (design-wizard) конвертирует ввод
      пользователя в pp (что он видит и печатает) в эту долю РОВНО ОДИН
      РАЗ, при заполнении этого поля — сюда должно попадать уже
      сконвертированное значение, не сырой ввод "1" (что означало бы
      100 п.п., а не 1 п.п.).

    Если задан, mde (относительный) вычислен из него как mde_abs_input /
    baseline_mean метрики mde_source_metric. Хранится только для
    трассируемости UI-ввода — расчет мощности всегда идет через mde
    (относительный), это поле не влияет на логику напрямую, НО используется
    как подсказка в implausible_sample_size_warning() (abkit/design/
    power.py) при implausibly малом расчетном n — сигнал именно такой
    ошибки единиц."""
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
