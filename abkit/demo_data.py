"""Генерация синтетических demo-данных: общая для CLI (`abkit demo`) и Streamlit
(кнопка «Загрузить демо-данные» в табе Design)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from abkit.config import DesignConfig, MetricConfig

DEMO_METRICS = [
    MetricConfig(name="revenue", type="continuous", pre_col="revenue_pre"),
    MetricConfig(name="clicks", type="binary", role="secondary"),
    MetricConfig(name="conv_rate", type="ratio", num="orders", den="sessions", role="secondary"),
]


def generate_demo_design_data(n: int, seed: int = 0) -> pd.DataFrame:
    """Синтетические исторические данные для демо-эксперимента."""
    rng = np.random.default_rng(seed)
    revenue_pre = rng.normal(100, 20, size=n)
    return pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "platform": rng.choice(["ios", "android"], size=n),
            "revenue": revenue_pre + rng.normal(0, 5, size=n),
            "revenue_pre": revenue_pre,
            "clicks": rng.binomial(1, 0.12, size=n),
            "orders": rng.integers(0, 5, size=n),
            "sessions": rng.integers(1, 10, size=n),
        }
    )


def make_demo_design_config(name: str, n: int, seed: int = 0) -> DesignConfig:
    """Конфиг демо-эксперимента: revenue (continuous+CUPED), clicks (binary), conv_rate (ratio)."""
    return DesignConfig(
        name=name,
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=list(DEMO_METRICS),
        strata=["platform"],
        sample_size=n,
        split_method="stratified",
        seed=seed,
        isolation="off",  # demo-данные синтетические и переиспользуются между запусками
    )


def generate_demo_post_data_for_config(
    config: DesignConfig,
    assignments: pd.DataFrame,
    *,
    effect: float = 0.03,
    attrition: float = 0.02,
    seed: int = 1,
) -> pd.DataFrame:
    """Синтетические пост-данные для ЛЮБОГО эксперимента — по метрикам из его
    config, не только для захардкоженного demo (см. generate_demo_post_data
    ниже, которая используется CLI-командой `abkit demo` для фиксированного
    набора revenue/clicks/conv_rate). Используется кнопкой "Сгенерировать
    demo пост-данные" в Analyze-табе, когда у пользователя еще нет реальных
    результатов теста, но он хочет посмотреть, как работает анализ, на СВОЕМ
    (не обязательно "demo") эксперименте.

    - continuous/binary: пре-период (для pre_col) генерируется тут же и
      коррелирует с пост-значением на ~0.65 (нет доступа к реальным
      историческим данным дизайна — Experiment.load() их не хранит, только
      assignments и config, см. experiment.py — поэтому "pre" тоже синтетика).
    - ratio: num/den генерируются согласованно (num = Binomial(den, p)).
    - +effect относительного лифта — только группам, отличным от control, и
      только primary-метрикам (secondary остаются шумом без эффекта).
    - ~attrition (по умолчанию 2%) юзеров теряются симметрично по группам
      (независимо в каждой группе), чтобы проверки честности (SRM/loss)
      показывали не идеальные, а реалистично "живые" числа.
    """
    from abkit.experiment import infer_control_name

    rng = np.random.default_rng(seed)
    control_name = infer_control_name(config.groups)
    groups = assignments["group"].to_numpy()
    is_lifted = groups != control_name
    n = len(assignments)

    out: dict[str, np.ndarray] = {}

    for metric in config.metrics:
        apply_effect = is_lifted if metric.role == "primary" else np.zeros(n, dtype=bool)

        if metric.type == "ratio":
            den = rng.integers(1, 10, size=n)
            base_p = 0.30
            p = np.where(apply_effect, base_p * (1 + effect), base_p)
            num = rng.binomial(den, np.clip(p, 0, 1))
            out[metric.num] = num
            out[metric.den] = den
            continue

        if metric.type == "binary":
            if metric.pre_col:
                pre = rng.beta(2, 8, size=n)  # правдоподобный "pre"-пропенсити в [0,1]
                out[metric.pre_col] = pre
                base_p = float(pre.mean())
            else:
                base_p = 0.10
            p = np.where(apply_effect, np.clip(base_p * (1 + effect), 0, 1), base_p)
            out[metric.name] = rng.binomial(1, p)
            continue

        # continuous
        mu, sigma = 100.0, 20.0
        pre = rng.normal(mu, sigma, size=n)
        rho = 0.65
        post = rho * (pre - mu) + np.sqrt(1 - rho**2) * sigma * rng.normal(0, 1, size=n) + mu
        post = np.where(apply_effect, post * (1 + effect), post)
        if metric.pre_col:
            out[metric.pre_col] = pre
        out[metric.name] = post

    df = pd.DataFrame({config.unit_col: assignments["unit_id"].to_numpy(), **out})

    keep = np.ones(n, dtype=bool)
    for group_name in pd.unique(groups):
        group_idx = np.where(groups == group_name)[0]
        n_drop = int(round(len(group_idx) * attrition))
        if n_drop:
            drop_idx = rng.choice(group_idx, size=n_drop, replace=False)
            keep[drop_idx] = False

    return df[keep].reset_index(drop=True)


def generate_demo_post_data(assignments: pd.DataFrame, effect: float, seed: int = 1) -> pd.DataFrame:
    """Фактические (post-period) данные с реальным лифтом revenue в группе treatment."""
    rng = np.random.default_rng(seed)
    n = len(assignments)
    is_treatment = (assignments["group"] == "treatment").to_numpy()
    revenue_pre = rng.normal(100, 20, size=n)
    revenue = revenue_pre + rng.normal(0, 5, size=n)
    revenue[is_treatment] += effect * 100
    return pd.DataFrame(
        {
            "user_id": assignments["unit_id"],
            "revenue": revenue,
            "revenue_pre": revenue_pre,
            "clicks": rng.binomial(1, 0.12, size=n),
            "orders": rng.integers(0, 5, size=n),
            "sessions": rng.integers(1, 10, size=n),
        }
    )
