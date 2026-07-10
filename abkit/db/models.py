"""SQLAlchemy ORM-модели схемы Postgres (DOCKER.md, раздел 5).

Используются только в серверном режиме (ABKIT_MODE=db). Файловый режим
(ABKIT_MODE=file, дефолт) продолжает работать через abkit/storage.py и эти
модели не импортирует.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('viewer','editor','admin')", name="ck_users_role"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    first_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class Experiment(Base):
    __tablename__ = "experiments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('designed','running','completed','archived')", name="ck_experiments_status"
        ),
        CheckConstraint(
            "publication_status IN ('draft','published')", name="ck_experiments_publication_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    # Редакционный статус (FRONTEND.md §1/§3.3) — независим от операционного
    # status выше; draft видят владелец+admin, published — все роли.
    publication_status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    # Ограничение видимости по ролям (Properties modal) — null означает
    # дефолтные draft/published-правила выше; список ролей ["editor","admin"]
    # сужает видимость published-эксперимента до этих ролей (плюс owners/
    # editors/admin всегда видят, см. abkit/auth/experiment_access.py).
    visible_roles: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    design_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExperimentAccess(Base):
    """Дополнительные владельцы/редакторы эксперимента поверх Experiment.owner_id
    (Edit Properties modal, как в Superset) — FRONTEND.md, UX-пакет. access='owner'
    дает те же права редактирования, что и оригинальный owner_id; access='editor'
    дает права редактирования без прав владельца (пока это различие не используется
    отдельно — обе роли трактуются одинаково в require_experiment_edit_access)."""

    __tablename__ = "experiment_access"
    __table_args__ = (
        CheckConstraint("access IN ('owner','editor')", name="ck_experiment_access_access"),
        Index("ix_experiment_access_experiment", "experiment_id"),
        Index("ix_experiment_access_user", "user_id"),
        Index(
            "ux_experiment_access_experiment_user", "experiment_id", "user_id", unique=True
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    access: Mapped[str] = mapped_column(Text, nullable=False)


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        Index("ix_assignments_experiment_group", "experiment_id", "group_name"),
        Index("ix_assignments_unit_id", "unit_id"),
    )

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), primary_key=True
    )
    unit_id: Mapped[str] = mapped_column(Text, primary_key=True)
    group_name: Mapped[str] = mapped_column(Text, nullable=False)
    stratum: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('pre_design','post_analysis','validation')", name="ck_datasets_kind"
        ),
        CheckConstraint("source IN ('upload','sql','demo')", name="ck_datasets_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # nullable: датасет kind='pre_design' загружается ДО того, как эксперимент
    # создан (визард шаг 1, FRONTEND.md §3.2: "POST /datasets {..., experiment_id?}");
    # design-джоба привязывает его через DatasetRepo.attach_to_experiment()
    # после успешного создания эксперимента.
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    n_rows: Mapped[int] = mapped_column(BigInteger, nullable=False)
    columns: Mapped[list] = mapped_column(JSONB, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # DB2 (dataset-from-SQL, CLAUDE.md): source distinguishes how the parquet
    # was produced — 'sql' datasets carry connection_id/sql_text so
    # POST /datasets/{id}/refresh can re-run the same query later.
    source: Mapped[str] = mapped_column(Text, nullable=False, default="upload")
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("database_connections.id", ondelete="SET NULL"), nullable=True
    )
    sql_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Datasets follow-up: explicit schema/table the user picked via the
    # From SQL cascade, when sql_text was generated from (or later confirmed
    # to still match) that pick — NULL for hand-written queries the cascade
    # was never used for, or once sql_text has been edited to diverge from
    # it (see abkit/jobs.py::run_update_dataset). Authoritative source for
    # the Edit modal's cascade prefill; re-parsing sql_text is only a
    # fallback for rows this doesn't cover.
    source_schema: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_table: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExperimentDataset(Base):
    """Many-to-many use record (DB3, dataset-centric model, CLAUDE.md): a
    dataset can be selected by more than one experiment, and the same
    experiment can use different datasets for design/analyze/validate.
    Dataset.experiment_id/kind (single link) remain as the PRIMARY/first
    association for backward-compatible reads — this table is the
    authoritative record of every use, populated by abkit/jobs.py whenever
    a dataset is actually used (not merely selected in the UI)."""

    __tablename__ = "experiment_datasets"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('pre_design','post_analysis','validation')", name="ck_experiment_datasets_kind"
        ),
        Index("ix_experiment_datasets_experiment", "experiment_id"),
        Index("ix_experiment_datasets_dataset", "dataset_id"),
        Index(
            "ux_experiment_datasets_experiment_dataset_kind",
            "experiment_id", "dataset_id", "kind", unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    # ondelete="SET NULL" (migration 0009): deleting a dataset must not be
    # blocked by — nor break — the results of experiments that already
    # analyzed it. results.json is self-sufficient; only this live pointer
    # goes null (GET /experiments/{name}/results already handles that).
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )
    # Frozen snapshot of the dataset's filename at analyze time (migration
    # 0009) — results.json is meant to be self-sufficient (UX package,
    # Datasets §2.2), so "what was analyzed" must survive the dataset row
    # itself being deleted later (dataset_id above goes null then), not just
    # degrade to "unknown dataset".
    dataset_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    results: Mapped[dict] = mapped_column(JSONB, nullable=False)
    report_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    user_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    object_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    object_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    object_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class ExperimentBlock(Base):
    """Markdown-блоки страницы теста (FRONTEND.md §1/§3.3): Гипотеза/Выводы/
    Решение — автосоздаются пустыми при создании эксперимента (ExperimentRepo.
    create), плюс произвольные custom-блоки, добавляемые из UI."""

    __tablename__ = "experiment_blocks"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('hypothesis','conclusion','decision','custom')",
            name="ck_experiment_blocks_kind",
        ),
        Index("ix_experiment_blocks_experiment", "experiment_id", "position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Job(Base):
    """Фоновые задачи (design/analyze/validate) — FRONTEND.md §4. Без Celery:
    ThreadPoolExecutor (backend/jobs/runner.py) + эта таблица как источник
    правды для GET /jobs/{id} (переживает рестарт воркера, в отличие от
    состояния в памяти процесса)."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','requires_confirmation','completed','failed')",
            name="ck_jobs_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    progress: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Стемпится на КАЖДОЙ мутации (onupdate — не нужно вручную трогать в
    # каждом методе JobRepo) — heartbeat для sweeper'а (backend/jobs/runner.py
    # ::JobRunner._sweep_stale_jobs), ловящего job, застрявшую в 'running' без
    # прогресса дольше ABKIT_JOB_TIMEOUT_MINUTES (worker умер без исключения,
    # например OOM-killed процесс).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DatabaseConnection(Base):
    """Admin-managed подключение к внешней БД (Database Connections
    feature, CLAUDE.md) — источник для датасетов из SQL (DB2).
    password_encrypted — Fernet-шифротекст (abkit/db_connections/crypto.py),
    никогда не plaintext и никогда не возвращается из API (write-only)."""

    __tablename__ = "database_connections"
    __table_args__ = (
        CheckConstraint(
            "engine IN ('postgresql','clickhouse','mssql')", name="ck_database_connections_engine"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    engine: Mapped[str] = mapped_column(Text, nullable=False)
    host: Mapped[str] = mapped_column(Text, nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    database: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    extra_params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Tag(Base):
    """Tags for A/B tests (Superset-style dashboard tags) — free-form
    labeling for search/grouping (by product/team/feature/etc), not a
    controlled vocabulary. `name` is CITEXT (same case-insensitive-unique
    pattern as User.email) so "Checkout"/"checkout" collide instead of
    silently creating two tags that mean the same thing. `color` exists for
    a future manual color picker (out of scope v1) — the UI currently always
    computes a deterministic color from a hash of the name instead of
    reading this column; nullable and unused by any code path today."""

    __tablename__ = "tags"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    color: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExperimentTag(Base):
    """experiment<->tag many-to-many — a plain link with no extra columns,
    so (unlike ExperimentDataset, which carries a `kind`) a composite PK on
    the pair is the whole story: no surrogate id, ON DELETE CASCADE both
    ways so deleting either side cleans up the link automatically."""

    __tablename__ = "experiment_tags"

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
