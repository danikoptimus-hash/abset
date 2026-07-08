"""Репозитории поверх SQLAlchemy-моделей (DOCKER.md, раздел 8) — слой доступа к
данным для серверного режима (ABKIT_MODE=db). Каждый публичный метод — одна
самостоятельная транзакция (через session_scope); это проще рассуждать о
консистентности, чем делить сессии между вызовами.
"""

from __future__ import annotations

import hashlib
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy import func, insert, select

from abkit import storage
from abkit.db.engine import session_scope
from abkit.db.models import AnalysisResult, AuditLog, Assignment, Dataset, Experiment, User

_ACTIVE_STATUSES = ("designed", "running")


class RepoError(storage.StorageError):
    """Ошибка уровня репозитория. Наследуется от storage.StorageError, чтобы
    существующий `except storage.StorageError` (app.py/cli.py) ловил ошибки
    обоих режимов хранения без изменений."""


class UserRepo:
    """CRUD над users. Хеширование паролей (argon2id) и политика rate-limit —
    ответственность abkit/auth/ (этап D2); репозиторий только хранит/читает."""

    def create(
        self,
        *,
        email: str,
        name: str,
        password_hash: str,
        role: str,
        must_change_password: bool = False,
    ) -> uuid_mod.UUID:
        with session_scope() as s:
            if s.scalar(select(User).where(User.email == email)) is not None:
                raise RepoError(f"Пользователь с email '{email}' уже существует")
            user = User(
                email=email,
                name=name,
                password_hash=password_hash,
                role=role,
                must_change_password=must_change_password,
            )
            s.add(user)
            s.flush()
            return user.id

    def get_by_email(self, email: str) -> User | None:
        with session_scope() as s:
            user = s.scalar(select(User).where(User.email == email))
            if user is not None:
                s.expunge(user)
            return user

    def get_by_id(self, user_id: uuid_mod.UUID) -> User | None:
        with session_scope() as s:
            user = s.get(User, user_id)
            if user is not None:
                s.expunge(user)
            return user

    def list_all(self) -> list[User]:
        with session_scope() as s:
            users = list(s.scalars(select(User).order_by(User.created_at)))
            for u in users:
                s.expunge(u)
            return users

    def count(self) -> int:
        with session_scope() as s:
            return len(list(s.scalars(select(User))))

    def update_role(self, user_id: uuid_mod.UUID, role: str) -> None:
        with session_scope() as s:
            user = s.get(User, user_id)
            if user is None:
                raise RepoError(f"Пользователь {user_id} не найден")
            user.role = role

    def set_active(self, user_id: uuid_mod.UUID, is_active: bool) -> None:
        with session_scope() as s:
            user = s.get(User, user_id)
            if user is None:
                raise RepoError(f"Пользователь {user_id} не найден")
            user.is_active = is_active

    def update_name(self, user_id: uuid_mod.UUID, name: str) -> None:
        with session_scope() as s:
            user = s.get(User, user_id)
            if user is None:
                raise RepoError(f"Пользователь {user_id} не найден")
            user.name = name

    def set_password_hash(
        self, user_id: uuid_mod.UUID, password_hash: str, must_change_password: bool = False
    ) -> None:
        with session_scope() as s:
            user = s.get(User, user_id)
            if user is None:
                raise RepoError(f"Пользователь {user_id} не найден")
            user.password_hash = password_hash
            user.must_change_password = must_change_password

    def record_login_success(self, user_id: uuid_mod.UUID) -> None:
        with session_scope() as s:
            user = s.get(User, user_id)
            if user is None:
                raise RepoError(f"Пользователь {user_id} не найден")
            user.failed_logins = 0
            user.locked_until = None
            user.last_login_at = datetime.now(timezone.utc)

    def record_login_failure(
        self, email: str, *, max_attempts: int = 5, lockout_minutes: int = 15
    ) -> None:
        """Блокировка перебора (DOCKER.md §4.2): после max_attempts подряд
        неудач — блокировка на lockout_minutes. Хранится в БД, не в памяти
        процесса, поэтому переживает рестарт/несколько воркеров."""
        with session_scope() as s:
            user = s.scalar(select(User).where(User.email == email))
            if user is None:
                return
            user.failed_logins += 1
            if user.failed_logins >= max_attempts:
                user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=lockout_minutes)


