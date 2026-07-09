"""Ждёт доступности Postgres — вызывается docker/entrypoint.sh перед `alembic
upgrade head` (DOCKER.md §7.2, шаг 1: "Ждать доступности Postgres, таймаут 60с").

Реализовано через psycopg (уже зависимость проекта) вместо системной утилиты
pg_isready — не требует ставить postgresql-client в образ ради одной проверки.
"""

from __future__ import annotations

import sys
import time

import psycopg

from abkit.db.engine import get_database_url


def wait_for_postgres(timeout_seconds: int = 60, poll_interval: float = 1.0) -> None:
    """Блокируется, пока Postgres не примет подключение, либо не истечет таймаут."""
    dsn = get_database_url().replace("postgresql+psycopg://", "postgresql://")
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=3):
                return
        except Exception as e:  # noqa: BLE001 — любая ошибка подключения = "еще не готов"
            last_error = e
            time.sleep(poll_interval)
    raise TimeoutError(f"Postgres unavailable after {timeout_seconds}s: {last_error}")


if __name__ == "__main__":
    timeout = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    try:
        wait_for_postgres(timeout)
    except TimeoutError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    print("Postgres is available.")
