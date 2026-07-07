"""Engine/Session-фабрика для серверного режима (ABKIT_MODE=db).

DATABASE_URL берется из окружения (см. .env.example) — единственный источник
истины про то, куда подключаться; никаких путей/секретов в settings.yaml.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


class DatabaseConfigError(Exception):
    """DATABASE_URL не задан или некорректен."""


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise DatabaseConfigError(
            "DATABASE_URL не задан в окружении — обязателен для ABKIT_MODE=db "
            "(см. .env.example)"
        )
    return url


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Синглтон-движок для процесса (пересоздается только между тестами через reset_engine)."""
    global _engine
    if _engine is None:
        _engine = create_engine(get_database_url(), pool_pre_ping=True, future=True)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    """Контекстный менеджер сессии с автокоммитом/роллбэком — основной способ
    работы с БД в репозиториях."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Сбрасывает синглтон engine/session factory — нужно в тестах, где
    DATABASE_URL меняется между тест-кейсами (testcontainers поднимает новый
    Postgres на каждый запуск с новым портом)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
