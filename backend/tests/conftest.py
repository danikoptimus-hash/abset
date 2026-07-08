"""Фикстуры backend-тестов. db_url/postgres_url — из корневого conftest.py
(общие с tests/, testcontainers либо TEST_DATABASE_URL — см. DOCKER.md §12)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(db_url, monkeypatch):
    monkeypatch.setenv("ABKIT_SECRET_KEY", "a-real-generated-secret-for-backend-tests")
    # Experiment.design()/.load() выбирают файловый/db-режим по ABKIT_MODE, а
    # не по DATABASE_URL (см. abkit/experiment_store.get_experiment_store());
    # monkeypatch гарантирует откат после теста, в отличие от os.environ[...]=.
    monkeypatch.setenv("ABKIT_MODE", "db")

    from backend.main import create_app

    app = create_app()
    with TestClient(app) as client:
        yield client
