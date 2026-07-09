"""database_connections (DB1, CLAUDE.md "Database Connections" feature):
admin-managed connections to external databases (PostgreSQL/ClickHouse/
MSSQL) used to create datasets from SQL (DB2). Passwords are stored Fernet-
encrypted (abkit/db_connections/crypto.py), never in plaintext.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-11
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "database_connections",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("engine", sa.Text(), nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("database", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column("extra_params", postgresql.JSONB(), nullable=True),
        sa.Column("ssl", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "engine IN ('postgresql','clickhouse','mssql')", name="ck_database_connections_engine"
        ),
    )


def downgrade() -> None:
    op.drop_table("database_connections")
