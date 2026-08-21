"""Служебные схемы и схема по умолчанию — чистое правило, без подключения.

Живет на сервере, а не в React-компоненте, потому что набор служебных имен —
свойство ДВИЖКА: в компоненте пришлось бы протаскивать engine через три формы
и держать три копии одного списка (прогон против настоящей БД — в
backend/tests/test_dataset_from_sql.py).
"""

from __future__ import annotations

import pytest

from abkit.db_connections.introspection import default_schema, visible_schemas


# ---------------------------------------------------------------------------
# Что скрывается
# ---------------------------------------------------------------------------


def test_postgres_hides_information_schema_and_pg_internals():
    """Именно с этого начался баг-репорт: селектор открывался на
    information_schema, привычных таблиц там нет — и вывод «пикер сломан»
    напрашивался сам."""
    got = visible_schemas(
        "postgresql",
        ["information_schema", "pg_catalog", "pg_toast", "pg_temp_1", "public", "analytics"],
    )
    assert got == ["public", "analytics"]


def test_postgres_prefix_rule_covers_names_we_never_enumerated():
    """pg_* зарезервирован самим постгресом, поэтому правило — префикс, а не
    список: pg_toast_temp_7 не пришлось бы дописывать руками."""
    assert visible_schemas("postgresql", ["pg_toast_temp_7", "sales"]) == ["sales"]


def test_mssql_hides_sys_guest_and_fixed_database_roles():
    got = visible_schemas(
        "mssql",
        ["INFORMATION_SCHEMA", "sys", "guest", "db_owner", "db_datareader", "dbo", "reporting"],
    )
    assert got == ["dbo", "reporting"]


def test_clickhouse_hides_system_and_information_schema():
    got = visible_schemas("clickhouse", ["system", "INFORMATION_SCHEMA", "default", "events"])
    assert got == ["default", "events"]


def test_matching_is_case_insensitive():
    assert visible_schemas("postgresql", ["Information_Schema", "PG_CATALOG", "public"]) == ["public"]


def test_user_schemas_are_never_hidden_by_a_foreign_engines_rule():
    """`db_*` скрывается ТОЛЬКО у MSSQL (там это роли БД). В постгресе схема
    `db_sales` — обычная пользовательская схема, и прятать ее нельзя."""
    assert visible_schemas("postgresql", ["db_sales"]) == ["db_sales"]
    assert visible_schemas("mssql", ["db_sales"]) == []


def test_unknown_engine_hides_nothing():
    """Лучше показать лишнее, чем молча скрыть данные пользователя."""
    assert visible_schemas("duckdb", ["information_schema", "main"]) == [
        "information_schema", "main",
    ]


# ---------------------------------------------------------------------------
# Схема по умолчанию
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("engine", "schemas", "expected"),
    [
        ("postgresql", ["analytics", "public"], "public"),
        ("mssql", ["reporting", "dbo"], "dbo"),
        ("clickhouse", ["events", "default"], "default"),
    ],
)
def test_default_schema_is_picked_when_present(engine, schemas, expected):
    assert default_schema(engine, schemas) == expected


def test_default_schema_is_none_when_absent():
    """Ничего не выдумываем: если public нет, пусть пользователь выберет сам —
    первая попавшаяся схема не «схема по умолчанию»."""
    assert default_schema("postgresql", ["analytics", "staging"]) is None


def test_default_schema_returns_the_spelling_the_database_gave():
    """Имя поедет в кавычках в `SELECT * FROM "schema"."table"`, где регистр
    уже значим — вернуть свой lowercase значило бы сломать запрос."""
    assert default_schema("postgresql", ["PUBLIC"]) == "PUBLIC"


def test_unknown_engine_has_no_default():
    assert default_schema("duckdb", ["main"]) is None
