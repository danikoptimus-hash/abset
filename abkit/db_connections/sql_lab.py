"""SQL Lab — интерактивное выполнение запроса против подключенной БД.

Отличается от материализации датасета (sql_dataset.py) ровно двумя вещами, и
обе — про то, что здесь человек сидит и ждет ответа:

- результат ОГРАНИЧЕН (INTERACTIVE_ROW_LIMIT): смотреть в браузере миллион
  строк невозможно, а тянуть их через сеть — только занимать соединение к БД.
  Полная выгрузка живет в создании датасета, куда SQL Lab и передает запрос;
- таймаут короткий (INTERACTIVE_TIMEOUT_SEC, по умолчанию 60с против 300с у
  материализации): зависший интерактивный запрос надо оборвать быстро, у него
  за спиной живой пользователь, а не фоновая джоба.

SELECT-only-гард (sql_guard.py) применяется БЕЗ изменений — SQL Lab не дает
никаких новых прав по сравнению с уже существующим превью запроса.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import sqlglot
from sqlalchemy import text as sa_text

from abkit.db_connections.engines import ConnectionSpec, build_engine
from abkit.db_connections.sql_dataset import SqlExecutionError
from abkit.db_connections.sql_guard import sqlglot_dialect, validate_select_only
from abkit.logging_config import get_logger

log = get_logger(__name__)

# Сколько строк максимум возвращает интерактивный прогон. Не настраивается
# через env намеренно: это предел ЧИТАЕМОСТИ (столько человек все равно не
# просмотрит) и предел объема JSON-ответа, а не ресурсная политика. Кому нужно
# больше — тому нужен датасет, а не грид в браузере.
INTERACTIVE_ROW_LIMIT = 1000

_CONNECT_TIMEOUT_SEC = 15


def interactive_timeout_sec() -> int:
    """ABKIT_SQL_LAB_TIMEOUT_SEC (default 60). Отдельная ручка от
    ABKIT_SQL_TIMEOUT_SEC (300с, материализация датасета): у интерактивного
    запроса другой профиль — за ним ждет человек, и 5 минут молчания в UI
    неприемлемы, тогда как для фоновой выгрузки это норма."""
    return int(os.environ.get("ABKIT_SQL_LAB_TIMEOUT_SEC", "60"))


@dataclass
class SqlLabResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    n_rows: int
    elapsed_ms: int
    truncated: bool
    """True — результат обрезан до INTERACTIVE_ROW_LIMIT, и пользователю об
    этом сказано явно. Без этого флага человек принял бы 1000 строк за весь
    результат и сделал бы неверный вывод по данным."""
    limit_pushed_down: bool = False
    """True — лимит удалось внедрить В САМ ЗАПРОС (БД вернет только нужное).
    False — запрос ушел как есть, а обрезка произошла на нашей стороне при
    чтении. Второе тоже безопасно по памяти (читаем первый чанк и
    останавливаемся), но БД успевает посчитать больше, поэтому разницу видно
    в логах, а не только в поведении."""
    warnings: list[str] = field(default_factory=list)


def apply_interactive_limit(sql: str, engine: str, limit: int = INTERACTIVE_ROW_LIMIT) -> tuple[str, bool]:
    """Внедряет LIMIT в запрос средствами sqlglot, с учетом диалекта.

    Почему через sqlglot, а не конкатенацией " LIMIT 1000": в MSSQL нет
    LIMIT (там TOP/OFFSET FETCH), у ClickHouse свои особенности, а запрос
    может уже заканчиваться на ORDER BY, комментарий или собственный LIMIT.
    sqlglot разбирает дерево и печатает обратно на нужном диалекте, поэтому
    получается синтаксически корректно для каждого движка.

    Существующий меньший LIMIT НЕ увеличивается: `SELECT ... LIMIT 10`
    остается десяткой. Пользователь попросил меньше — незачем давать больше.

    Возвращает (sql, удалось_ли_внедрить). Неудача — не ошибка: вызывающий
    все равно обрежет результат при чтении, см. execute_interactive.
    """
    dialect = sqlglot_dialect(engine)
    try:
        statement = sqlglot.parse_one(sql, read=dialect)
        if statement is None:
            return sql, False
        existing = statement.args.get("limit")
        if existing is not None:
            try:
                current = int(existing.expression.this)
                if current <= limit:
                    return sql, True  # уже строже нашего — ничего не меняем
            except (AttributeError, TypeError, ValueError):
                # Лимит-выражение, которое мы не смогли прочитать (параметр,
                # арифметика). Молча заменять его на свое — значит изменить
                # смысл запроса, поэтому не трогаем и режем при чтении.
                return sql, False
        return statement.limit(limit).sql(dialect=dialect), True
    except Exception as e:
        # sqlglot не всесилен на экзотическом диалектном синтаксисе. Это не
        # повод отказывать в выполнении — валидацию SELECT-only запрос уже
        # прошел, а ограничение будет применено при чтении.
        log.info("sql_lab.limit_pushdown_failed", error=str(e))
        return sql, False


_TIMEOUT_MARKERS = (
    "statement timeout",
    "canceling statement",
    "query was cancelled",
    "timeout expired",
    "max_execution_time",
    "timeout exceeded",
)


def _looks_like_timeout(exc: Exception) -> bool:
    """Оборвала ли БД запрос по нашему бюджету. По тексту — потому что тип
    исключения свой у каждого драйвера (psycopg.errors.QueryCanceled,
    clickhouse-connect и pymssql — свои), и общего предка у них нет."""
    text = str(exc).lower()
    return any(marker in text for marker in _TIMEOUT_MARKERS)


def _timeout_message(timeout_sec: int, elapsed_ms: int | None = None) -> str:
    took = f" (took {elapsed_ms / 1000:.1f}s)" if elapsed_ms is not None else ""
    return (
        f"Query exceeded the {timeout_sec}s interactive timeout{took} and was cancelled. "
        "Narrow the query, or create a dataset from it — dataset materialization runs in "
        "the background with a longer budget."
    )


def _apply_statement_timeout(conn: Any, engine: str, timeout_sec: int) -> None:
    """Просит САМУ БД оборвать запрос по истечении бюджета.

    Без этого таймаут был бы только замером постфактум: запрос все равно
    доработал бы до конца на стороне БД (заняв ее ресурсы), а мы бы лишь
    сообщили, что ждали дольше положенного — то есть «отмена» была бы на
    словах. Здесь запрос действительно снимается сервером БД.

    Единого переносимого способа нет — у каждого движка своя ручка, и там,
    где ее нет (MSSQL/pymssql: таймаут задается при подключении, не на
    запрос), остается проверка постфактум в execute_interactive. Поэтому
    любая неудача здесь — не ошибка выполнения: логируем и продолжаем, а не
    отказываем пользователю в запросе из-за ненастроенного таймаута.
    """
    try:
        if engine == "postgresql":
            conn.exec_driver_sql(f"SET statement_timeout = {int(timeout_sec) * 1000}")
        elif engine == "clickhouse":
            conn.exec_driver_sql(f"SET max_execution_time = {int(timeout_sec)}")
    except Exception as e:
        log.info("sql_lab.statement_timeout_not_applied", engine=engine, error=str(e))


def _jsonable(value: Any) -> Any:
    """Значение ячейки -> то, что переживет JSON.

    Даты/Decimal/UUID/bytes приезжают из разных драйверов в разных типах, и
    ни один из них не сериализуется сам. Приводим к строке — грид все равно
    отображает текст, а угадывать формат за пользователя (локаль дат,
    точность Decimal) хуже, чем показать каноническое строковое представление.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        # NaN/Inf не проходят строгий JSON — отдаем как None, иначе ответ
        # целиком становится невалидным JSON'ом из-за одной ячейки.
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            return None
        return value
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    return str(value)


