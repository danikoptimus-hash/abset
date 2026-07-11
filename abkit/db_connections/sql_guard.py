"""SELECT-only валидация SQL перед выполнением против внешней БД (DB2,
CLAUDE.md). Это вторая линия обороны, а не единственная — основная
рекомендация (README/DOCKER.md) — заводить для ABSet read-only пользователя
БД. Здесь блокируется как очевидный DML/DDL на верхнем уровне, так и трюк
"запись внутри CTE" (WITH t AS (INSERT ... RETURNING *) SELECT * FROM t) —
sqlglot дает дерево целиком, проверяем не только корневой узел, а весь
walk()."""

from __future__ import annotations

import sqlglot
from sqlglot import exp

_ALLOWED_TOP_LEVEL = (exp.Select, exp.Union)

_BLOCKED_NODE_TYPES = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
    exp.TruncateTable, exp.Merge, exp.Grant, exp.Command, exp.Copy,
)

_DIALECT_BY_ENGINE = {"postgresql": "postgres", "clickhouse": "clickhouse", "mssql": "tsql"}


class SqlValidationError(Exception):
    """SQL отклонен ДО выполнения — не read-only запрос или несколько
    операторов в одном тексте."""


def sqlglot_dialect(engine: str) -> str:
    return _DIALECT_BY_ENGINE.get(engine, engine)


def validate_select_only(sql: str, engine: str) -> None:
    dialect = sqlglot_dialect(engine)
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as e:
        raise SqlValidationError(f"Could not parse SQL: {e}") from e

    if len(statements) == 0:
        raise SqlValidationError("Empty query")
    if len(statements) != 1:
        raise SqlValidationError("Only a single SELECT statement is allowed (no multiple statements)")

    stmt = statements[0]
    if not isinstance(stmt, _ALLOWED_TOP_LEVEL):
        raise SqlValidationError(
            f"Only SELECT/WITH queries are allowed, got '{type(stmt).__name__}'"
        )
    if isinstance(stmt, exp.Select) and stmt.args.get("locks"):
        raise SqlValidationError("Row-locking clauses (FOR UPDATE/FOR SHARE) are not allowed")

    for node in stmt.walk():
        actual = node[0] if isinstance(node, tuple) else node
        if isinstance(actual, _BLOCKED_NODE_TYPES):
            raise SqlValidationError(
                f"Query contains a disallowed statement ('{type(actual).__name__}') — "
                "only read-only SELECT/WITH queries are allowed"
            )
