"""abkit — фреймворк для дизайна и анализа A/B тестов."""

from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import DesignError, Experiment

__all__ = ["DesignConfig", "MetricConfig", "Experiment", "DesignError"]

__version__ = "0.1.0"