class ExperimentRepo:
    def create(
        self,
        *,
        name: str,
        owner_id: uuid_mod.UUID,
        status: str,
        config: dict[str, Any],
        design_summary: dict[str, Any] | None = None,
        created_at: datetime | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        archived_at: datetime | None = None,
    ) -> Experiment:
        """created_at/started_at/completed_at/archived_at — обычно проставляются
        БД (server_default/update_status); явные значения нужны только для
        импорта легаси-экспериментов (DOCKER.md §9), чтобы сохранить настоящую
        историю статусов, а не создать новую с текущим временем."""
        with session_scope() as s:
            if s.scalar(select(Experiment).where(Experiment.name == name)) is not None:
                raise RepoError(f"Эксперимент с именем '{name}' уже существует")
            exp = Experiment(
                name=name, owner_id=owner_id, status=status, config=config, design_summary=design_summary
            )
            if created_at is not None:
                exp.created_at = created_at
            if started_at is not None:
                exp.started_at = started_at
            if completed_at is not None:
                exp.completed_at = completed_at
            if archived_at is not None:
                exp.archived_at = archived_at
            s.add(exp)
            s.flush()
            s.refresh(exp)
            s.expunge(exp)
            return exp

    def get_by_name(self, name: str) -> Experiment | None:
        with session_scope() as s:
            exp = s.scalar(select(Experiment).where(Experiment.name == name))
            if exp is not None:
                s.expunge(exp)
            return exp

    def list_all(self, *, active_only: bool = False) -> list[Experiment]:
        with session_scope() as s:
            stmt = select(Experiment)
            if active_only:
                stmt = stmt.where(Experiment.status.in_(_ACTIVE_STATUSES))
            exps = list(s.scalars(stmt.order_by(Experiment.created_at)))
            for e in exps:
                s.expunge(e)
            return exps

    def update_status(self, name: str, new_status: str) -> None:
        with session_scope() as s:
            exp = s.scalar(select(Experiment).where(Experiment.name == name))
            if exp is None:
                raise RepoError(f"Эксперимент '{name}' не найден")
            exp.status = new_status
            now = datetime.now(timezone.utc)
            if new_status == "running":
                exp.started_at = now
            elif new_status == "completed":
                exp.completed_at = now
            elif new_status == "archived":
                exp.archived_at = now

    def delete(self, name: str) -> None:
        """Admin-only (DOCKER.md §4.1, "Удалять эксперименты") — реальное
        удаление строки; assignments/datasets/analysis_results удаляются
        каскадом через FK ON DELETE CASCADE."""
        with session_scope() as s:
            exp = s.scalar(select(Experiment).where(Experiment.name == name))
            if exp is None:
                raise RepoError(f"Эксперимент '{name}' не найден")
            s.delete(exp)


class AssignmentRepo:
    def bulk_insert(self, experiment_id: uuid_mod.UUID, assignments: pd.DataFrame) -> None:
        rows = [
            dict(
                experiment_id=experiment_id,
                unit_id=str(r.unit_id),
                group_name=str(r.group),
                stratum=None if ("stratum" in assignments.columns and pd.isna(r.stratum)) else (
                    str(r.stratum) if "stratum" in assignments.columns else None
                ),
                assigned_at=r.assigned_at,
            )
            for r in assignments.itertuples()
        ]
        if not rows:
            return
        with session_scope() as s:
            s.execute(insert(Assignment), rows)

    def load(self, experiment_id: uuid_mod.UUID) -> pd.DataFrame:
        with session_scope() as s:
            rows = s.execute(
                select(
                    Assignment.unit_id, Assignment.group_name, Assignment.stratum, Assignment.assigned_at
                ).where(Assignment.experiment_id == experiment_id)
            ).all()
        return pd.DataFrame(rows, columns=["unit_id", "group", "stratum", "assigned_at"])

    def count_for_experiment(self, experiment_id: uuid_mod.UUID) -> int:
        """Для диалога подтверждения удаления (app.py) — сколько строк
        реально удалится каскадом вместе с экспериментом."""
        with session_scope() as s:
            return (
                s.scalar(
                    select(func.count()).select_from(Assignment).where(
                        Assignment.experiment_id == experiment_id
                    )
                )
                or 0
            )

    def occupied_units_for_active_experiments(
        self, exclude_experiment_ids: set[uuid_mod.UUID] | None = None
    ) -> dict[str, set[str]]:
        """Один SQL-запрос вместо чтения assignments.parquet каждого активного
        эксперимента по отдельности (DOCKER.md §5)."""
        with session_scope() as s:
            stmt = (
                select(Experiment.name, Assignment.unit_id)
                .join(Assignment, Assignment.experiment_id == Experiment.id)
                .where(Experiment.status.in_(_ACTIVE_STATUSES))
            )
            if exclude_experiment_ids:
                stmt = stmt.where(Experiment.id.notin_(exclude_experiment_ids))
            rows = s.execute(stmt).all()
        occupied: dict[str, set[str]] = {}
        for name, unit_id in rows:
            occupied.setdefault(name, set()).add(unit_id)
        return occupied

    def occupied_units_for_selected_experiments(
        self, experiment_ids: set[uuid_mod.UUID]
    ) -> dict[str, set[str]]:
        """Как occupied_units_for_active_experiments, но "только эти конкретные
        эксперименты" вместо "все активные КРОМЕ этих" (UI: изоляция
        exclude_selected) — и все равно только среди активных статусов."""
        if not experiment_ids:
            return {}
        with session_scope() as s:
            stmt = (
                select(Experiment.name, Assignment.unit_id)
                .join(Assignment, Assignment.experiment_id == Experiment.id)
                .where(Experiment.status.in_(_ACTIVE_STATUSES))
                .where(Experiment.id.in_(experiment_ids))
            )
            rows = s.execute(stmt).all()
        occupied: dict[str, set[str]] = {}
        for name, unit_id in rows:
            occupied.setdefault(name, set()).add(unit_id)
        return occupied


