"""SQL Lab: внедрение лимита и сериализация значений — без БД и без движков.

Прогон против настоящей БД покрыт backend/tests/test_sql_lab_api.py (там же
права и история), браузерный сценарий — frontend/e2e/sql-lab.spec.ts.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from abkit.db_connections.sql_lab import (
    INTERACTIVE_ROW_LIMIT,
    _apply_statement_timeout,
    _jsonable,
    _looks_like_timeout,
    apply_interactive_limit,
    interactive_timeout_sec,
)


# ---------------------------------------------------------------------------
# Внедрение LIMIT (ТЗ п.1: интерактивные прогоны ограничены)
# ---------------------------------------------------------------------------


def test_limit_is_injected_for_postgres():
    sql, pushed = apply_interactive_limit("SELECT id FROM users", "postgresql", 1000)
    assert pushed is True
    assert "LIMIT 1000" in sql.upper()


def test_limit_is_injected_for_clickhouse():
    sql, pushed = apply_interactive_limit("SELECT id FROM users", "clickhouse", 1000)
    assert pushed is True
    assert "LIMIT 1000" in sql.upper()


def test_limit_uses_top_for_mssql():
    """У MSSQL нет LIMIT — конкатенация ' LIMIT 1000' дала бы синтаксическую
    ошибку. sqlglot печатает на нужном диалекте, поэтому получается TOP."""
    sql, pushed = apply_interactive_limit("SELECT id FROM users", "mssql", 1000)
    assert pushed is True
    assert "TOP" in sql.upper()


def test_existing_smaller_limit_is_not_raised():
    """Пользователь попросил 10 строк — незачем давать больше."""
    sql, pushed = apply_interactive_limit("SELECT id FROM users LIMIT 10", "postgresql", 1000)
    assert pushed is True
    assert "LIMIT 10" in sql.upper()
    assert "1000" not in sql


def test_existing_larger_limit_is_capped():
    sql, _ = apply_interactive_limit("SELECT id FROM users LIMIT 50000", "postgresql", 1000)
    assert "LIMIT 1000" in sql.upper()
    assert "50000" not in sql


def test_limit_applied_to_a_cte_query():
    sql, pushed = apply_interactive_limit(
        "WITH t AS (SELECT id FROM users) SELECT * FROM t", "postgresql", 1000
    )
    assert pushed is True
    assert "LIMIT 1000" in sql.upper()


def test_order_by_survives_limit_injection():
    """Наивная конкатенация ломала бы порядок клауз — sqlglot собирает дерево."""
    sql, _ = apply_interactive_limit(
        "SELECT id FROM users ORDER BY id DESC", "postgresql", 1000
    )
    upper = sql.upper()
    assert upper.index("ORDER BY") < upper.index("LIMIT")


def test_unparseable_sql_falls_back_without_raising():
    """sqlglot не всесилен на экзотическом диалектном синтаксисе. Это не повод
    отказывать в выполнении: SELECT-гард запрос уже прошел, а обрезка все
    равно произойдет при чтении (см. execute_interactive)."""
    sql, pushed = apply_interactive_limit("SELECT ??? FROM $$$", "postgresql", 1000)
    assert pushed is False
    assert sql == "SELECT ??? FROM $$$"


def test_non_literal_limit_is_left_alone():
    """Лимит-выражение, которое мы не смогли прочитать, не подменяем: это
    изменило бы смысл запроса. Режем при чтении."""
    sql, pushed = apply_interactive_limit(
        "SELECT id FROM users LIMIT (SELECT 5)", "postgresql", 1000
    )
    assert pushed is False


def test_default_limit_is_a_thousand():
    assert INTERACTIVE_ROW_LIMIT == 1000


# ---------------------------------------------------------------------------
# Таймаут
# ---------------------------------------------------------------------------


def test_interactive_timeout_defaults_to_60s(monkeypatch):
    monkeypatch.delenv("ABKIT_SQL_LAB_TIMEOUT_SEC", raising=False)
    assert interactive_timeout_sec() == 60


def test_interactive_timeout_is_configurable(monkeypatch):
    monkeypatch.setenv("ABKIT_SQL_LAB_TIMEOUT_SEC", "15")
    assert interactive_timeout_sec() == 15


def test_interactive_timeout_is_separate_from_materialization(monkeypatch):
    """У интерактивного прогона за спиной живой человек, у материализации —
    фоновая джоба; одна ручка на оба случая означала бы либо висящий UI, либо
    обрубленную выгрузку."""
    from abkit.db_connections.sql_dataset import _default_timeout_sec

    monkeypatch.setenv("ABKIT_SQL_TIMEOUT_SEC", "300")
    monkeypatch.setenv("ABKIT_SQL_LAB_TIMEOUT_SEC", "60")
    assert interactive_timeout_sec() == 60
    assert _default_timeout_sec() == 300


# ---------------------------------------------------------------------------
# Отмена по таймауту силами самой БД
# ---------------------------------------------------------------------------


class _RecordingConn:
    def __init__(self, fail: bool = False):
        self.statements: list[str] = []
        self.fail = fail

    def exec_driver_sql(self, sql: str):
        if self.fail:
            raise RuntimeError("permission denied to set parameter")
        self.statements.append(sql)


def test_postgres_gets_a_server_side_statement_timeout():
    """Иначе «таймаут» был бы только замером постфактум: запрос доработал бы
    до конца на стороне БД, заняв ее ресурсы."""
    conn = _RecordingConn()
    _apply_statement_timeout(conn, "postgresql", 60)
    assert conn.statements == ["SET statement_timeout = 60000"]  # мс, не секунды


def test_clickhouse_gets_max_execution_time_in_seconds():
    conn = _RecordingConn()
    _apply_statement_timeout(conn, "clickhouse", 45)
    assert conn.statements == ["SET max_execution_time = 45"]


def test_mssql_has_no_per_statement_knob_and_is_left_alone():
    """У pymssql таймаут задается на подключении, а не на запрос — там
    остается проверка постфактум в execute_interactive."""
    conn = _RecordingConn()
    _apply_statement_timeout(conn, "mssql", 60)
    assert conn.statements == []


def test_failure_to_set_the_timeout_does_not_break_the_query():
    """Урезанные права на SET — не повод отказать в выполнении запроса:
    остается backstop-проверка по времени."""
    _apply_statement_timeout(_RecordingConn(fail=True), "postgresql", 60)


@pytest.mark.parametrize(
    "message",
    [
        "canceling statement due to statement timeout",
        "Timeout expired",
        "Code: 159. DB::Exception: Timeout exceeded: max_execution_time",
    ],
)
def test_driver_timeouts_are_recognized_across_engines(message):
    """Тип исключения у каждого драйвера свой, общего предка нет — распознаем
    по тексту, иначе пользователь увидел бы сырое сообщение драйвера."""
    assert _looks_like_timeout(RuntimeError(message)) is True


def test_ordinary_sql_errors_are_not_mistaken_for_timeouts():
    assert _looks_like_timeout(RuntimeError('relation "orders" does not exist')) is False


# ---------------------------------------------------------------------------
# Сериализация значений в JSON
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, True, False, 0, -1, 42, 3.14, "text"])
def test_json_native_values_pass_through(value):
    assert _jsonable(value) == value


def test_nan_and_infinity_become_null():
    """NaN/Inf не проходят строгий JSON — одна такая ячейка делала бы
    невалидным ВЕСЬ ответ, а не только себя."""
    assert _jsonable(float("nan")) is None
    assert _jsonable(float("inf")) is None
    assert _jsonable(float("-inf")) is None
    assert _jsonable(math.nan) is None


def test_dates_decimals_and_uuids_are_stringified():
    assert _jsonable(date(2026, 1, 1)) == "2026-01-01"
    assert _jsonable(datetime(2026, 1, 1, 12, 30)) == "2026-01-01 12:30:00"
    assert _jsonable(Decimal("10.50")) == "10.50"
    uid = UUID("12345678-1234-5678-1234-567812345678")
    assert _jsonable(uid) == str(uid)


def test_binary_is_summarized_not_dumped():
    """Сырые байты в JSON-гриде бесполезны и могут быть огромными."""
    assert _jsonable(b"\x00\x01\x02") == "<3 bytes>"
