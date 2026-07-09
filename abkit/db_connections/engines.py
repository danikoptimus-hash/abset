"""Поддерживаемые движки внешних БД (DB1, CLAUDE.md) — построение
SQLAlchemy URL + connect_args из полей DatabaseConnection.

Драйверы:
- PostgreSQL: psycopg (v3) — уже основная зависимость приложения (своя же БД),
  переиспользуется и для пользовательских подключений.
- ClickHouse: clickhouse-connect — официальный HTTP-клиент ClickHouse Inc.,
  регистрирует SQLAlchemy-диалект "clickhousedb" поверх HTTP(S) (не "родной"
  TCP-протокол 9000/9440) — поэтому дефолтные порты здесь 8123 (plain) /
  8443 (TLS), а не 9000/9440.
- MSSQL: pymssql (обертка над FreeTDS) вместо pyodbc — не требует системного
  Microsoft ODBC-драйвера (лицензионный EULA, отдельная установка в образе),
  ставится обычным pip wheel'ом.

Опциональные зависимости (extras [db-connectors], pyproject.toml) — движок
конкретного подключения импортируется лениво (см. build_engine), чтобы
базовая установка abkit не тянула драйверы БД, которыми не пользуется.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_DEFAULT_PORTS: dict[str, dict[str, int]] = {
    "postgresql": {"plain": 5432, "ssl": 5432},
    "clickhouse": {"plain": 8123, "ssl": 8443},
    "mssql": {"plain": 1433, "ssl": 1433},
}

ENGINE_CHOICES = ("postgresql", "clickhouse", "mssql")


def default_port(engine: str, ssl: bool) -> int:
    ports = _DEFAULT_PORTS.get(engine)
    if ports is None:
        raise ValueError(f"Unknown engine '{engine}'")
    return ports["ssl" if ssl else "plain"]


@dataclass
class ConnectionSpec:
    """Расшифрованные поля подключения — держится в памяти только на время
    построения engine/выполнения запроса, никогда не персистится как есть."""

    engine: str
    host: str
    port: int
    database: str
    username: str
    password: str
    ssl: bool = False
    extra_params: dict[str, Any] | None = None


def build_url(spec: ConnectionSpec) -> str:
    from urllib.parse import quote_plus

    user = quote_plus(spec.username)
    password = quote_plus(spec.password)
    host = spec.host
    port = spec.port
    database = spec.database

    if spec.engine == "postgresql":
        url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}"
        if spec.ssl:
            url += "?sslmode=require"
        return url
    if spec.engine == "clickhouse":
        # clickhouse-connect only registers a single SQLAlchemy dialect name
        # ("clickhousedb", no +http/+https driver suffix) — TLS is toggled
        # via the `secure` query param instead (passed straight through to
        # clickhouse_connect.get_client(**url.query)).
        url = f"clickhousedb://{user}:{password}@{host}:{port}/{database}"
        if spec.ssl:
            url += "?secure=true"
        return url
    if spec.engine == "mssql":
        url = f"mssql+pymssql://{user}:{password}@{host}:{port}/{database}"
        return url
    raise ValueError(f"Unknown engine '{spec.engine}'")


def connect_args(spec: ConnectionSpec, timeout_sec: int) -> dict[str, Any]:
    """connect_args differ per driver's own timeout keyword."""
    extra = dict(spec.extra_params or {})
    if spec.engine == "postgresql":
        return {"connect_timeout": timeout_sec, **extra}
    if spec.engine == "clickhouse":
        return {"connect_timeout": timeout_sec, **extra}
    if spec.engine == "mssql":
        if spec.ssl:
            extra.setdefault("tds_version", "7.4")
        return {"login_timeout": timeout_sec, **extra}
    raise ValueError(f"Unknown engine '{spec.engine}'")


def build_engine(spec: ConnectionSpec, timeout_sec: int = 10):
    """create_engine() lazily imports each driver package via SQLAlchemy's
    own dialect loading (clickhouse_connect / pymssql) only when that
    engine is actually used — no explicit import needed here. Without the
    [db-connectors] extra installed, this raises a normal ImportError for
    clickhouse/mssql; PostgreSQL always works (psycopg is a core
    dependency, already used for abkit's own DB)."""
    from sqlalchemy import create_engine

    return create_engine(build_url(spec), connect_args=connect_args(spec, timeout_sec))
