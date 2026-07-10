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
from abkit.db.models import (
    AnalysisResult,
    AuditLog,
    Assignment,
    DatabaseConnection,
    Dataset,
    Experiment,
    ExperimentAccess,
    ExperimentBlock,
    ExperimentDataset,
    ExperimentTag,
    Job,
    Tag,
    User,
)

_ACTIVE_STATUSES = ("designed", "running")
_DEFAULT_BLOCK_KINDS = ("hypothesis", "conclusion", "decision")


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
        first_name: str,
        last_name: str = "",
        password_hash: str,
        role: str,
        must_change_password: bool = False,
    ) -> uuid_mod.UUID:
        with session_scope() as s:
            if s.scalar(select(User).where(User.email == email)) is not None:
                raise RepoError(f"A user with email '{email}' already exists")
            user = User(
                email=email,
                first_name=first_name,
                last_name=last_name,
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
        # created_at desc: same reasoning as DatabaseConnectionRepo.list_all()
        # — a freshly created/modified user must be visible on the Admin
        # Users list's first page (default AntD Table page size 10) without
        # hunting through pagination.
        with session_scope() as s:
            users = list(s.scalars(select(User).order_by(User.created_at.desc())))
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
                raise RepoError(f"User {user_id} not found")
            user.role = role

    def set_active(self, user_id: uuid_mod.UUID, is_active: bool) -> None:
        with session_scope() as s:
            user = s.get(User, user_id)
            if user is None:
                raise RepoError(f"User {user_id} not found")
            user.is_active = is_active

    def update_name(self, user_id: uuid_mod.UUID, first_name: str, last_name: str) -> None:
        with session_scope() as s:
            user = s.get(User, user_id)
            if user is None:
                raise RepoError(f"User {user_id} not found")
            user.first_name = first_name
            user.last_name = last_name

    def set_password_hash(
        self, user_id: uuid_mod.UUID, password_hash: str, must_change_password: bool = False
    ) -> None:
        with session_scope() as s:
            user = s.get(User, user_id)
            if user is None:
                raise RepoError(f"User {user_id} not found")
            user.password_hash = password_hash
            user.must_change_password = must_change_password

    def record_login_success(self, user_id: uuid_mod.UUID) -> None:
        with session_scope() as s:
            user = s.get(User, user_id)
            if user is None:
                raise RepoError(f"User {user_id} not found")
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
        publication_status: str = "draft",
        created_at: datetime | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        archived_at: datetime | None = None,
    ) -> Experiment:
        """created_at/started_at/completed_at/archived_at — обычно проставляются
        БД (server_default/update_status); явные значения нужны только для
        импорта легаси-экспериментов (DOCKER.md §9), чтобы сохранить настоящую
        историю статусов, а не создать новую с текущим временем.

        Автосоздает пустые markdown-блоки hypothesis/conclusion/decision в той
        же транзакции (FRONTEND.md §3.3: "При создании эксперимента
        автосоздаются пустые hypothesis/conclusion/decision")."""
        with session_scope() as s:
            if s.scalar(select(Experiment).where(Experiment.name == name)) is not None:
                raise RepoError(f"An experiment named '{name}' already exists")
            exp = Experiment(
                name=name, owner_id=owner_id, status=status, config=config,
                design_summary=design_summary, publication_status=publication_status,
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
            for position, kind in enumerate(_DEFAULT_BLOCK_KINDS):
                s.add(ExperimentBlock(experiment_id=exp.id, kind=kind, position=position))
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
        """Newest first — the default (unfiltered, unsearched) list view is
        meant to surface recent activity, same as "Last Modified" sorting
        implies; oldest-first was hiding brand new experiments off the first
        page entirely once the table passed page_size rows."""
        with session_scope() as s:
            stmt = select(Experiment)
            if active_only:
                stmt = stmt.where(Experiment.status.in_(_ACTIVE_STATUSES))
            exps = list(s.scalars(stmt.order_by(Experiment.created_at.desc())))
            for e in exps:
                s.expunge(e)
            return exps

    def update_status(self, name: str, new_status: str) -> None:
        with session_scope() as s:
            exp = s.scalar(select(Experiment).where(Experiment.name == name))
            if exp is None:
                raise RepoError(f"Experiment '{name}' not found")
            exp.status = new_status
            now = datetime.now(timezone.utc)
            if new_status == "running":
                exp.started_at = now
            elif new_status == "completed":
                exp.completed_at = now
            elif new_status == "archived":
                exp.archived_at = now

    def update_publication_status(self, name: str, publication_status: str) -> None:
        """draft<->published — переходы обратимы в обе стороны (FRONTEND.md §3.3)."""
        with session_scope() as s:
            exp = s.scalar(select(Experiment).where(Experiment.name == name))
            if exp is None:
                raise RepoError(f"Experiment '{name}' not found")
            exp.publication_status = publication_status

    def update_visible_roles(self, name: str, visible_roles: list[str] | None) -> None:
        """Properties modal (UX-пакет) — null снимает ограничение видимости,
        возвращая дефолтные draft/published-правила (см. abkit/access.py)."""
        with session_scope() as s:
            exp = s.scalar(select(Experiment).where(Experiment.name == name))
            if exp is None:
                raise RepoError(f"Experiment '{name}' not found")
            exp.visible_roles = visible_roles

    def rename(self, name: str, new_name: str) -> None:
        with session_scope() as s:
            exp = s.scalar(select(Experiment).where(Experiment.name == name))
            if exp is None:
                raise RepoError(f"Experiment '{name}' not found")
            if new_name != name and s.scalar(select(Experiment).where(Experiment.name == new_name)) is not None:
                raise RepoError(f"An experiment named '{new_name}' already exists")
            exp.name = new_name

    def delete(self, name: str) -> None:
        """Admin-only (DOCKER.md §4.1, "Удалять эксперименты") — реальное
        удаление строки; assignments/datasets/analysis_results удаляются
        каскадом через FK ON DELETE CASCADE."""
        with session_scope() as s:
            exp = s.scalar(select(Experiment).where(Experiment.name == name))
            if exp is None:
                raise RepoError(f"Experiment '{name}' not found")
            s.delete(exp)


class ExperimentAccessRepo:
    """Дополнительные владельцы/редакторы эксперимента (Edit Properties modal,
    UX-пакет) — поверх Experiment.owner_id. См. abkit/access.py для того, как
    эти строки комбинируются с owner_id/admin в проверках прав/видимости."""

    def list_for_experiment(self, experiment_id: uuid_mod.UUID) -> list[ExperimentAccess]:
        with session_scope() as s:
            rows = list(
                s.scalars(
                    select(ExperimentAccess).where(ExperimentAccess.experiment_id == experiment_id)
                )
            )
            for r in rows:
                s.expunge(r)
            return rows

    def set_for_experiment(
        self, experiment_id: uuid_mod.UUID, grants: list[tuple[uuid_mod.UUID, str]]
    ) -> None:
        """Заменяет ВЕСЬ список грантов эксперимента (owners/editors мультиселекты
        в Properties modal сохраняются целиком, не по одному) — grants: список
        (user_id, access) пар, access in ('owner','editor')."""
        with session_scope() as s:
            s.query(ExperimentAccess).filter(
                ExperimentAccess.experiment_id == experiment_id
            ).delete(synchronize_session=False)
            for user_id, access in grants:
                s.add(ExperimentAccess(experiment_id=experiment_id, user_id=user_id, access=access))

    def user_has_access(self, experiment_id: uuid_mod.UUID, user_id: uuid_mod.UUID) -> bool:
        with session_scope() as s:
            return (
                s.scalar(
                    select(ExperimentAccess.id).where(
                        ExperimentAccess.experiment_id == experiment_id,
                        ExperimentAccess.user_id == user_id,
                    )
                )
                is not None
            )

    def experiment_ids_for_user(self, user_id: uuid_mod.UUID) -> set[uuid_mod.UUID]:
        """Один запрос вместо N+1 — для фильтрации списка экспериментов по
        видимости (GET /experiments), где can_view_experiment() иначе делал бы
        по запросу на каждую строку."""
        with session_scope() as s:
            rows = s.execute(
                select(ExperimentAccess.experiment_id).where(ExperimentAccess.user_id == user_id)
            ).all()
        return {r[0] for r in rows}


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
        kind: str,
        filename: str,
        n_rows: int,
        columns: list[str],
        storage_path: str,
        sha256: str,
        experiment_id: uuid_mod.UUID | None = None,
        uploaded_by: uuid_mod.UUID | None = None,
        source: str = "upload",
        connection_id: uuid_mod.UUID | None = None,
        sql_text: str | None = None,
        fetched_at: datetime | None = None,
        source_schema: str | None = None,
        source_table: str | None = None,
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
                source=source,
                connection_id=connection_id,
                sql_text=sql_text,
                fetched_at=fetched_at,
                source_schema=source_schema,
                source_table=source_table,
            )
            s.add(ds)
            s.flush()
            return ds.id

    def update_after_refresh(
        self, dataset_id: uuid_mod.UUID, *, n_rows: int, columns: list[str], sha256: str
    ) -> None:
        """POST /datasets/{id}/refresh (source='sql', DB2): re-ran sql_text,
        parquet on disk was overwritten in place — update the row's stats to
        match, storage_path/sql_text/connection_id are unchanged."""
        with session_scope() as s:
            ds = s.get(Dataset, dataset_id)
            if ds is None:
                raise RepoError(f"Dataset {dataset_id} not found")
            ds.n_rows = n_rows
            ds.columns = columns
            ds.sha256 = sha256
            ds.fetched_at = datetime.now(timezone.utc)

    def get_by_id(self, dataset_id: uuid_mod.UUID) -> Dataset | None:
        with session_scope() as s:
            ds = s.get(Dataset, dataset_id)
            if ds is not None:
                s.expunge(ds)
            return ds

    def delete(self, dataset_id: uuid_mod.UUID) -> None:
        """Реальное удаление строки (Actions -> Delete, UX-пакет Datasets
        §2.2 — usage-проверка и подтверждение делает вызывающая сторона,
        abkit/jobs.py::run_delete_dataset). Файл на диске (storage_path)
        удаляет вызывающая сторона."""
        with session_scope() as s:
            ds = s.get(Dataset, dataset_id)
            if ds is None:
                raise RepoError(f"Dataset {dataset_id} not found")
            s.delete(ds)

    def rename(self, dataset_id: uuid_mod.UUID, filename: str) -> None:
        """Edit dataset -> name (UX-пакет Datasets §2.3) — для любого source."""
        with session_scope() as s:
            ds = s.get(Dataset, dataset_id)
            if ds is None:
                raise RepoError(f"Dataset {dataset_id} not found")
            ds.filename = filename

    def update_sql_source(
        self,
        dataset_id: uuid_mod.UUID,
        *,
        connection_id: uuid_mod.UUID | None,
        sql_text: str | None,
        source_schema: str | None = None,
        source_table: str | None = None,
    ) -> None:
        """Edit dataset -> connection/SQL for source='sql' (UX-пакет Datasets
        §2.3) — только меняет что ИСПОЛЬЗОВАТЬ при следующем фетче; сам
        re-fetch (n_rows/columns/sha256/fetched_at) делает отдельный джоб,
        переиспользующий run_refresh_sql_dataset ПОСЛЕ этого вызова.
        source_schema/source_table ВСЕГДА перезаписываются вместе с sql_text
        (Datasets follow-up) — вызывающая сторона (run_update_dataset)
        передает их, только если текущий SQL все еще ТОЧНО совпадает со
        сгенерированным для этого выбора запросом; иначе — None, чтобы не
        хранить устаревшее/неверное указание источника."""
        with session_scope() as s:
            ds = s.get(Dataset, dataset_id)
            if ds is None:
                raise RepoError(f"Dataset {dataset_id} not found")
            ds.connection_id = connection_id
            ds.sql_text = sql_text
            ds.source_schema = source_schema
            ds.source_table = source_table

    def attach_to_experiment(self, dataset_id: uuid_mod.UUID, experiment_id: uuid_mod.UUID) -> None:
        """Привязывает pre_design датасет (загружен до создания эксперимента,
        experiment_id=None) к только что созданному эксперименту — вызывается
        design-джобой после успешного Experiment.design()."""
        with session_scope() as s:
            ds = s.get(Dataset, dataset_id)
            if ds is None:
                raise RepoError(f"Dataset {dataset_id} not found")
            ds.experiment_id = experiment_id

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

    def list_all(self) -> list[Dataset]:
        """Для страницы /datasets (FRONTEND.md §5.2) — датасеты всех
        экспериментов, не только одного."""
        with session_scope() as s:
            rows = list(s.scalars(select(Dataset).order_by(Dataset.uploaded_at.desc())))
            for r in rows:
                s.expunge(r)
            return rows

    @staticmethod
    def compute_sha256(data: pd.DataFrame) -> str:
        return hashlib.sha256(pd.util.hash_pandas_object(data, index=True).to_numpy().tobytes()).hexdigest()

    @staticmethod
    def compute_sha256_from_file(path: str) -> str:
        """Streaming file-byte hash — for source='sql' datasets (DB2), which
        are written to parquet chunk-by-chunk without ever materializing the
        full DataFrame in memory; compute_sha256(df) would defeat that."""
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def count_for_experiment(self, experiment_id: uuid_mod.UUID) -> int:
        with session_scope() as s:
            return (
                s.scalar(
                    select(func.count()).select_from(Dataset).where(Dataset.experiment_id == experiment_id)
                )
                or 0
            )


