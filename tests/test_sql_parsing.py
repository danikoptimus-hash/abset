"""Backfill helper for migration 0010 (datasets.source_schema/source_table) —
see abkit/db_connections/sql_parsing.py."""

import pytest

from abkit.db_connections.sql_parsing import parse_schema_table_from_sql


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("SELECT * FROM public.users", ("public", "users")),
        ('SELECT * FROM "public"."users"', ("public", "users")),
        ('SELECT * FROM public."users"', ("public", "users")),
        ("select id, email from public.users where role = 'admin'", ("public", "users")),
        ("SELECT * FROM   analytics.events", ("analytics", "events")),
    ],
)
def test_parses_simple_schema_qualified_from(sql, expected):
    assert parse_schema_table_from_sql(sql) == expected


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "   ",
        "SELECT * FROM users",  # unqualified — no schema
        "SELECT a.* FROM public.users a JOIN public.orders o ON a.id = o.user_id",
        "WITH t AS (SELECT * FROM public.users) SELECT * FROM t",
        "SELECT * FROM (SELECT * FROM public.users) sub",
    ],
)
def test_leaves_complex_or_unqualified_queries_unparsed(sql):
    assert parse_schema_table_from_sql(sql) == (None, None)
