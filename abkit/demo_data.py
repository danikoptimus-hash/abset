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
