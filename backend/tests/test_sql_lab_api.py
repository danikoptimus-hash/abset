"""SQL Lab и датасеты с плейсхолдерами — против реальной БД.

Внешняя БД, к которой ходит SQL Lab, здесь ЕСТЬ: тестовый Postgres, поднятый
testcontainers'ом для самого приложения, регистрируется как обычное
Database Connection и служит источником — то есть проверяется настоящий путь
(подключение -> гард -> выполнение -> история), а не заглушка.
"""

from __future__ import annotations

import time

import pytest

from abkit.auth.passwords import hash_password
from abkit.db.repositories import DatasetRepo, SqlLabQueryRepo, UserRepo


def _login(app_client, email="editor@co.com", role="editor"):
    UserRepo().create(
        email=email, first_name="E", password_hash=hash_password("pw12345"), role=role
    )
    resp = app_client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345"})
    assert resp.status_code == 200
    return resp.json()["id"]


def _pg_params(db_url: str) -> dict:
    """Разбирает URL тестового Postgres в поля формы подключения."""
    from urllib.parse import urlsplit

    parsed = urlsplit(db_url.replace("postgresql+psycopg://", "postgresql://"))
    return {
        "display_name": f"selftest_{int(time.time() * 1000)}",
        "engine": "postgresql",
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "database": (parsed.path or "/postgres").lstrip("/"),
        "username": parsed.username or "postgres",
        "password": parsed.password or "",
    }


@pytest.fixture
def connection_id(app_client, db_url):
    """Подключение к тому же Postgres, в котором живет само приложение —
    у него гарантированно есть таблицы (users и т.п.), на которых можно
    осмысленно проверить и выполнение, и превью."""
    # Создание подключения — admin-only; логинимся админом, потом остальные
    # тесты работают своим пользователем.
    UserRepo().create(
        email="connadmin@co.com", first_name="A",
        password_hash=hash_password("pw12345"), role="admin",
    )
    app_client.post("/api/v1/auth/login", json={"email": "connadmin@co.com", "password": "pw12345"})
    resp = app_client.post("/api/v1/admin/db-connections", json=_pg_params(db_url))
    assert resp.status_code == 201, resp.text
    conn_id = resp.json()["id"]
    app_client.post("/api/v1/auth/logout")
    return conn_id


# ---------------------------------------------------------------------------
# Выполнение
# ---------------------------------------------------------------------------


