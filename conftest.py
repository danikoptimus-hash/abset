"""Общие фикстуры для тестов серверного режима (ABKIT_MODE=db, DOCKER.md).

postgres_url поднимает Postgres через testcontainers (локальная разработка,
нужен запущенный Docker) либо использует TEST_DATABASE_URL из окружения, если
он уже задан (CI — см. .github/workflows/ci.yml, Postgres через services).
Если ни то, ни другое недоступно — тесты, зависящие от postgres_url,
skip'аются, а не падают (файловый режим и статистическое ядро от Postgres не
зависят и продолжают работать без Docker).
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

# Ryuk (testcontainers' resource-reaper sidecar) требует отдельный образ; наша
# фикстура и так делает container.stop() в finally, так что для локального
# прогона тестов отключаем его по умолчанию (можно переопределить снаружи) —
# это также защищает от нестабильной сети при первом pull образа реапера.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")


def _apply_migrations(db_url: str) -> None:
    root = os.path.dirname(os.path.abspath(__file__))  # conftest.py живет в корне репозитория
    env = {**os.environ, "DATABASE_URL": db_url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head упал:\nstdout={result.stdout}\nstderr={result.stderr}"
        )


@pytest.fixture(scope="session")
def postgres_url():
    external = os.environ.get("TEST_DATABASE_URL")
    if external:
        _apply_migrations(external)
        yield external
        return

    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers не установлен и TEST_DATABASE_URL не задан")

    try:
        # postgres:15 вместо 16-alpine из DOCKER.md — этот тег уже был закэширован
        # локально, что важно на нестабильной сети; PROD docker-compose.yml (этап
        # D4) по-прежнему будет использовать postgres:16-alpine, версия тестовой
        # БД тут не принципиальна (схема без версия-специфичных фич 16-й ветки).
        container = PostgresContainer("postgres:15", driver="psycopg")
        container.start()
    except Exception as e:  # Docker недоступен/не запущен
        pytest.skip(f"Не удалось поднять Postgres через testcontainers (Docker недоступен?): {e}")
        return

    try:
        db_url = container.get_connection_url()
        _apply_migrations(db_url)
        yield db_url
    finally:
        container.stop()


@pytest.fixture
def db_url(postgres_url):
    """Per-test: сбрасывает синглтон engine, чистит все таблицы, выставляет
    DATABASE_URL в окружение — тесты репозиториев изолированы друг от друга."""
    from sqlalchemy import create_engine, text

    os.environ["DATABASE_URL"] = postgres_url
    from abkit.db.engine import reset_engine

    reset_engine()

    engine = create_engine(postgres_url, future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE audit_log, analysis_results, datasets, "
                "assignments, experiment_blocks, jobs, database_connections, experiments, users "
                "RESTART IDENTITY CASCADE"
            )
        )
    engine.dispose()

    yield postgres_url
    reset_engine()
