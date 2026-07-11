"""abkit — фреймворк для дизайна и анализа A/B тестов."""

from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import DesignError, Experiment

__all__ = ["DesignConfig", "MetricConfig", "Experiment", "DesignError", "PRODUCT_NAME"]

__version__ = "2.0.0"

# Единый источник имени продукта (UX-пакет, ребрендинг) — README.md, HTML-отчеты
# (abkit/viz/report.py), CLI (cli.py/cli_admin.py --help), backend (Settings >
# About, FastAPI app title), frontend (frontend/src/branding.ts — TS не может
# импортировать Python, синхронизировать вручную при изменении). "abkit" (в
# нижнем регистре) остается техническим идентификатором пакета/репозитория/
# путей — не переименовывается.
PRODUCT_NAME = "ABSet"