class ExperimentDatasetRepo:
    """Many-to-many use record (DB3, dataset-centric model) — see
    abkit.db.models.ExperimentDataset. link() is idempotent: re-selecting
    the same dataset for the same experiment+kind is a no-op, not a new row."""

    def link(self, experiment_id: uuid_mod.UUID, dataset_id: uuid_mod.UUID, kind: str) -> None:
        with session_scope() as s:
            exists = s.scalar(
                select(ExperimentDataset.id).where(
                    ExperimentDataset.experiment_id == experiment_id,
                    ExperimentDataset.dataset_id == dataset_id,
                    ExperimentDataset.kind == kind,
                )
            )
            if exists is None:
                s.add(ExperimentDataset(experiment_id=experiment_id, dataset_id=dataset_id, kind=kind))

    def list_for_experiment(self, experiment_id: uuid_mod.UUID) -> list[ExperimentDataset]:
        with session_scope() as s:
            rows = list(
                s.scalars(
                    select(ExperimentDataset)
                    .where(ExperimentDataset.experiment_id == experiment_id)
                    .order_by(ExperimentDataset.created_at.desc())
                )
            )
            for r in rows:
                s.expunge(r)
            return rows

    def experiments_using_dataset(self, dataset_id: uuid_mod.UUID) -> list[uuid_mod.UUID]:
        """Distinct experiment ids that have actually used this dataset
        (design/analyze/validate) — dataset delete usage-check (UX package,
        Datasets п.2.2)."""
        with session_scope() as s:
            return list(
                s.scalars(
                    select(ExperimentDataset.experiment_id)
                    .where(ExperimentDataset.dataset_id == dataset_id)
                    .distinct()
                )
            )


