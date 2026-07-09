"""FastAPI backend (FRONTEND.md) — REST API поверх существующего ядра abkit.
Точка входа: `uvicorn backend.main:app`. Единственный интерфейс к БД (React-UI
+ CLI) с этапа R8 FRONTEND.md — Streamlit (app.py) удален."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.errors import register_exception_handlers
from backend.jobs import JobRunner
from backend.routers import admin as admin_router
from backend.routers import audit as audit_router
from backend.routers import auth as auth_router
from backend.routers import datasets as datasets_router
from backend.routers import db_connections as db_connections_router
from backend.routers import design as design_router
from backend.routers import experiments as experiments_router
from backend.routers import jobs as jobs_router
from backend.routers import users as users_router


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Backend существует только поверх Postgres — но Experiment.design()/.load()
    # решают, файловый или db-режим использовать, по ABKIT_MODE (не по
    # DATABASE_URL!, см. abkit/experiment_store.get_experiment_store()).
    # setdefault, не безусловное присваивание: если тест уже выставил
    # ABKIT_MODE через monkeypatch (backend/tests/conftest.py), не перебиваем
    # это — иначе monkeypatch не сможет корректно откатить значение после теста.
    os.environ.setdefault("ABKIT_MODE", "db")

    # Fail fast (DOCKER.md §3): без настоящего ABKIT_SECRET_KEY сервис не
    # должен подниматься вообще, а не падать на первом логине.
    from abkit.auth.tokens import get_secret_key

    get_secret_key()

    runner = JobRunner()
    # FRONTEND.md §4: незавершенные (pending/running) jobs с прошлого запуска
    # процесса помечаются failed — их future-объекты потеряны вместе с ним.
    runner.mark_unfinished_jobs_failed_on_startup()
    # В ТЕЧЕНИЕ жизни этого процесса: job, застрявшая в 'running' без
    # прогресса дольше ABKIT_JOB_TIMEOUT_MINUTES (worker умер без исключения,
    # например OOM-killed) — не ждем следующего рестарта backend'а.
    runner.start_heartbeat_sweeper()
    app.state.job_runner = runner
    try:
        yield
    finally:
        runner.shutdown(wait=False)


def create_app() -> FastAPI:
    from abkit import PRODUCT_NAME, __version__

    app = FastAPI(
        title=f"{PRODUCT_NAME} API",
        version=__version__,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        lifespan=_lifespan,
    )

    register_exception_handlers(app)

    # Нужен только для локальной разработки фронта (vite dev server на другом
    # порту/origin) — в проде frontend и /api/* на одном origin через nginx
    # (FRONTEND.md §2), там CORS не участвует вообще.
    dev_origins = [o.strip() for o in os.environ.get("ABKIT_CORS_ORIGINS", "").split(",") if o.strip()]
    if dev_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=dev_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(auth_router.router, prefix="/api/v1")
    app.include_router(experiments_router.router, prefix="/api/v1")
    app.include_router(datasets_router.router, prefix="/api/v1")
    app.include_router(db_connections_router.router, prefix="/api/v1")
    app.include_router(admin_router.router, prefix="/api/v1")
    app.include_router(audit_router.router, prefix="/api/v1")
    app.include_router(design_router.router, prefix="/api/v1")
    app.include_router(jobs_router.router, prefix="/api/v1")
    app.include_router(users_router.router, prefix="/api/v1")

    @app.get("/api/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/v1/version")
    def version_info() -> dict[str, str]:
        """Settings > About (UX package) — the only public (no-auth) way for
        the frontend to know what to display; not sensitive information."""
        return {"product_name": PRODUCT_NAME, "version": __version__}

    return app


app = create_app()
