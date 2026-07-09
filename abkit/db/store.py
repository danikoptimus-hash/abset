"""DbExperimentStore — реализация abkit.experiment_store.ExperimentStore поверх
Postgres (ABKIT_MODE=db). Собирает ExperimentRepo + AssignmentRepo; тяжелые
артефакты (report.html, results.json) по-прежнему пишутся на диск, в
ABKIT_DATA_DIR/<experiment_name>/ (DOCKER.md §5 — "тяжелые бинарники на volume
/data", в БД лежат только метаданные).
"""

from __future__ import annotations

import os
import uuid as uuid_mod
from pathlib import Path
from typing import Literal

import pandas as pd

from abkit import storage
from abkit.config import DesignConfig
from abkit.db.repositories import AssignmentRepo, ExperimentRepo, RepoError, ResultRepo, UserRepo
from abkit.experiment_store import ExperimentHandle

# До этапа D2 (auth) экспериментам в db-режиме, созданным без явного owner_id,
# назначается служебный владелец. password_hash="!" не может совпасть ни с
# одним настоящим хешем (argon2id/bcrypt так не начинаются) — учетка нерабочая
# для логина, только placeholder-владелец до подключения реальной аутентификации.
_SYSTEM_USER_EMAIL = "system@abkit.local"


class DbStoreError(storage.StorageError):
    """Ошибка серверного режима хранения. Наследуется от storage.StorageError,
    чтобы существующий `except storage.StorageError` в app.py/cli.py ловил
    ошибки обоих режимов без изменений (задел на этап D2)."""


def get_data_dir() -> Path:
    raw = os.environ.get("ABKIT_DATA_DIR", str(Path.home() / ".abkit_data"))
    return Path(raw).expanduser().resolve()


class DbExperimentStore:
    def __init__(self) -> None:
        self.experiments = ExperimentRepo()
        self.assignments = AssignmentRepo()
        self.users = UserRepo()
        self.results = ResultRepo()
        self.data_dir = get_data_dir()

    def _ensure_system_user(self) -> uuid_mod.UUID:
        existing = self.users.get_by_email(_SYSTEM_USER_EMAIL)
        if existing is not None:
            return existing.id
        return self.users.create(
            email=_SYSTEM_USER_EMAIL, first_name="system", password_hash="!", role="admin"
        )

    def _artifact_dir(self, name: str) -> Path:
        path = self.data_dir / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create_experiment(
        self, config: DesignConfig, assignments: pd.DataFrame, owner_id: str | None = None
    ) -> ExperimentHandle:
        resolved_owner = uuid_mod.UUID(owner_id) if owner_id else self._ensure_system_user()
        config_dict = config.model_dump(mode="json")
        try:
            exp_row = self.experiments.create(
                name=config.name, owner_id=resolved_owner, status="designed", config=config_dict
            )
        except RepoError as e:
            raise DbStoreError(str(e)) from e
        self.assignments.bulk_insert(exp_row.id, assignments)
        path = self._artifact_dir(config.name)
        return ExperimentHandle(name=config.name, path=path, config=config, assignments=assignments)

    def load_experiment(self, name: str) -> ExperimentHandle:
        exp_row = self.experiments.get_by_name(name)
        if exp_row is None:
            raise DbStoreError(f"Experiment '{name}' not found")
        config = DesignConfig.model_validate(exp_row.config)
        assignments = self.assignments.load(exp_row.id)
        path = self._artifact_dir(name)
        return ExperimentHandle(name=name, path=path, config=config, assignments=assignments)

    def save_analysis_result(self, name: str, results_json: str, report_path: Path) -> None:
        """Пишет строку в analysis_results (jsonb с results.json целиком) —
        вызывается из Experiment.analyze() в db-режиме после Analysis Results
        сформированы, для трассируемости (какие результаты когда получены)."""
        import json

        exp_row = self.experiments.get_by_name(name)
        if exp_row is None:
            raise DbStoreError(f"Experiment '{name}' not found")
        self.results.create(
            experiment_id=exp_row.id, results=json.loads(results_json), report_path=str(report_path)
        )

    def occupied_units(
        self,
        exclude_experiments: Literal["all_active"] | list[str],
        current_experiment_name: str | None,
    ) -> dict[str, set[str]]:
        """Один SQL-запрос вместо чтения assignments.parquet каждого активного
        эксперимента (DOCKER.md §5) — используется abkit/design/isolation.py в
        db-режиме через параметр store=."""
        exclude_ids: set[uuid_mod.UUID] = set()
        if current_experiment_name:
            exp = self.experiments.get_by_name(current_experiment_name)
            if exp is not None:
                exclude_ids.add(exp.id)
        if exclude_experiments != "all_active":
            for nm in exclude_experiments:
                exp = self.experiments.get_by_name(nm)
                if exp is not None:
                    exclude_ids.add(exp.id)
        return self.assignments.occupied_units_for_active_experiments(exclude_ids)

    def occupied_units_selected(
        self, selected_experiments: list[str], current_experiment_name: str | None
    ) -> dict[str, set[str]]:
        """Изоляция "только выбранные эксперименты" (UI: exclude_selected) —
        в отличие от occupied_units, здесь selected_experiments — это INCLUDE-
        список, а не exclude-список."""
        ids: set[uuid_mod.UUID] = set()
        for nm in selected_experiments:
            if nm == current_experiment_name:
                continue
            exp = self.experiments.get_by_name(nm)
            if exp is not None:
                ids.add(exp.id)
        return self.assignments.occupied_units_for_selected_experiments(ids)
