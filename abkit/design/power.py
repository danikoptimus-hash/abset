"""Расчет размера выборки, MDE и мощности."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd
from scipy import stats as sp_stats
from scipy.optimize import brentq


@dataclass
class PowerResult:
    """Результат расчета мощности/MDE для одной метрики."""

    metric: str
    metric_type: Literal["continuous", "binary", "ratio"]
    baseline_mean: float
    baseline_std: float
    mde_abs: float | None = None
    mde_rel: float | None = None
    sample_size_per_group: float | None = None
    rho: float | None = None
    mde_abs_cuped: float | None = None
    mde_rel_cuped: float | None = None
    sample_size_per_group_cuped: float | None = None
    warnings: list[str] = field(default_factory=list)


def _z_scores(alpha: float, power: float) -> tuple[float, float]:
    z_alpha = sp_stats.norm.ppf(1 - alpha / 2)
    z_power = sp_stats.norm.ppf(power)
    return z_alpha, z_power


def sample_size_continuous(
    std: float, mde_abs: float, alpha: float = 0.05, power: float = 0.8, ratio: float = 1.0
) -> float:
    """Размер контрольной группы для детектирования абсолютного эффекта (z-приближение).

    ratio = n_treatment / n_control.
    """
    if mde_abs <= 0:
        raise ValueError("mde_abs must be positive")
    if std < 0:
        raise ValueError("std cannot be negative")
    z_alpha, z_power = _z_scores(alpha, power)
    return (1 + 1 / ratio) * std**2 * (z_alpha + z_power) ** 2 / mde_abs**2


def mde_continuous(
    std: float, n_control: float, alpha: float = 0.05, power: float = 0.8, ratio: float = 1.0
) -> float:
    """Достижимый абсолютный MDE при заданном размере контрольной группы."""
    if n_control <= 0:
        raise ValueError("n_control must be positive")
    z_alpha, z_power = _z_scores(alpha, power)
    return (z_alpha + z_power) * std * np.sqrt((1 + 1 / ratio) / n_control)


def power_given_n_continuous(
    std: float, mde_abs: float, n_control: float, alpha: float = 0.05, ratio: float = 1.0
) -> float:
    """Аналитическая мощность при заданных std/эффекте/размере контрольной группы.

    Замкнутая форма — обращение sample_size_continuous относительно power.
    """
    if n_control <= 0:
        raise ValueError("n_control must be positive")
    z_alpha, _ = _z_scores(alpha, 0.5)
    z_power = np.sqrt(n_control * mde_abs**2 / ((1 + 1 / ratio) * std**2)) - z_alpha
    return float(sp_stats.norm.cdf(z_power))


def sample_size_binary(
    p_control: float,
    p_treat: float,
    alpha: float = 0.05,
    power: float = 0.8,
    ratio: float = 1.0,
) -> float:
    """Размер контрольной группы для двухвыборочного z-теста пропорций.

    ratio = n_treatment / n_control.
    """
    if not (0 < p_control < 1) or not (0 < p_treat < 1):
        raise ValueError("Proportions must be in the interval (0, 1)")
    if p_control == p_treat:
        raise ValueError("p_control and p_treat cannot be equal")
    z_alpha, z_power = _z_scores(alpha, power)
    p_pooled = (p_control + ratio * p_treat) / (1 + ratio)
    std_null = np.sqrt(p_pooled * (1 - p_pooled) * (1 + 1 / ratio))
    std_alt = np.sqrt(p_control * (1 - p_control) + p_treat * (1 - p_treat) / ratio)
    return (z_alpha * std_null + z_power * std_alt) ** 2 / (p_treat - p_control) ** 2


def mde_binary(
    p_control: float,
    n_control: float,
    alpha: float = 0.05,
    power: float = 0.8,
    ratio: float = 1.0,
) -> float:
    """Достижимый абсолютный MDE (в пропорциях) при заданном размере контрольной группы.

    Решается численно (обратная задача к sample_size_binary): аналитического
    обращения нет.
    """
    if not 0 < p_control < 1:
        raise ValueError("p_control must be in the interval (0, 1)")
    if n_control <= 0:
        raise ValueError("n_control must be positive")

    def f(delta: float) -> float:
        return (
            sample_size_binary(p_control, p_control + delta, alpha=alpha, power=power, ratio=ratio)
            - n_control
        )

    lo, hi = 1e-9, 1 - p_control - 1e-9
    # required_n(delta) монотонно убывает по delta: чем больше эффект, тем меньше нужно наблюдений
    if f(lo) <= 0:
        return lo
    if f(hi) >= 0:
        return hi
    return brentq(f, lo, hi, xtol=1e-12)


def power_given_n_binary(
    p_control: float, p_treat: float, n_control: float, alpha: float = 0.05, ratio: float = 1.0
) -> float:
    """Аналитическая мощность при заданных пропорциях/размере контрольной группы.

    Замкнутая форма — обращение sample_size_binary относительно power.
    """
    if not (0 < p_control < 1) or not (0 < p_treat < 1):
        raise ValueError("Proportions must be in the interval (0, 1)")
    if n_control <= 0:
        raise ValueError("n_control must be positive")
    z_alpha, _ = _z_scores(alpha, 0.5)
    p_pooled = (p_control + ratio * p_treat) / (1 + ratio)
    std_null = np.sqrt(p_pooled * (1 - p_pooled) * (1 + 1 / ratio))
    std_alt = np.sqrt(p_control * (1 - p_control) + p_treat * (1 - p_treat) / ratio)
    z_power = (np.sqrt(n_control) * abs(p_treat - p_control) - z_alpha * std_null) / std_alt
    return float(sp_stats.norm.cdf(z_power))


def delta_method_variance(num: pd.Series, den: pd.Series) -> tuple[float, float]:
    """Среднее и дисперсия ratio-метрики (num/den) на уровне юнита дельта-методом.

    Возвращает (mean, variance), которые можно дальше использовать как обычную
    continuous-метрику в формулах sample_size_continuous/mde_continuous.
    """
    if len(num) < 2:
        raise ValueError("At least 2 observations are needed to estimate variance")
    num_mean, den_mean = float(num.mean()), float(den.mean())
    if den_mean == 0:
        raise ValueError("The denominator mean cannot be zero")
    var_num = float(num.var(ddof=1))
    var_den = float(den.var(ddof=1))
    cov = float(np.cov(num, den, ddof=1)[0, 1])
    ratio_mean = num_mean / den_mean
    variance = (
        var_num / den_mean**2
        - 2 * (num_mean / den_mean**3) * cov
        + (num_mean**2 / den_mean**4) * var_den
    )
    return ratio_mean, max(variance, 0.0)


def correlation_with_pre(metric: pd.Series, pre: pd.Series) -> float:
    """Корреляция метрики с pre-period ковариатой (для оценки эффекта CUPED)."""
    combined = pd.DataFrame({"metric": metric, "pre": pre}).dropna()
    if len(combined) < 2:
        return 0.0
    corr = combined["metric"].corr(combined["pre"])
    return 0.0 if pd.isna(corr) else float(corr)


def cuped_variance_multiplier(rho: float) -> float:
    """Множитель дисперсии при CUPED: var_cuped = var * (1 - rho^2)."""
    return 1 - rho**2
