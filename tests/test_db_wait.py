"""abkit/db/wait.py — используется docker/entrypoint.sh перед alembic upgrade
(DOCKER.md §7.2, шаг 1)."""

import pytest

from abkit.db.wait import wait_for_postgres


def test_wait_for_postgres_succeeds_immediately_when_available(db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db_url)
    wait_for_postgres(timeout_seconds=10, poll_interval=0.2)


def test_wait_for_postgres_times_out_on_unreachable_host(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:y@localhost:1/nope")
    with pytest.raises(TimeoutError, match="unavailable"):
        wait_for_postgres(timeout_seconds=2, poll_interval=0.3)
