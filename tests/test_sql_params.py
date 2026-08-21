"""Плейсхолдеры дат в SQL-запросах датасетов (без БД и без внешних движков).

Проверяется ровно то, что делает подстановку безопасной: закрытый список имен,
строгий разбор дат и то, что в SQL уезжает только результат этого разбора.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from abkit.db_connections.sql_params import (
    SqlParamError,
    describe_params,
    find_placeholders,
    has_placeholders,
    parse_date,
    render_sql,
    validate_placeholders,
)

QUERY = "SELECT * FROM events WHERE ts >= {{date_from}} AND ts < {{date_to}}"


# ---------------------------------------------------------------------------
# Обнаружение
# ---------------------------------------------------------------------------


def test_finds_both_placeholders_in_order():
    assert find_placeholders(QUERY) == ["date_from", "date_to"]


def test_repeated_placeholder_reported_once():
    sql = "SELECT {{date_from}}, x FROM t WHERE a >= {{date_from}}"
    assert find_placeholders(sql) == ["date_from"]


def test_whitespace_inside_braces_is_tolerated():
    assert find_placeholders("WHERE a >= {{  date_from  }}") == ["date_from"]
    assert render_sql("WHERE a >= {{  date_from  }}", date_from="2026-01-01", date_to=None) == (
        "WHERE a >= '2026-01-01'"
    )


def test_query_without_placeholders():
    assert find_placeholders("SELECT 1") == []
    assert has_placeholders("SELECT 1") is False
    assert has_placeholders(None) is False


# ---------------------------------------------------------------------------
# Валидация имен (ТЗ п.2: неизвестное имя -> внятная ошибка С ИМЕНЕМ)
# ---------------------------------------------------------------------------


def test_allowed_placeholders_validate():
    assert validate_placeholders(QUERY) == ["date_from", "date_to"]


def test_unknown_placeholder_is_rejected_and_named():
    """Без указания имени пользователь ищет его глазами в запросе на сто строк."""
    with pytest.raises(SqlParamError) as e:
        validate_placeholders("SELECT * FROM t WHERE user_id = {{user_id}}")
    assert "{{user_id}}" in str(e.value)
    assert "date_from" in str(e.value) and "date_to" in str(e.value)


def test_several_unknown_placeholders_all_named():
    with pytest.raises(SqlParamError) as e:
        validate_placeholders("SELECT {{foo}}, {{bar}} FROM t")
    assert "{{foo}}" in str(e.value)
    assert "{{bar}}" in str(e.value)


def test_typo_in_a_known_name_is_still_unknown():
    """{{date_form}} — самая вероятная опечатка; молча проигнорировать её
    значило бы уехать в БД с literal '{{date_form}}' и упасть там."""
    with pytest.raises(SqlParamError) as e:
        validate_placeholders("WHERE a >= {{date_form}}")
    assert "{{date_form}}" in str(e.value)


def test_template_expressions_are_rejected():
    """Шаблонизатора здесь нет — и это не должно выясняться в момент, когда
    невыполнимый текст уже уехал в БД."""
    for sql in (
        "SELECT * FROM t WHERE a >= {{ 1 + 1 }}",
        "SELECT * FROM t {% if x %} WHERE y {% endif %}",
        "SELECT {{}} FROM t",
    ):
        with pytest.raises(SqlParamError):
            validate_placeholders(sql)


def test_jinja_filter_syntax_is_rejected():
    with pytest.raises(SqlParamError):
        validate_placeholders("WHERE a >= {{ date_from | upper }}")


# ---------------------------------------------------------------------------
# Разбор дат
# ---------------------------------------------------------------------------


def test_parse_iso_string():
    assert parse_date("2026-01-31", "date_from") == date(2026, 1, 31)


def test_parse_accepts_date_and_datetime_objects():
    assert parse_date(date(2026, 2, 1), "d") == date(2026, 2, 1)
    assert parse_date(datetime(2026, 2, 1, 13, 45), "d") == date(2026, 2, 1)


@pytest.mark.parametrize(
    "bad",
    [
        "01/02/2026",     # неоднозначно: январь или февраль — зависит от читателя
        "2026-13-01",     # несуществующий месяц
        "2026-02-30",     # несуществующий день
        "yesterday",
        "",
        "2026",
        "'2026-01-01'",   # уже в кавычках — попытка протащить литерал
    ],
)
def test_non_date_values_are_rejected(bad):
    with pytest.raises(SqlParamError) as e:
        parse_date(bad, "date_from")
    assert "date_from" in str(e.value)
    assert "YYYY-MM-DD" in str(e.value)


def test_non_string_non_date_rejected():
    with pytest.raises(SqlParamError):
        parse_date(12345, "date_from")


# ---------------------------------------------------------------------------
# Подстановка (ТЗ п.2: строгое форматирование, никакой интерполяции ввода)
# ---------------------------------------------------------------------------


def test_substitution_produces_quoted_iso_literals():
    rendered = render_sql(QUERY, date_from="2026-01-01", date_to="2026-01-31")
    assert rendered == (
        "SELECT * FROM events WHERE ts >= '2026-01-01' AND ts < '2026-01-31'"
    )
    assert "{{" not in rendered


def test_substitution_replaces_every_occurrence():
    sql = "SELECT {{date_from}} AS a, {{date_from}} AS b"
    assert render_sql(sql, date_from="2026-03-05", date_to=None) == (
        "SELECT '2026-03-05' AS a, '2026-03-05' AS b"
    )


def test_query_without_placeholders_is_returned_unchanged():
    sql = "SELECT * FROM t"
    assert render_sql(sql, date_from="2026-01-01", date_to="2026-01-31") == sql


def test_missing_value_for_a_used_placeholder_is_an_error():
    """Молча подставить пустоту значило бы выполнить запрос за неизвестный
    период — тихо и не тем данным."""
    with pytest.raises(SqlParamError) as e:
        render_sql(QUERY, date_from="2026-01-01", date_to=None)
    assert "date_to" in str(e.value)


def test_injection_attempt_through_a_date_value_is_rejected():
    """Ключевое свойство: в SQL уезжает не строка пользователя, а результат
    date.isoformat(). Всё, что не разбирается в дату, отваливается ДО того,
    как что-либо будет подставлено."""
    for attack in (
        "2026-01-01' OR '1'='1",
        "2026-01-01'; DROP TABLE users; --",
        "2026-01-01 UNION SELECT password FROM users",
    ):
        with pytest.raises(SqlParamError):
            render_sql(QUERY, date_from=attack, date_to="2026-01-31")


def test_rendered_sql_is_parseable_by_the_select_guard():
    """Подставленный текст должен проходить существующий SELECT-only гард —
    иначе валидная пара «запрос + даты» падала бы на ровном месте."""
    from abkit.db_connections.sql_guard import validate_select_only

    rendered = render_sql(QUERY, date_from="2026-01-01", date_to="2026-01-31")
    validate_select_only(rendered, "postgresql")  # не бросает


def test_placeholders_cannot_smuggle_a_non_select():
    """Даже если бы кто-то попытался собрать DML через плейсхолдер — значение
    всё равно проходит parse_date и не может содержать SQL."""
    sql = "SELECT * FROM t WHERE a >= {{date_from}}"
    with pytest.raises(SqlParamError):
        render_sql(sql, date_from="2026-01-01; DELETE FROM users", date_to=None)


# ---------------------------------------------------------------------------
# Подпись параметров для UI
# ---------------------------------------------------------------------------


def test_describe_params():
    assert describe_params(date(2026, 1, 1), date(2026, 1, 31)) == "2026-01-01..2026-01-31"
    assert describe_params(None, None) is None
    assert describe_params(date(2026, 1, 1), None) == "2026-01-01..…"
