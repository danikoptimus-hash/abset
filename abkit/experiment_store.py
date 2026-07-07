"""Абстракция хранения экспериментов, общая для файлового (ABKIT_MODE=file,
дефолт) и серверного (ABKIT_MODE=db) режимов (DOCKER.md, раздел 8, пункт 1).

Experiment.design()/.load() работают через get_experiment_store(), поэтому
статистическая оркестрация (design/analyze) не знает и не должна знать, куда
физически пишутся данные — файлы или Postgres.

Тяжелые артефакты (report.html, results.json, design_report.html) в обоих
режимах остаются файлами на диске (DOCKER.md §5: "тяжелые бинарники — на
volume /data"); ExperimentHandle.path — директория, куда их писать/откуда
читать, независимо от режима.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pandas as pd

from abkit.config import DesignConfig


@dataclass
class ExperimentHandle:
    """Результат create_experiment()/load_experiment(): все, что нужно
    Experiment для дальнейшей работы, независимо от бэкенда хранения."""

    name: str
    path: Path
    config: DesignConfig
    assignments: pd.DataFrame | None = None


class ExperimentStore(Protocol):
    """Минимальный интерфейс, которого достаточно Experiment.design()/.load().

    Не включает update_status/list_experiments — их вызывают напрямую app.py и
    cli.py (через abkit.storage в файловом режиме); в серверном режиме их
    аналог — ExperimentRepo, используемый напрямую (см. DOCKER.md, этап D2).
    """

    def create_experiment(
        self, config: DesignConfig, assignments: pd.DataFrame, owner_id: str | None = None
    ) -> ExperimentHandle: ...

    def load_experiment(self, name: str) -> ExperimentHandle: ...


class FileExperimentStore:
    """Обертка над abkit/storage.py — существующее файловое поведение 1-в-1, ни
    одна операция/проверка не изменена. Используется по умолчанию (ABKIT_MODE
    не задан или "file"), поэтому все существующие тесты не видят разницы."""

    def __init__(self, experiments_dir: Path):
        self.experiments_dir = experiments_dir

    def create_experiment(
        self, config: DesignConfig, assignments: pd.DataFrame, owner_id: str | None = None
    ) -> ExperimentHandle:
        from abkit import storage

        path = storage.create_experiment_dir(self.experiments_dir, config.name)
        storage.save_config(path, config)
        storage.save_assignments(path, assignments)
        storage.save_group_samples(path, assignments)
        storage.register_experiment(self.experiments_dir, config.name, path, status="designed")
        return ExperimentHandle(name=config.name, path=path, config=config, assignments=assignments)

    def load_experiment(self, name: str) -> ExperimentHandle:
        from abkit import storage

        path = storage.experiment_path(self.experiments_dir, name)
        config = storage.load_config(path)
        assignments = storage.load_assignments(path)
        return ExperimentHandle(name=name, path=path, config=config, assignments=assignments)


def get_experiment_store(experiments_dir: Path | None = None) -> ExperimentStore:
    """Фабрика: ABKIT_MODE=db -> DbExperimentStore (Postgres + ABKIT_DATA_DIR
    для артефактов), иначе (дефолт) -> FileExperimentStore (текущее поведение).
    """
    mode = os.environ.get("ABKIT_MODE", "file")
    if mode == "db":
        from abkit.db.store import DbExperimentStore

        return DbExperimentStore()

    from abkit import storage

    experiments_dir = experiments_dir or storage.get_experiments_dir()
    return FileExperimentStore(experiments_dir)
