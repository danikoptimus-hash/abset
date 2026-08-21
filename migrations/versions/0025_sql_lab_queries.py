"""sql_lab_queries: per-user SQL Lab query history

SQL Lab держит историю последних запросов пользователя, чтобы к ним можно было
вернуться одним кликом (та же идея, что в Superset's SQL Lab → Query history).

Почему отдельная таблица, а не audit_log: audit_log — журнал ИЗМЕНЕНИЙ данных и
прав, по нему разбирают инциденты. Прочитанные запросы там были бы шумом, к
тому же истории нужны свои поля (длительность, число строк, текст ошибки) и
своя политика хранения (последние 50 на пользователя, остальное подрезается),
чего у аудита нет и быть не должно.

История ЛИЧНАЯ: user_id + ON DELETE CASCADE. Чужие запросы видеть нельзя —
в тексте запроса легко оказываются имена таблиц и условия, которые сами по
себе чувствительны.

connection_id — ON DELETE SET NULL, не CASCADE: удаление подключения не должно
стирать историю (текст запроса остается полезен, его можно перевыполнить на
другом подключении), поэтому ссылка просто обнуляется.

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sql_lab_queries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("database_connections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Денормализовано: подключение могут переименовать или удалить, а в
        # истории должно остаться видно, ГДЕ запрос выполнялся.
        sa.Column("connection_name", sa.Text(), nullable=True),
        sa.Column("sql_text", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("n_rows", sa.Integer(), nullable=True),
        # NULL — запрос отработал; текст — упал. Хранится и то, и другое:
        # неудачные попытки в истории полезнее всего (к ним и возвращаются).
        sa.Column("error", sa.Text(), nullable=True),
    )
    # Единственный запрос к этой таблице — "последние N этого пользователя".
    op.create_index(
        "ix_sql_lab_queries_user_started",
        "sql_lab_queries",
        ["user_id", sa.text("started_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_sql_lab_queries_user_started", table_name="sql_lab_queries")
    op.drop_table("sql_lab_queries")