def test_run_returns_grid_with_row_count_and_elapsed(app_client, connection_id):
    _login(app_client)
    resp = app_client.post(
        "/api/v1/sql-lab/run",
        json={"connection_id": connection_id, "sql": "SELECT email, role FROM users"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["columns"] == ["email", "role"]
    assert body["n_rows"] >= 1
    assert body["elapsed_ms"] >= 0
    assert body["row_limit"] == 1000
    assert body["truncated"] is False
    assert all(set(row) == {"email", "role"} for row in body["rows"])


def test_run_requires_editor(app_client, connection_id):
    _login(app_client, email="viewer@co.com", role="viewer")
    resp = app_client.post(
        "/api/v1/sql-lab/run", json={"connection_id": connection_id, "sql": "SELECT 1 AS x"}
    )
    assert resp.status_code == 403


def test_select_only_guard_still_blocks_dml(app_client, connection_id):
    """SQL Lab не дает НОВЫХ прав — тот же гард, что у превью запроса."""
    _login(app_client)
    for sql in (
        "DELETE FROM users",
        "UPDATE users SET role = 'admin'",
        "DROP TABLE users",
        "WITH t AS (INSERT INTO users(email) VALUES ('x') RETURNING *) SELECT * FROM t",
    ):
        resp = app_client.post(
            "/api/v1/sql-lab/run", json={"connection_id": connection_id, "sql": sql}
        )
        assert resp.status_code == 422, f"{sql!r} must be refused"
        assert resp.json()["error"]["code"] == "sql_validation_error"


def test_multiple_statements_are_refused(app_client, connection_id):
    _login(app_client)
    resp = app_client.post(
        "/api/v1/sql-lab/run",
        json={"connection_id": connection_id, "sql": "SELECT 1; DROP TABLE users"},
    )
    assert resp.status_code == 422


def test_interactive_limit_caps_the_result(app_client, connection_id):
    """1000-строчный потолок применяется, и о нём сказано явно — иначе тысячу
    строк примут за весь результат."""
    _login(app_client)
    resp = app_client.post(
        "/api/v1/sql-lab/run",
        json={
            "connection_id": connection_id,
            "sql": "SELECT generate_series AS n FROM generate_series(1, 5000)",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_rows"] == 1000
    assert body["truncated"] is True
    assert any("1000" in w for w in body["warnings"])


def test_query_error_is_reported_not_a_500(app_client, connection_id):
    _login(app_client)
    resp = app_client.post(
        "/api/v1/sql-lab/run",
        json={"connection_id": connection_id, "sql": "SELECT * FROM no_such_table_here"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "sql_execution_error"


def test_slow_query_is_cancelled_by_the_database_not_merely_timed(
    app_client, connection_id, monkeypatch
):
    """Запрос должен быть СНЯТ сервером БД, а не просто отработать до конца с
    последующей жалобой на превышение бюджета: во втором случае «таймаут»
    ничего не ограничивает — ни время ожидания, ни нагрузку на источник.

    pg_sleep(10) при бюджете в 1 секунду: укладываемся в пару секунд — значит
    оборвали; вернулись через десять — значит только замерили."""
    monkeypatch.setenv("ABKIT_SQL_LAB_TIMEOUT_SEC", "1")
    _login(app_client)
    started = time.monotonic()
    resp = app_client.post(
        "/api/v1/sql-lab/run",
        json={"connection_id": connection_id, "sql": "SELECT pg_sleep(10) AS slept"},
    )
    elapsed = time.monotonic() - started

    assert resp.status_code == 422, resp.text
    assert "interactive timeout" in resp.json()["error"]["message"]
    assert elapsed < 5, f"запрос не был оборван сервером БД: ждали {elapsed:.1f}s"


def test_unknown_connection_is_404(app_client):
    _login(app_client)
    resp = app_client.post(
        "/api/v1/sql-lab/run",
        json={"connection_id": "00000000-0000-0000-0000-000000000000", "sql": "SELECT 1"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# История
# ---------------------------------------------------------------------------


def test_history_records_successful_runs(app_client, connection_id):
    _login(app_client)
    app_client.post(
        "/api/v1/sql-lab/run",
        json={"connection_id": connection_id, "sql": "SELECT 42 AS answer"},
    )
    items = app_client.get("/api/v1/sql-lab/history").json()["items"]
    assert items[0]["sql_text"] == "SELECT 42 AS answer"
    assert items[0]["error"] is None
    assert items[0]["n_rows"] == 1
    assert items[0]["connection_name"]


def test_history_records_failed_runs_too(app_client, connection_id):
    """К упавшим запросам возвращаются чаще всего («что я там написал?»)."""
    _login(app_client)
    app_client.post(
        "/api/v1/sql-lab/run",
        json={"connection_id": connection_id, "sql": "SELECT * FROM nope_missing"},
    )
    items = app_client.get("/api/v1/sql-lab/history").json()["items"]
    assert items[0]["error"] is not None
    assert items[0]["n_rows"] is None


def test_history_is_per_user(app_client, connection_id):
    """Текст чужого SQL (имена таблиц, условия) читать нельзя никому."""
    _login(app_client, email="alice@co.com")
    app_client.post(
        "/api/v1/sql-lab/run",
        json={"connection_id": connection_id, "sql": "SELECT 'alice-secret' AS x"},
    )
    app_client.post("/api/v1/auth/logout")

    _login(app_client, email="bob@co.com")
    app_client.post(
        "/api/v1/sql-lab/run", json={"connection_id": connection_id, "sql": "SELECT 'bob' AS x"}
    )
    items = app_client.get("/api/v1/sql-lab/history").json()["items"]
    texts = [i["sql_text"] for i in items]
    assert any("bob" in t for t in texts)
    assert not any("alice-secret" in t for t in texts)


def test_history_is_not_readable_by_admin_either(app_client, connection_id):
    _login(app_client, email="worker@co.com")
    app_client.post(
        "/api/v1/sql-lab/run",
        json={"connection_id": connection_id, "sql": "SELECT 'worker-only' AS x"},
    )
    app_client.post("/api/v1/auth/logout")

    _login(app_client, email="nosy-admin@co.com", role="admin")
    items = app_client.get("/api/v1/sql-lab/history").json()["items"]
    assert not any("worker-only" in i["sql_text"] for i in items)


def test_history_is_trimmed_to_the_cap(app_client, connection_id):
    """Без подрезки таблица растет линейно от активности."""
    user_id = _login(app_client, email="busy@co.com")
    import uuid as uuid_mod

    repo = SqlLabQueryRepo()
    for i in range(SqlLabQueryRepo.MAX_PER_USER + 10):
        repo.record(
            user_id=uuid_mod.UUID(user_id), connection_id=None, connection_name="c",
            sql_text=f"SELECT {i}", duration_ms=1, n_rows=1, error=None,
        )
    assert len(repo.list_for_user(uuid_mod.UUID(user_id), limit=500)) == SqlLabQueryRepo.MAX_PER_USER


def test_history_can_be_cleared(app_client, connection_id):
    _login(app_client)
    app_client.post(
        "/api/v1/sql-lab/run", json={"connection_id": connection_id, "sql": "SELECT 1 AS x"}
    )
    assert app_client.get("/api/v1/sql-lab/history").json()["items"]
    assert app_client.delete("/api/v1/sql-lab/history").status_code == 204
    assert app_client.get("/api/v1/sql-lab/history").json()["items"] == []
