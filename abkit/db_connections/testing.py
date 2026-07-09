"""POST /admin/db-connections/{id}/test (DB1): пробное SELECT 1 с понятной
классификацией ошибки. Классификация — best-effort по тексту исключения
(разные драйверы бросают разные классы) — точнее всего для PostgreSQL
(psycopg), для ClickHouse/MSSQL это разумное приближение, задокументированное
как известное ограничение (DB5, README)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from abkit.db_connections.engines import ConnectionSpec, build_engine

TestOutcome = Literal["ok", "host_unreachable", "auth_failed", "db_not_found", "error"]


@dataclass
class ConnectionTestResult:
    outcome: TestOutcome
    message: str


def _classify(exc: Exception) -> ConnectionTestResult:
    text = str(exc).lower()
    if any(
        s in text
        for s in (
            "could not translate host", "could not connect", "connection refused",
            "timeout expired", "timed out", "timeout", "name or service not known",
            "network is unreachable", "no route to host", "max retries exceeded",
            "failed to resolve host", "getaddrinfo failed",
        )
    ):
        return ConnectionTestResult("host_unreachable", "Host unreachable or connection timed out")
    if any(
        s in text
        for s in (
            "password authentication failed", "authentication failed", "access denied",
            "login failed", "login incorrect", "auth_failed",
        )
    ):
        return ConnectionTestResult("auth_failed", "Authentication failed — check username/password")
    if any(
        s in text
        for s in ("unknown database", "cannot open database", 'database "') + (
            ("does not exist",) if "database" in text else ()
        )
    ):
        return ConnectionTestResult("db_not_found", "Database not found on the server")
    return ConnectionTestResult("error", str(exc)[:300])


def test_connection(spec: ConnectionSpec, timeout_sec: int = 10) -> ConnectionTestResult:
    try:
        engine = build_engine(spec, timeout_sec=timeout_sec)
        try:
            with engine.connect() as conn:
                from sqlalchemy import text as sa_text

                conn.execute(sa_text("SELECT 1"))
            return ConnectionTestResult("ok", "Connection successful")
        finally:
            engine.dispose()
    except Exception as e:  # noqa: BLE001 — деталь только для классификации, наружу не течет
        return _classify(e)