def execute_interactive(
    spec: ConnectionSpec,
    sql: str,
    *,
    limit: int = INTERACTIVE_ROW_LIMIT,
    timeout_sec: int | None = None,
) -> SqlLabResult:
    """Прогон запроса для SQL Lab. Всегда возвращает не больше `limit` строк.

    Гард SELECT-only применяется к ИСХОДНОМУ тексту (до внедрения лимита):
    именно его написал пользователь, и именно он должен быть проверен —
    проверять сгенерированный sqlglot'ом текст значило бы проверять не то,
    что человек ввел.
    """
    validate_select_only(sql, spec.engine)
    timeout_sec = timeout_sec if timeout_sec is not None else interactive_timeout_sec()

    # ВНИМАНИЕ: в запрос внедряется limit + 1, а не limit.
    # Иначе, когда лимит успешно ушел в БД, мы получили бы ровно `limit`
    # строк и не смогли бы отличить "результат ровно такой" от "результат
    # больше, БД его обрезала" — и показали бы тысячу строк как весь ответ,
    # без предупреждения. Лишняя строка существует только чтобы ответить на
    # этот вопрос; до пользователя она не доезжает (обрезается ниже).
    limited_sql, pushed_down = apply_interactive_limit(sql, spec.engine, limit + 1)
    warnings: list[str] = []

    engine = build_engine(spec, timeout_sec=_CONNECT_TIMEOUT_SEC)
    start = time.monotonic()
    try:
        try:
            raw_conn = engine.connect()
        except Exception as e:
            raise SqlExecutionError(f"Could not connect: {e}") from e
        with raw_conn as conn:
            # ПОРЯДОК ВАЖЕН: сначала таймаут, потом stream_results. С
            # включенным stream_results SQLAlchemy оборачивает КАЖДЫЙ оператор
            # в серверный курсор (DECLARE ... CURSOR FOR ...), а `SET` внутри
            # курсора — синтаксическая ошибка, и таймаут молча не применился бы.
            _apply_statement_timeout(conn, spec.engine, timeout_sec)
            conn = conn.execution_options(stream_results=True)
            try:
                # chunksize=limit+1: читаем ОДИН чанк. Даже если лимит не
                # удалось внедрить в запрос, в память попадет не больше
                # limit+1 строк — лишняя строка нужна ровно чтобы отличить
                # "ровно limit строк" от "было больше, обрезали".
                chunks = pd.read_sql(sa_text(limited_sql), conn, chunksize=limit + 1)
                df = next(chunks, None)
                if df is None:
                    # Ноль строк: типизированную схему берем отдельным
                    # пробником — иначе колонки остались бы неизвестны и грид
                    # был бы пуст даже без заголовков.
                    df = pd.read_sql(
                        sa_text(f"SELECT * FROM ({sql}) AS __abkit_probe WHERE 1 = 0"), conn
                    )
            except Exception as e:
                if _looks_like_timeout(e):
                    # БД оборвала запрос по нашему же statement_timeout.
                    # Показывать сырое "canceling statement due to statement
                    # timeout" значило бы предлагать пользователю догадаться,
                    # чей это таймаут и что с ним делать.
                    raise SqlExecutionError(_timeout_message(timeout_sec)) from e
                raise SqlExecutionError(f"Query failed: {e}") from e
            finally:
                # Курсор закрываем сразу: соединение к внешней БД не должно
                # оставаться занятым, пока мы сериализуем ответ.
                try:
                    chunks.close()  # type: ignore[union-attr]
                except Exception:
                    pass
    finally:
        engine.dispose()

    elapsed_ms = int((time.monotonic() - start) * 1000)
    if elapsed_ms > timeout_sec * 1000:
        # Backstop для движков, где запрос не снимается сервером (MSSQL: у
        # pymssql таймаут задается на подключении, не на запрос). Там, где
        # _apply_statement_timeout сработал, сюда уже не доходит — БД оборвала
        # запрос сама. Молча отдавать результат, дождавшийся втрое дольше
        # бюджета, нельзя: бюджет тогда ничего не значил бы.
        raise SqlExecutionError(_timeout_message(timeout_sec, elapsed_ms))

    truncated = len(df) > limit
    if truncated:
        df = df.iloc[:limit]
        warnings.append(
            f"Preview limited to {limit} rows — the full result is larger. "
            "Create a dataset from this query to materialize all rows."
        )

    columns = [str(c) for c in df.columns]
    rows = [{col: _jsonable(value) for col, value in record.items()} for record in df.to_dict("records")]
    return SqlLabResult(
        columns=columns,
        rows=rows,
        n_rows=len(rows),
        elapsed_ms=elapsed_ms,
        truncated=truncated,
        limit_pushed_down=pushed_down,
        warnings=warnings,
    )