class DatasetRepo:
    def create(
        self,
        *,
        experiment_id: uuid_mod.UUID,
        kind: str,
        filename: str,
        n_rows: int,
        columns: list[str],
        storage_path: str,
        sha256: str,
        uploaded_by: uuid_mod.UUID | None = None,
    ) -> uuid_mod.UUID:
        with session_scope() as s:
            ds = Dataset(
                experiment_id=experiment_id,
                kind=kind,
                filename=filename,
                n_rows=n_rows,
                columns=columns,
                storage_path=storage_path,
                sha256=sha256,
                uploaded_by=uploaded_by,
            )
            s.add(ds)
            s.flush()
            return ds.id

    def list_for_experiment(self, experiment_id: uuid_mod.UUID) -> list[Dataset]:
        with session_scope() as s:
            rows = list(
                s.scalars(
                    select(Dataset)
                    .where(Dataset.experiment_id == experiment_id)
                    .order_by(Dataset.uploaded_at)
                )
            )
            for r in rows:
                s.expunge(r)
            return rows

    @staticmethod
    def compute_sha256(data: pd.DataFrame) -> str:
        return hashlib.sha256(pd.util.hash_pandas_object(data, index=True).to_numpy().tobytes()).hexdigest()

    def count_for_experiment(self, experiment_id: uuid_mod.UUID) -> int:
        with session_scope() as s:
            return (
                s.scalar(
                    select(func.count()).select_from(Dataset).where(Dataset.experiment_id == experiment_id)
                )
                or 0
            )


class ResultRepo:
    def create(
        self,
        *,
        experiment_id: uuid_mod.UUID,
        results: dict[str, Any],
        report_path: str,
        dataset_id: uuid_mod.UUID | None = None,
        created_by: uuid_mod.UUID | None = None,
    ) -> uuid_mod.UUID:
        with session_scope() as s:
            r = AnalysisResult(
                experiment_id=experiment_id,
                dataset_id=dataset_id,
                results=results,
                report_path=report_path,
                created_by=created_by,
            )
            s.add(r)
            s.flush()
            return r.id

    def latest_for_experiment(self, experiment_id: uuid_mod.UUID) -> AnalysisResult | None:
        with session_scope() as s:
            r = s.scalar(
                select(AnalysisResult)
                .where(AnalysisResult.experiment_id == experiment_id)
                .order_by(AnalysisResult.created_at.desc())
                .limit(1)
            )
            if r is not None:
                s.expunge(r)
            return r

    def count_for_experiment(self, experiment_id: uuid_mod.UUID) -> int:
        with session_scope() as s:
            return (
                s.scalar(
                    select(func.count()).select_from(AnalysisResult).where(
                        AnalysisResult.experiment_id == experiment_id
                    )
                )
                or 0
            )


class AuditRepo:
    def log(
        self,
        *,
        action: str,
        user_id: uuid_mod.UUID | None = None,
        user_email: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        object_name: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with session_scope() as s:
            entry = AuditLog(
                action=action,
                user_id=user_id,
                user_email=user_email,
                object_type=object_type,
                object_id=object_id,
                object_name=object_name,
                details=details,
            )
            s.add(entry)

    def _filtered(self, stmt, *, user_id, action, object_name, date_from, date_to):
        if user_id is not None:
            stmt = stmt.where(AuditLog.user_id == user_id)
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)
        if object_name is not None:
            stmt = stmt.where(AuditLog.object_name == object_name)
        if date_from is not None:
            stmt = stmt.where(AuditLog.ts >= date_from)
        if date_to is not None:
            stmt = stmt.where(AuditLog.ts <= date_to)
        return stmt

    def list_recent(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        user_id: uuid_mod.UUID | None = None,
        action: str | None = None,
        object_name: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[AuditLog]:
        with session_scope() as s:
            stmt = select(AuditLog).order_by(AuditLog.ts.desc(), AuditLog.id.desc())
            stmt = self._filtered(
                stmt, user_id=user_id, action=action, object_name=object_name,
                date_from=date_from, date_to=date_to,
            )
            stmt = stmt.offset(offset).limit(limit)
            rows = list(s.scalars(stmt))
            for r in rows:
                s.expunge(r)
            return rows

    def count(
        self,
        *,
        user_id: uuid_mod.UUID | None = None,
        action: str | None = None,
        object_name: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> int:
        with session_scope() as s:
            stmt = select(func.count()).select_from(AuditLog)
            stmt = self._filtered(
                stmt, user_id=user_id, action=action, object_name=object_name,
                date_from=date_from, date_to=date_to,
            )
            return s.scalar(stmt) or 0
