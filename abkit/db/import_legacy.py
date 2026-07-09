"""Импорт легаси (файловый режим, ABKIT_MODE=file) реестра экспериментов в
серверный режим (ABKIT_MODE=db) — DOCKER.md §9. Используется CLI-командой
`abkit-admin import-legacy --dir ... --owner ...`.

Идемпотентно: повторный запуск не дублирует уже импортированные эксперименты
(сверка по имени — тому же уникальному ключу, что и в самой БД).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from abkit import storage
from abkit.db.repositories import AssignmentRepo, ExperimentRepo, ResultRepo, UserRepo
from abkit.db.store import get_data_dir


class LegacyImportError(Exception):
    """Ошибка уровня импорта (например, владелец не найден)."""


@dataclass
class ImportReport:
    imported: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def import_legacy_dir(legacy_dir: Path, owner_email: str) -> ImportReport:
    """Читает legacy_dir/registry.json и папки экспериментов (config.yaml,
    assignments.parquet, design_report.html, report.html, results.json —
    DOCKER.md §9) и создает соответствующие записи в Postgres + копирует
    HTML/JSON-артефакты на ABKIT_DATA_DIR. Одна ошибка на один эксперимент не
    прерывает импорт остальных — репорт содержит и успехи, и провалы.
    """
    owner = UserRepo().get_by_email(owner_email)
    if owner is None:
        raise LegacyImportError(f"Owner user '{owner_email}' not found")

    registry = storage.read_registry(legacy_dir)
    report = ImportReport()
    exp_repo = ExperimentRepo()
    assign_repo = AssignmentRepo()
    data_dir = get_data_dir()

    for name, entry in registry.items():
        if exp_repo.get_by_name(name) is not None:
            report.skipped_existing.append(name)
            continue
        try:
            exp_path = Path(entry["path"])
            config = storage.load_config(exp_path)
            assignments = storage.load_assignments(exp_path)

            exp_row = exp_repo.create(
                name=name,
                owner_id=owner.id,
                status=entry["status"],
                config=config.model_dump(mode="json"),
                created_at=_parse_dt(entry.get("created_at")),
                started_at=_parse_dt(entry.get("started_at")),
                completed_at=_parse_dt(entry.get("completed_at")),
            )
            assign_repo.bulk_insert(exp_row.id, assignments)

            target_dir = data_dir / name
            target_dir.mkdir(parents=True, exist_ok=True)
            for artifact in ("design_report.html", "report.html", "results.json"):
                src = exp_path / artifact
                if src.exists():
                    shutil.copy2(src, target_dir / artifact)

            results_json_path = exp_path / "results.json"
            if results_json_path.exists():
                results_data = json.loads(results_json_path.read_text(encoding="utf-8"))
                ResultRepo().create(
                    experiment_id=exp_row.id,
                    results=results_data,
                    report_path=str(target_dir / "report.html"),
                )

            report.imported.append(name)
        except Exception as e:  # noqa: BLE001 — одна ошибка не должна прерывать импорт остальных
            report.failed[name] = str(e)

    return report
