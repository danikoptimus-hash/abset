"""Плейсхолдеры дат в SQL-запросах датасетов: {{date_from}} / {{date_to}}.

Зачем это существует. Дизайн эксперимента считают на ОДНОМ окне данных
(например, месяц до старта), а результаты собирают на ДРУГОМ (сам период
теста). Запрос при этом один и тот же — меняются только две даты. Без
плейсхолдеров пользователь копирует запрос, руками правит две даты, забывает
поправить вторую, и получает выборку не за тот период; ошибка при этом
тихая — данные есть, они просто не те.

Синтаксис — jinja-подобный `{{date_from}}`, знакомый по Superset (там это
Jinja-шаблоны в датасетах). Но НАСТОЯЩЕГО шаблонизатора здесь нет и не будет:

- Jinja в SQL — это исполнение произвольного выражения на сервере рядом с
  доступом к БД. Оправдано это было бы богатством возможностей, которое здесь
  никому не нужно: задача — подставить две даты;
- поэтому разрешены РОВНО два имени, и подставляются они не как текст от
  пользователя, а как результат `date.isoformat()` уже разобранной даты
  (см. render_sql). Что бы ни ввели в поле, в SQL уедет либо корректный
  литерал 'YYYY-MM-DD', либо ничего — запрос будет отклонен раньше.

Отсюда же и порядок работы с гардом SELECT-only: он применяется к УЖЕ
ПОДСТАВЛЕННОМУ тексту (sqlglot не разберет `{{date_from}}`), но подставить
туда можно только дату, так что расширить права через плейсхолдер нельзя.
"""

from __future__ import annotations

import re
from datetime import date, datetime

# Только два имени. Список закрытый и намеренно короткий: каждое новое имя —
# это новый контракт с пользовательскими запросами, который придется
# поддерживать вечно.
DATE_FROM = "date_from"
DATE_TO = "date_to"
ALLOWED_PLACEHOLDERS = (DATE_FROM, DATE_TO)

# `{{ name }}` с любыми пробелами внутри. Имя ловим широко (\w+ и точки/дефисы),
# чтобы про НЕИЗВЕСТНОЕ имя можно было сказать пользователю, какое именно
# оказалось лишним, а не отвечать общим "синтаксис не тот".
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([\w.\-]+)\s*\}\}")

# Любая оставшаяся пара фигурных скобок — например `{{ 1+1 }}` или
# `{% if %}`: их _PLACEHOLDER_RE не поймает (там не имя), но пропускать их
# молча нельзя — это была бы попытка шаблонизации, которой тут нет.
_ANY_TEMPLATE_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)


class SqlParamError(Exception):
    """Плейсхолдер неизвестен, значение параметра не дата, или параметры не
    переданы для запроса, который их требует. Роутер маппит в 422 —
    это ошибка ввода, а не сбой."""


def find_placeholders(sql: str) -> list[str]:
    """Имена плейсхолдеров в порядке первого появления, без повторов."""
    seen: list[str] = []
    for match in _PLACEHOLDER_RE.finditer(sql or ""):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def has_placeholders(sql: str | None) -> bool:
    return bool(sql) and bool(_ANY_TEMPLATE_RE.search(sql or ""))


def validate_placeholders(sql: str) -> list[str]:
    """Проверяет, что в запросе только разрешенные плейсхолдеры.

    Возвращает найденные имена (подмножество ALLOWED_PLACEHOLDERS). Бросает
    SqlParamError с УКАЗАНИЕМ конкретного лишнего имени — «unknown placeholder»
    без имени заставляет искать его глазами в запросе на сто строк.
    """
    names = find_placeholders(sql)
    unknown = [n for n in names if n not in ALLOWED_PLACEHOLDERS]
    if unknown:
        raise SqlParamError(
            f"Unknown placeholder{'s' if len(unknown) > 1 else ''}: "
            + ", ".join(f"{{{{{n}}}}}" for n in unknown)
            + f". Only {{{{{DATE_FROM}}}}} and {{{{{DATE_TO}}}}} are supported."
        )

    # Шаблонная конструкция, которая не является ни одним из разрешенных
    # плейсхолдеров: `{% for %}`, `{{ 1+1 }}`, `{{}}`. Отдельная проверка,
    # потому что _PLACEHOLDER_RE такое просто не матчит, и без нее оно уехало
    # бы в SQL как есть и упало бы уже в БД с невнятной синтаксической ошибкой.
    def _is_allowed_placeholder(text: str) -> bool:
        return text.startswith("{{") and text[2:-2].strip() in ALLOWED_PLACEHOLDERS

    leftovers = [
        m.group(0) for m in _ANY_TEMPLATE_RE.finditer(sql) if not _is_allowed_placeholder(m.group(0))
    ]
    if leftovers:
        raise SqlParamError(
            f"Unsupported template expression: {leftovers[0]}. "
            f"Only the plain placeholders {{{{{DATE_FROM}}}}} and {{{{{DATE_TO}}}}} are supported "
            "— there is no template engine here."
        )
    return names


def parse_date(value: object, field: str) -> date:
    """Строгий разбор даты. Принимает `date`/`datetime` и строку 'YYYY-MM-DD'.

    Только ISO и ничего больше: '01/02/2026' неоднозначна (январь или
    февраль — зависит от того, кто читает), а молча выбранная интерпретация
    дала бы выборку не за тот период. Лучше отказ с понятным текстом.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return date.fromisoformat(text)
        except ValueError as e:
            raise SqlParamError(
                f"'{field}' must be a date in YYYY-MM-DD format, got '{text}'."
            ) from e
    raise SqlParamError(f"'{field}' must be a date in YYYY-MM-DD format, got {type(value).__name__}.")


def render_sql(sql: str, *, date_from: object | None, date_to: object | None) -> str:
    """Подставляет даты и возвращает готовый к выполнению SQL.

    Подставляется ТОЛЬКО результат date.isoformat(), обернутый в одинарные
    кавычки: 'YYYY-MM-DD'. Пользовательская строка до SQL не доезжает никогда —
    она сначала разбирается в объект `date`, и любой мусор отваливается на
    этом шаге. Поэтому кавычки экранировать не от чего: в ISO-дате нет ни
    кавычек, ни чего-либо еще, что могло бы закрыть литерал.
    """
    names = validate_placeholders(sql)
    if not names:
        return sql

    values: dict[str, str] = {}
    if DATE_FROM in names:
        if date_from is None:
            raise SqlParamError(
                f"This query uses {{{{{DATE_FROM}}}}} but no value was provided for it."
            )
        values[DATE_FROM] = f"'{parse_date(date_from, DATE_FROM).isoformat()}'"
    if DATE_TO in names:
        if date_to is None:
            raise SqlParamError(
                f"This query uses {{{{{DATE_TO}}}}} but no value was provided for it."
            )
        values[DATE_TO] = f"'{parse_date(date_to, DATE_TO).isoformat()}'"

    def _replace(match: re.Match[str]) -> str:
        return values[match.group(1)]

    return _PLACEHOLDER_RE.sub(_replace, sql)


def describe_params(date_from: date | None, date_to: date | None) -> str | None:
    """Короткая подпись параметров для списка датасетов: '2026-01-01..2026-01-31'.
    None — параметров нет (обычный запрос без плейсхолдеров)."""
    if date_from is None and date_to is None:
        return None
    left = date_from.isoformat() if date_from else "…"
    right = date_to.isoformat() if date_to else "…"
    return f"{left}..{right}"