class ResultRepo:
    def create(
        self,
        *,
        experiment_id: uuid_mod.UUID,
        results: dict[str, Any],
        report_path: str,
        dataset_id: uuid_mod.UUID | None = None,
        dataset_filename: str | None = None,
        created_by: uuid_mod.UUID | None = None,
    ) -> uuid_mod.UUID:
        with session_scope() as s:
            r = AnalysisResult(
                experiment_id=experiment_id,
                dataset_id=dataset_id,
                dataset_filename=dataset_filename,
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

    def _filtered(self, stmt, *, user_id, user_email, action, object_name, date_from, date_to):
        if user_id is not None:
            stmt = stmt.where(AuditLog.user_id == user_id)
        if user_email is not None:
            # user_email денормализован в audit_log (переживает удаление
            # пользователя), поэтому фильтруем по нему напрямую, а не через
            # user_id — так находятся и записи "осиротевших" пользователей.
            stmt = stmt.where(AuditLog.user_email == user_email)
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
        user_email: str | None = None,
        action: str | None = None,
        object_name: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[AuditLog]:
        with session_scope() as s:
            stmt = select(AuditLog).order_by(AuditLog.ts.desc(), AuditLog.id.desc())
            stmt = self._filtered(
                stmt, user_id=user_id, user_email=user_email, action=action, object_name=object_name,
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
        user_email: str | None = None,
        action: str | None = None,
        object_name: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> int:
        with session_scope() as s:
            stmt = select(func.count()).select_from(AuditLog)
            stmt = self._filtered(
                stmt, user_id=user_id, user_email=user_email, action=action, object_name=object_name,
                date_from=date_from, date_to=date_to,
            )
            return s.scalar(stmt) or 0


class BlockRepo:
    """Markdown-блоки страницы теста (FRONTEND.md §1/§3.3). Дефолтные
    hypothesis/conclusion/decision создаются в ExperimentRepo.create();
    здесь — чтение и upsert-списком (PUT /experiments/{name}/blocks)."""

    def list_for_experiment(self, experiment_id: uuid_mod.UUID) -> list[ExperimentBlock]:
        with session_scope() as s:
            rows = list(
                s.scalars(
                    select(ExperimentBlock)
                    .where(ExperimentBlock.experiment_id == experiment_id)
                    .order_by(ExperimentBlock.position)
                )
            )
            for r in rows:
                s.expunge(r)
            return rows

    def upsert_many(
        self,
        experiment_id: uuid_mod.UUID,
        blocks: list[dict[str, Any]],
        updated_by: uuid_mod.UUID | None = None,
    ) -> list[ExperimentBlock]:
        """blocks: список {id?, kind, title, content_md, position}. id задан и
        принадлежит этому эксперименту -> обновление; иначе -> создание новой
        строки (обычно custom-блок). Существующие custom-блоки, чьих id нет в
        списке, удаляются (это и есть "удалить блок" в UI) — блоки
        hypothesis/conclusion/decision никогда не удаляются этим методом, даже
        если отсутствуют в списке."""
        with session_scope() as s:
            existing = list(
                s.scalars(select(ExperimentBlock).where(ExperimentBlock.experiment_id == experiment_id))
            )
            existing_by_id = {e.id: e for e in existing}
            kept_ids: set[uuid_mod.UUID] = set()

            result: list[ExperimentBlock] = []
            for block in blocks:
                # block["id"] приходит строкой из API (BlockIn.id: str | None) —
                # existing_by_id ключуется uuid.UUID из ORM, без преобразования
                # lookup ВСЕГДА промахивался, и апдейт превращался в дубликат
                # новой строки вместо обновления существующей.
                raw_id = block.get("id")
                block_id = uuid_mod.UUID(raw_id) if raw_id else None
                existing_block = existing_by_id.get(block_id) if block_id else None
                if existing_block is not None:
                    existing_block.title = block.get("title", existing_block.title)
                    existing_block.content_md = block.get("content_md", existing_block.content_md)
                    existing_block.position = block.get("position", existing_block.position)
                    existing_block.updated_by = updated_by
                    kept_ids.add(existing_block.id)
                    result.append(existing_block)
                else:
                    new_block = ExperimentBlock(
                        experiment_id=experiment_id,
                        kind=block.get("kind", "custom"),
                        title=block.get("title", ""),
                        content_md=block.get("content_md", ""),
                        position=block.get("position", 0),
                        updated_by=updated_by,
                    )
                    s.add(new_block)
                    s.flush()
                    kept_ids.add(new_block.id)
                    result.append(new_block)

            for e in existing:
                if e.kind != "custom":
                    continue
                if e.id not in kept_ids:
                    s.delete(e)

            # Явный flush ПЕРЕД refresh(): апдейты существующих блоков (title/
            # content_md/position выше) не флашились по отдельности (в отличие
            # от новых блоков, для которых flush() уже был вызван) — без этого
            # refresh() ниже перечитывает из БД состояние ДО этих изменений и
            # молча их стирает из возвращаемых объектов (сами данные в БД к
            # этому моменту уже корректны, но ответ API показывает старые).
            s.flush()

            result.sort(key=lambda b: b.position)
            for r in result:
                s.refresh(r)
                s.expunge(r)
            return result


class JobRepo:
    """Фоновые задачи (FRONTEND.md §4) — источник правды для GET /jobs/{id},
    переживает рестарт процесса (в отличие от состояния ThreadPoolExecutor
    в памяти, см. backend/jobs/runner.py)."""

    def create(self, *, type: str, created_by: uuid_mod.UUID | None = None) -> Job:
        with session_scope() as s:
            job = Job(type=type, status="pending", created_by=created_by)
            s.add(job)
            s.flush()
            s.refresh(job)
            s.expunge(job)
            return job

    def get_by_id(self, job_id: uuid_mod.UUID) -> Job | None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is not None:
                s.expunge(job)
            return job

    def mark_running(self, job_id: uuid_mod.UUID) -> None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is not None:
                job.status = "running"

    def update_progress(self, job_id: uuid_mod.UUID, progress: dict[str, Any]) -> None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is not None:
                job.progress = progress

    def mark_completed(self, job_id: uuid_mod.UUID, result_ref: dict[str, Any] | None = None) -> None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is not None:
                job.status = "completed"
                job.result_ref = result_ref
                job.finished_at = datetime.now(timezone.utc)

    def mark_requires_confirmation(self, job_id: uuid_mod.UUID, result_ref: dict[str, Any]) -> None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is not None:
                job.status = "requires_confirmation"
                job.result_ref = result_ref
                job.finished_at = datetime.now(timezone.utc)

    def mark_failed(self, job_id: uuid_mod.UUID, error: str) -> None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is not None:
                job.status = "failed"
                job.error = error
                job.finished_at = datetime.now(timezone.utc)

    def list_unfinished(self) -> list[Job]:
        with session_scope() as s:
            rows = list(
                s.scalars(select(Job).where(Job.status.in_(("pending", "running"))))
            )
            for r in rows:
                s.expunge(r)
            return rows

    def list_stale_running(self, older_than: datetime) -> list[Job]:
        """'running' jobs whose updated_at heartbeat hasn't moved since
        `older_than` — a worker that died without raising a catchable
        exception (see JobRunner._sweep_stale_jobs)."""
        with session_scope() as s:
            rows = list(
                s.scalars(
                    select(Job).where(Job.status == "running", Job.updated_at < older_than)
                )
            )
            for r in rows:
                s.expunge(r)
            return rows


class DatabaseConnectionRepo:
    """Admin-managed подключения к внешним БД (DB1) — CRUD только по id
    (список для UI короткий, страничность не нужна, как и у users)."""

    def create(
        self,
        *,
        display_name: str,
        engine: str,
        host: str,
        port: int,
        database: str,
        username: str,
        password_encrypted: str,
        extra_params: dict[str, Any] | None = None,
        ssl: bool = False,
        created_by: uuid_mod.UUID | None = None,
    ) -> DatabaseConnection:
        with session_scope() as s:
            conn = DatabaseConnection(
                display_name=display_name, engine=engine, host=host, port=port,
                database=database, username=username, password_encrypted=password_encrypted,
                extra_params=extra_params, ssl=ssl, created_by=created_by,
            )
            s.add(conn)
            s.flush()
            s.refresh(conn)
            s.expunge(conn)
            return conn

    def get_by_id(self, conn_id: uuid_mod.UUID) -> DatabaseConnection | None:
        with session_scope() as s:
            conn = s.get(DatabaseConnection, conn_id)
            if conn is not None:
                s.expunge(conn)
            return conn

    def list_all(self) -> list[DatabaseConnection]:
        # created_at desc: a freshly created connection must be visible on
        # the admin list's first page (default AntD Table page size 10)
        # without hunting through pagination — alphabetical-by-name ordering
        # buried new rows once enough connections accumulated.
        with session_scope() as s:
            rows = list(s.scalars(select(DatabaseConnection).order_by(DatabaseConnection.created_at.desc())))
            for r in rows:
                s.expunge(r)
            return rows

    def update(
        self,
        conn_id: uuid_mod.UUID,
        *,
        display_name: str | None = None,
        engine: str | None = None,
        host: str | None = None,
        port: int | None = None,
        database: str | None = None,
        username: str | None = None,
        password_encrypted: str | None = None,
        extra_params: dict[str, Any] | None = None,
        ssl: bool | None = None,
    ) -> DatabaseConnection:
        with session_scope() as s:
            conn = s.get(DatabaseConnection, conn_id)
            if conn is None:
                raise RepoError(f"Database connection {conn_id} not found")
            if display_name is not None:
                conn.display_name = display_name
            if engine is not None:
                conn.engine = engine
            if host is not None:
                conn.host = host
            if port is not None:
                conn.port = port
            if database is not None:
                conn.database = database
            if username is not None:
                conn.username = username
            # password_encrypted: only overwritten when a new password was
            # actually provided (write-only field, "unchanged" placeholder
            # in the UI means "keep the existing encrypted value").
            if password_encrypted is not None:
                conn.password_encrypted = password_encrypted
            if extra_params is not None:
                conn.extra_params = extra_params
            if ssl is not None:
                conn.ssl = ssl
            s.flush()
            s.refresh(conn)
            s.expunge(conn)
            return conn

    def delete(self, conn_id: uuid_mod.UUID) -> None:
        with session_scope() as s:
            conn = s.get(DatabaseConnection, conn_id)
            if conn is None:
                raise RepoError(f"Database connection {conn_id} not found")
            s.delete(conn)


class TagRepo:
    """Tags (Superset-style A/B test tags, CLAUDE.md). name is CITEXT
    (case-insensitive unique) — get_or_create() leans on that: "type to
    create" from the UI should never error just because someone already made
    "Checkout" and this user typed "checkout"; it should silently reuse it."""

    def get_or_create(self, name: str, *, created_by: uuid_mod.UUID | None = None) -> Tag:
        with session_scope() as s:
            existing = s.scalar(select(Tag).where(Tag.name == name))
            if existing is not None:
                s.expunge(existing)
                return existing
            tag = Tag(name=name, created_by=created_by)
            s.add(tag)
            s.flush()
            s.refresh(tag)
            s.expunge(tag)
            return tag

    def get_by_id(self, tag_id: uuid_mod.UUID) -> Tag | None:
        with session_scope() as s:
            tag = s.get(Tag, tag_id)
            if tag is not None:
                s.expunge(tag)
            return tag

    def search(self, q: str | None, *, limit: int = 20) -> list[Tag]:
        """Typeahead (GET /tags?q=) — substring match, case-insensitive
        (CITEXT ILIKE-equivalent via plain `like` since the column type
        itself is already case-insensitive)."""
        with session_scope() as s:
            stmt = select(Tag).order_by(Tag.name).limit(limit)
            if q:
                stmt = stmt.where(Tag.name.like(f"%{q}%"))
            rows = list(s.scalars(stmt))
            for r in rows:
                s.expunge(r)
            return rows

    def delete(self, tag_id: uuid_mod.UUID) -> None:
        """Admin-only (enforced by the caller) — ON DELETE CASCADE on
        experiment_tags removes it from every experiment automatically."""
        with session_scope() as s:
            tag = s.get(Tag, tag_id)
            if tag is None:
                raise RepoError(f"Tag {tag_id} not found")
            s.delete(tag)


class ExperimentTagRepo:
    def set_for_experiment(self, experiment_id: uuid_mod.UUID, tag_ids: list[uuid_mod.UUID]) -> None:
        """PUT /experiments/{name}/tags — full replace: the caller always
        sends the COMPLETE desired tag list, not a delta."""
        with session_scope() as s:
            current = set(
                s.scalars(
                    select(ExperimentTag.tag_id).where(ExperimentTag.experiment_id == experiment_id)
                )
            )
            desired = set(tag_ids)
            for tag_id in current - desired:
                s.execute(
                    ExperimentTag.__table__.delete().where(
                        ExperimentTag.experiment_id == experiment_id, ExperimentTag.tag_id == tag_id,
                    )
                )
            for tag_id in desired - current:
                s.add(ExperimentTag(experiment_id=experiment_id, tag_id=tag_id))

    def list_for_experiment(self, experiment_id: uuid_mod.UUID) -> list[Tag]:
        with session_scope() as s:
            rows = list(
                s.scalars(
                    select(Tag)
                    .join(ExperimentTag, ExperimentTag.tag_id == Tag.id)
                    .where(ExperimentTag.experiment_id == experiment_id)
                    .order_by(Tag.name)
                )
            )
            for r in rows:
                s.expunge(r)
            return rows

    def list_for_experiments(self, experiment_ids: list[uuid_mod.UUID]) -> dict[uuid_mod.UUID, list[Tag]]:
        """Bulk fetch for the experiments list page's Tags column — one
        query for a whole page instead of N+1."""
        if not experiment_ids:
            return {}
        with session_scope() as s:
            rows = list(
                s.execute(
                    select(ExperimentTag.experiment_id, Tag)
                    .join(Tag, Tag.id == ExperimentTag.tag_id)
                    .where(ExperimentTag.experiment_id.in_(experiment_ids))
                    .order_by(Tag.name)
                )
            )
            # A tag used by more than one experiment comes back as the SAME
            # Tag object (identity map) across multiple rows — expunge it
            # only once, or the second expunge raises (already removed).
            expunged: set[int] = set()
            result: dict[uuid_mod.UUID, list[Tag]] = {eid: [] for eid in experiment_ids}
            for experiment_id, tag in rows:
                if id(tag) not in expunged:
                    s.expunge(tag)
                    expunged.add(id(tag))
                result[experiment_id].append(tag)
            return result

    def count_for_tag(self, tag_id: uuid_mod.UUID) -> int:
        """How many experiments use this tag — DELETE /tags/{id}
        confirmation (UX package, Tags §3.2)."""
        with session_scope() as s:
            return s.scalar(
                select(func.count()).select_from(ExperimentTag).where(ExperimentTag.tag_id == tag_id)
            )
