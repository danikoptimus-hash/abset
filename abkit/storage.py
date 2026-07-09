"""Хранение экспериментов: папки, registry.json, атомарные записи."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from filelock import FileLock

from abkit.config import DesignConfig

STATUSES = ("designed", "running", "completed", "archived")

# порядок разрешенных переходов статуса эксперимента
_STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "designed": ("running", "archived"),
    "running": ("completed", "archived"),
    "completed": ("archived",),
    "archived": (),
}


class StorageError(Exception):
    """Пользовательская ошибка хранения (не баг, а некорректное использование)."""


def _default_settings() -> dict[str, Any]:
    return {
        "experiments_dir": "~/ab_experiments",
        "default_alpha": 0.05,
        "default_power": 0.8,
        "default_correction": "holm",
        "random_seed": None,
    }


def load_settings(settings_path: Path | str | None = None) -> dict[str, Any]:
    """Загружает settings.yaml, если он есть, иначе — дефолты."""
    settings = _default_settings()
    path = Path(settings_path) if settings_path else Path("settings.yaml")
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        settings.update(loaded)
    return settings


def get_experiments_dir(settings: dict[str, Any] | None = None) -> Path:
    """Определяет корневую папку экспериментов.

    Приоритет: переменная окружения ABKIT_EXPERIMENTS_DIR > settings.yaml > дефолт.
    """
    env_dir = os.environ.get("ABKIT_EXPERIMENTS_DIR")
    if env_dir:
        raw = env_dir
    else:
        settings = settings if settings is not None else load_settings()
        raw = settings.get("experiments_dir", "~/ab_experiments")
    return Path(raw).expanduser().resolve()


def _registry_path(experiments_dir: Path) -> Path:
    return experiments_dir / "registry.json"


def _registry_lock(experiments_dir: Path) -> FileLock:
    lock_path = experiments_dir / "registry.json.lock"
    return FileLock(str(lock_path), timeout=30)


def _read_registry_unlocked(experiments_dir: Path) -> dict[str, Any]:
    path = _registry_path(experiments_dir)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return {}
    return json.loads(content)


def _write_registry_atomic(experiments_dir: Path, registry: dict[str, Any]) -> None:
    """Атомарная запись реестра: пишем во временный файл и переименовываем."""
    experiments_dir.mkdir(parents=True, exist_ok=True)
    path = _registry_path(experiments_dir)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(experiments_dir), prefix=".registry_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise


def read_registry(experiments_dir: Path) -> dict[str, Any]:
    """Читает registry.json под файловой блокировкой (защита от чтения на середине записи)."""
    with _registry_lock(experiments_dir):
        return _read_registry_unlocked(experiments_dir)


def register_experiment(
    experiments_dir: Path,
    name: str,
    path: Path,
    status: str = "designed",
) -> None:
    """Регистрирует новый эксперимент в реестре. Ошибка при коллизии имени."""
    now = datetime.now(timezone.utc).isoformat()
    with _registry_lock(experiments_dir):
        registry = _read_registry_unlocked(experiments_dir)
        if name in registry:
            raise StorageError(
                f"An experiment named '{name}' is already registered. "
                "Choose a different name."
            )
        registry[name] = {
            "status": status,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "path": str(path),
        }
        _write_registry_atomic(experiments_dir, registry)


def update_status(experiments_dir: Path, name: str, new_status: str) -> None:
    """Переводит эксперимент в новый статус с проверкой допустимости перехода."""
    if new_status not in STATUSES:
        raise StorageError(
            f"Unknown status '{new_status}'. Allowed: {', '.join(STATUSES)}"
        )
    now = datetime.now(timezone.utc).isoformat()
    with _registry_lock(experiments_dir):
        registry = _read_registry_unlocked(experiments_dir)
        if name not in registry:
            raise StorageError(f"Experiment '{name}' not found in the registry")
        current = registry[name]["status"]
        allowed = _STATUS_TRANSITIONS.get(current, ())
        if new_status not in allowed:
            raise StorageError(
                f"Invalid status transition '{current}' -> '{new_status}'. "
                f"Allowed: {', '.join(allowed) if allowed else 'no transitions'}"
            )
        registry[name]["status"] = new_status
        if new_status == "running":
            registry[name]["started_at"] = now
        elif new_status == "completed":
            registry[name]["completed_at"] = now
        _write_registry_atomic(experiments_dir, registry)


def list_experiments(
    experiments_dir: Path, active_only: bool = False
) -> dict[str, Any]:
    """Возвращает содержимое реестра, опционально только активные (designed/running)."""
    registry = read_registry(experiments_dir)
    if not active_only:
        return registry
    return {
        name: entry
        for name, entry in registry.items()
        if entry["status"] in ("designed", "running")
    }


def experiment_path(experiments_dir: Path, name: str) -> Path:
    return experiments_dir / name


def create_experiment_dir(experiments_dir: Path, name: str) -> Path:
    """Создает папку эксперимента со стандартной структурой. Ошибка, если уже существует."""
    path = experiment_path(experiments_dir, name)
    if path.exists():
        raise StorageError(
            f"Experiment folder '{name}' already exists at path {path}"
        )
    (path / "logs").mkdir(parents=True)
    return path


def save_config(path: Path, config: DesignConfig) -> None:
    """Сохраняет DesignConfig в config.yaml папки эксперимента."""
    config_path = path / "config.yaml"
    data = config.model_dump(mode="json")
    with config_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def load_config(path: Path) -> DesignConfig:
    """Загружает DesignConfig из config.yaml папки эксперимента."""
    config_path = path / "config.yaml"
    if not config_path.exists():
        raise StorageError(f"config.yaml not found in {path}")
    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return DesignConfig.model_validate(data)


def save_assignments(path: Path, assignments: pd.DataFrame) -> None:
    """Сохраняет назначения групп в assignments.parquet."""
    assignments.to_parquet(path / "assignments.parquet", index=False)


def load_assignments(path: Path) -> pd.DataFrame:
    """Загружает назначения групп из assignments.parquet."""
    assignments_path = path / "assignments.parquet"
    if not assignments_path.exists():
        raise StorageError(f"assignments.parquet not found in {path}")
    return pd.read_parquet(assignments_path)


def save_group_samples(path: Path, assignments: pd.DataFrame) -> dict[str, Path]:
    """Пишет CSV-выборку по каждой группе в samples/<group>.csv для передачи в
    продуктовые системы: колонки unit_id, stratum, assigned_at. Разделитель —
    запятая, кодировка UTF-8 без BOM.

    assignments.parquet остается основным рабочим форматом (используется при
    джойне на этапе analyze); CSV в samples/ — дополнительная выгрузка.
    """
    samples_dir = path / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    columns = ["unit_id", "stratum", "assigned_at"]

    paths: dict[str, Path] = {}
    for group_name, group_df in assignments.groupby("group", observed=True):
        csv_path = samples_dir / f"{group_name}.csv"
        group_df[columns].to_csv(csv_path, index=False, encoding="utf-8", lineterminator="\n")
        paths[str(group_name)] = csv_path
    return paths


@dataclass
class ExperimentPaths:
    """Стандартные пути внутри папки эксперимента."""

    root: Path

    @property
    def config(self) -> Path:
        return self.root / "config.yaml"

    @property
    def assignments(self) -> Path:
        return self.root / "assignments.parquet"

    @property
    def design_report(self) -> Path:
        return self.root / "design_report.html"

    @property
    def report(self) -> Path:
        return self.root / "report.html"

    @property
    def results_json(self) -> Path:
        return self.root / "results.json"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def samples_dir(self) -> Path:
        return self.root / "samples"
