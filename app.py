"""abkit — веб-интерфейс на Streamlit: Design / Analyze / Experiments / Validation."""

from __future__ import annotations

import io
import os
import time
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from pydantic import ValidationError

from abkit import checks, storage
from abkit.auth.guards import AuthError, CurrentUser
from abkit.config import DesignConfig, MetricConfig
from abkit.demo_data import (
    generate_demo_design_data,
    generate_demo_post_data_for_config,
    make_demo_design_config,
)
from abkit.experiment import DesignError, Experiment, compute_metric_baseline_mean
from abkit.pipeline import PipelineError
from abkit.validation.simulation import ABReport, AAReport, run_aa, run_ab
from abkit.viz.help_texts import (
    HELP_EXPANDER_LABEL,
    HELP_EXPANDER_LABEL_TABLE,
    get_help_text,
    get_warning,
)
from abkit.viz.plots import (
    cumulative_lift_plot,
    distribution_plot,
    forest_plot,
    p99_clip_stats,
    segment_forest_plot,
)


def _help_expander(chart_type: str, *, table: bool = False) -> None:
    label = HELP_EXPANDER_LABEL_TABLE if table else HELP_EXPANDER_LABEL
    with st.expander(label, expanded=False):
        st.markdown(get_help_text(chart_type))


# --------------------------------------------------------------------------
# Auth (DOCKER.md §4) — активируется только при ABKIT_MODE=db. Файловый режим
# (дефолт) не показывает логин-экран и current_user всегда None.
# --------------------------------------------------------------------------

_SESSION_COOKIE_NAME = "abkit_session"


def _db_mode() -> bool:
    return os.environ.get("ABKIT_MODE", "file") == "db"


def _list_experiment_names(experiments_dir: Path, *, active_only: bool = False) -> dict[str, dict]:
    """Список экспериментов для селектбоксов/таблиц — Postgres (ExperimentRepo)
    в серверном режиме, файловый registry.json иначе. Форма возвращаемого
    словаря одинакова в обоих режимах, поэтому вызывающий код не меняется."""
    if _db_mode():
        from abkit.db.repositories import ExperimentRepo
        from abkit.db.store import get_data_dir

        rows = ExperimentRepo().list_all(active_only=active_only)
        data_dir = get_data_dir()
        return {
            r.name: {
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "path": str(data_dir / r.name),
                "owner_id": str(r.owner_id),
            }
            for r in rows
        }
    return storage.list_experiments(experiments_dir, active_only=active_only)


def _run_inline_script(script: str) -> None:
    """Выполняет небольшой inline JS через data: URI в st.iframe.

    st.components.v1.html снят с поддержки в Streamlit 1.58 (дедлайн удаления
    2026-06-01, уже прошел) — общий заменитель st.iframe принимает только
    путь/URL, не сырой HTML, поэтому заворачиваем скрипт в data:text/html URI.
    width/height=1 (0 невалиден для st.iframe) — фрейм визуально незаметен.
    """
    import urllib.parse

    html = f"<script>{script}</script>"
    uri = "data:text/html," + urllib.parse.quote(html)
    st.iframe(uri, height=1, width=1)


def _set_session_cookie(token: str) -> None:
    """Best-effort JS-мост: пишет токен сессии в cookie браузера, чтобы сессия
    переживала обновление страницы. Не HttpOnly — ограничение чистого Streamlit
    без reverse-proxy/middleware перед ним (TODO(D4): перенести установку cookie
    на nginx/middleware и сделать HttpOnly, как того требует DOCKER.md §11).
    Канонический источник правды для logout/истечения сессии — не сам факт
    наличия cookie, а серверная проверка JWT в _render_login_gate."""
    hours = float(os.environ.get("ABKIT_SESSION_LIFETIME_HOURS", "72"))
    max_age = int(hours * 3600)
    _run_inline_script(
        f'document.cookie = "{_SESSION_COOKIE_NAME}={token}; max-age={max_age}; '
        f'path=/; SameSite=Lax";'
    )


def _clear_session_cookie() -> None:
    _run_inline_script(
        f'document.cookie = "{_SESSION_COOKIE_NAME}=; max-age=0; path=/; SameSite=Lax";'
    )


def _do_logout() -> None:
    st.session_state.pop("auth_token", None)
    _clear_session_cookie()
    st.rerun()


def _render_self_registration_form() -> None:
    from abkit.auth.service import self_register

    with st.form("self_register_form"):
        email = st.text_input("Email", key="reg_email")
        name = st.text_input("Имя", key="reg_name")
        password = st.text_input("Пароль", type="password", key="reg_password")
        submitted = st.form_submit_button("Создать аккаунт")
    if submitted:
        try:
            self_register(email=email, name=name, password=password)
        except AuthError as e:
            st.error(str(e))
        else:
            st.success("Аккаунт создан (роль Viewer). Теперь войдите ниже.")


def _render_login_form() -> None:
    st.title("abkit — вход")
    from abkit.auth.service import login as auth_login

    placeholder = st.empty()
    with placeholder.container():
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Пароль", type="password")
            submitted = st.form_submit_button("Войти", type="primary")
    if submitted:
        try:
            token = auth_login(email, password)
        except AuthError as e:
            st.error(str(e))
        else:
            placeholder.empty()
            st.session_state["auth_token"] = token
            _set_session_cookie(token)
            st.rerun()

    if os.environ.get("ABKIT_ALLOW_SELF_REGISTRATION", "false").lower() == "true":
        with st.expander("Зарегистрироваться"):
            _render_self_registration_form()


def _render_force_password_change(current_user: CurrentUser) -> None:
    st.title("Смена пароля")
    st.info("Администратор сбросил ваш пароль — задайте новый перед продолжением.")
    from abkit.auth.service import change_own_password

    with st.form("force_change_password_form"):
        old_password = st.text_input("Текущий (временный) пароль", type="password")
        new_password = st.text_input("Новый пароль", type="password")
        new_password2 = st.text_input("Повторите новый пароль", type="password")
        submitted = st.form_submit_button("Сменить пароль", type="primary")
    if not submitted:
        return
    if new_password != new_password2:
        st.error("Пароли не совпадают")
    elif len(new_password) < 8:
        st.error("Пароль должен быть не короче 8 символов")
    else:
        try:
            change_own_password(current_user, old_password, new_password)
        except AuthError as e:
            st.error(str(e))
        else:
            st.success("Пароль изменен. Войдите заново.")
            _do_logout()


def _render_login_gate() -> CurrentUser | None:
    """Единственное, что видит незалогиненный пользователь (DOCKER.md §4.2).
    Возвращает CurrentUser при валидной сессии; иначе рендерит форму логина
    (или принудительной смены пароля) и возвращает None — main() должен
    прекратить рендер остального приложения в этом случае."""
    from abkit.auth.service import current_user_from_token

    token = st.session_state.get("auth_token")
    if not token:
        token = st.context.cookies.get(_SESSION_COOKIE_NAME)
        if token:
            st.session_state["auth_token"] = token

    current_user = current_user_from_token(token)
    if current_user is None:
        if token:
            st.session_state.pop("auth_token", None)
        _render_login_form()
        return None

    if current_user.must_change_password:
        _render_force_password_change(current_user)
        return None

    return current_user

STATUS_TRANSITIONS = {
    "designed": ("running", "archived"),
    "running": ("completed", "archived"),
    "completed": ("archived",),
    "archived": (),
}

_AGG_LABEL_TO_CODE = {
    "Сумма": "sum",
    "Максимум": "max",
    "Последнее значение": "last",
    "Первое значение": "first",
}
_AGG_DEFAULT_LABEL_BY_TYPE = {"continuous": "Сумма", "binary": "Максимум", "ratio": "Сумма"}

_LARGE_FILE_BYTES = 10 * 1024 * 1024
# в тестах (AppTest) укорачиваем через ABKIT_FLASH_SECONDS=0, чтобы не тормозить прогон
_FLASH_SECONDS = float(os.environ.get("ABKIT_FLASH_SECONDS", "2.0"))


def _flash_success(message: str) -> None:
    """Короткое success-сообщение, которое само исчезает перед показом основного
    результата — чтобы не путать пользователя с постоянным баннером."""
    placeholder = st.empty()
    placeholder.success(message)
    if _FLASH_SECONDS > 0:
        time.sleep(_FLASH_SECONDS)
    placeholder.empty()

_DESIGN_EXAMPLE_DF = pd.DataFrame(
    [
        {"user_id": "u_00001", "platform": "ios", "country": "RU", "segment": "premium", "converted_pre_30d": 1, "revenue_pre_30d": 1240, "sessions_pre_30d": 12},
        {"user_id": "u_00002", "platform": "android", "country": "UZ", "segment": "free", "converted_pre_30d": 0, "revenue_pre_30d": 0, "sessions_pre_30d": 3},
        {"user_id": "u_00003", "platform": "ios", "country": "KZ", "segment": "premium", "converted_pre_30d": 1, "revenue_pre_30d": 890, "sessions_pre_30d": 8},
        {"user_id": "u_00004", "platform": "android", "country": "RU", "segment": "free", "converted_pre_30d": 0, "revenue_pre_30d": 0, "sessions_pre_30d": 1},
        {"user_id": "u_00005", "platform": "web", "country": "UZ", "segment": "premium", "converted_pre_30d": 1, "revenue_pre_30d": 2100, "sessions_pre_30d": 15},
        {"user_id": "u_00006", "platform": "ios", "country": "RU", "segment": "free", "converted_pre_30d": 0, "revenue_pre_30d": 340, "sessions_pre_30d": 5},
    ]
)

_DESIGN_SQL_EXAMPLE = """SELECT
    user_id,
    any(platform) as platform,
    any(country) as country,
    any(segment) as segment,
    -- бинарные pre-period метрики
    max(if(event = 'purchase', 1, 0)) as converted_pre_30d,
    -- continuous pre-period метрики
    sum(if(event = 'purchase', revenue, 0)) as revenue_pre_30d,
    count(distinct session_id) as sessions_pre_30d
FROM events
WHERE date >= today() - 30 AND date < today()
GROUP BY user_id"""

_ANALYZE_EXAMPLE_DF = pd.DataFrame(
    [
        {"user_id": "u_00001", "converted": 1, "revenue": 3200, "sessions": 8},
        {"user_id": "u_00002", "converted": 0, "revenue": 0, "sessions": 2},
        {"user_id": "u_00003", "converted": 1, "revenue": 1450, "sessions": 5},
        {"user_id": "u_00004", "converted": 0, "revenue": 0, "sessions": 1},
        {"user_id": "u_00005", "converted": 1, "revenue": 4100, "sessions": 11},
        {"user_id": "u_00006", "converted": 0, "revenue": 120, "sessions": 3},
    ]
)

_ANALYZE_SQL_EXAMPLE = """SELECT
    user_id,
    max(if(event = 'purchase', 1, 0)) as converted,
    sum(if(event = 'purchase', revenue, 0)) as revenue,
    count(distinct session_id) as sessions
FROM events
WHERE date >= '2026-07-06' AND date < '2026-07-20'  -- период теста
  AND user_id IN (SELECT unit_id FROM assignments_of_your_test)
GROUP BY user_id"""


def _render_design_intro() -> None:
    st.subheader("Загрузите данные о ваших пользователях-кандидатах")

    with st.expander("❓ Что это за данные и что в них должно быть"):
        st.markdown(
            "Это snapshot вашей базы пользователей **ПЕРЕД** тестом — те, кого вы "
            "потенциально включите в эксперимент.\n\n"
            "**Формат:** одна строка = один пользователь.\n\n"
            "**Что должно быть в файле:**\n"
            "- Колонка с ID пользователя (обязательно, уникальная)\n"
            "- Признаки для стратификации: платформа, страна, сегмент, тариф и т.д. "
            "(желательно — иначе группы не будут сбалансированы)\n"
            "- Pre-period метрики: те же метрики, что будете мерить в тесте, но за "
            "период ДО теста (желательно — без них не работает CUPED и точный расчет MDE)"
        )

    with st.expander("📊 Пример: как должны выглядеть данные"):
        st.markdown(
            "Ниже — пример для интернет-магазина. Обратите внимание на разные типы "
            "pre-period метрик: `revenue_pre_30d` (continuous, для метрики «выручка»), "
            "`converted_pre_30d` (binary, для метрики «конверсия»), `sessions_pre_30d` "
            "(для ratio-метрик типа «выручка на сессию»)."
        )
        st.dataframe(_DESIGN_EXAMPLE_DF, hide_index=True)
        st.markdown(
            "- **user_id** — уникальный идентификатор (обязательно)\n"
            "- **platform, country, segment** — признаки для стратификации (можно "
            "любые категориальные, чем больше — тем лучше баланс)\n"
            "- **converted_pre_30d** — бинарная pre-period метрика (0/1) для будущего "
            "анализа конверсии\n"
            "- **revenue_pre_30d** — continuous pre-period метрика для выручки\n"
            "- **sessions_pre_30d** — количество сессий, нужно для ratio-метрик вроде "
            "revenue/sessions"
        )

    with st.expander("💡 Как выгрузить данные из БД (SQL-пример)"):
        st.code(_DESIGN_SQL_EXAMPLE, language="sql")
        st.markdown(
            "Замените `event = 'purchase'` на ваше событие конверсии. Период (30 дней) "
            "выбирайте так, чтобы он был осмысленным для вашего продукта — типичное "
            "окно принятия решения."
        )

    with st.expander("❓ Нет данных под рукой — хочу просто попробовать"):
        st.markdown(
            "Нажмите кнопку **«Загрузить демо-данные»** справа. Программа сгенерирует "
            "синтетический датасет на 5000 пользователей с реалистичной структурой "
            "(разные платформы, страны, сегменты, pre-period метрики) и проведет вас "
            "через весь воркфлоу — от дизайна до отчета анализа. Это лучший способ "
            "разобраться, как работает инструмент."
        )


def _render_analyze_intro() -> None:
    with st.expander("❓ Что это за данные и что в них должно быть"):
        st.markdown(
            "Загрузите данные пост-периода — тех же пользователей из вашего эксперимента "
            "и их фактические значения метрик **ЗА** время теста.\n\n"
            "**Формат:** одна строка = один пользователь.\n\n"
            "**Что должно быть в файле:**\n"
            "- Та же колонка с ID пользователя, что использовалась при дизайне\n"
            "- Фактические значения всех метрик, которые вы объявляли на этапе дизайна "
            "(программа проверит и предупредит, если каких-то нет)\n"
            "- Разбиение на группы (control/treatment) НЕ нужно — оно подтянется "
            "автоматически из сохраненных assignments выбранного эксперимента"
        )

    with st.expander("📊 Пример: как должны выглядеть данные"):
        st.markdown(
            "Пример для того же интернет-магазина. Данные ЗА период теста (например, "
            "14 дней после запуска). Показаны все три типа метрик, объявленных при дизайне:"
        )
        st.dataframe(_ANALYZE_EXAMPLE_DF, hide_index=True)
        st.markdown(
            "- **user_id** — тот же ID, что был в исторических данных\n"
            "- **converted** — бинарная метрика (сконвертировался ли за время теста)\n"
            "- **revenue** — выручка за время теста (continuous)\n"
            "- **sessions** — количество сессий за время теста (для ratio revenue/sessions)"
        )

    with st.expander("💡 Как выгрузить данные из БД"):
        st.code(_ANALYZE_SQL_EXAMPLE, language="sql")


def _read_uploaded_df(uploaded) -> pd.DataFrame:
    uploaded.seek(0)
    if uploaded.name.lower().endswith(".parquet"):
        return pd.read_parquet(uploaded)
    return pd.read_csv(uploaded)


def _load_uploaded(uploaded) -> pd.DataFrame:
    if (uploaded.size or 0) > _LARGE_FILE_BYTES:
        with st.spinner("Читаем файл..."):
            return _read_uploaded_df(uploaded)
    return _read_uploaded_df(uploaded)


def _next_row_id(prefix: str) -> str:
    counter_key = f"_{prefix}_counter"
    st.session_state[counter_key] = st.session_state.get(counter_key, 0) + 1
    return f"{prefix}{st.session_state[counter_key]}"


def _next_demo_name(experiments_dir: Path) -> str:
    registry = storage.read_registry(experiments_dir)
    name = "demo"
    suffix = 1
    while name in registry:
        suffix += 1
        name = f"demo_{suffix}"
    return name


def _sanitize_column_selections(data: pd.DataFrame) -> list[str]:
    """Сбрасывает ранее выбранные колонки (unit_col, страты, num/den/pre_col),
    которых нет в новых данных (например, после загрузки другого файла), и
    возвращает предупреждения для пользователя — иначе соответствующие
    selectbox/multiselect упадут с ошибкой (текущее значение не входит в options).
    """
    warnings: list[str] = []
    columns = set(data.columns)
    numeric_columns = set(data.select_dtypes(include="number").columns)

    unit_col = st.session_state.get("design_unit_col")
    if unit_col is not None and unit_col not in columns:
        del st.session_state["design_unit_col"]
        warnings.append(f"Ранее выбранная колонка unit_col «{unit_col}» не найдена в новых данных.")

    strata = st.session_state.get("design_strata")
    if strata:
        missing = [c for c in strata if c not in columns]
        if missing:
            st.session_state["design_strata"] = [c for c in strata if c in columns]
            for c in missing:
                warnings.append(f"Ранее выбранная страта «{c}» не найдена в новых данных.")

    for mid in st.session_state.get("design_metric_ids", []):
        for field_key, label in (
            (f"metric_num_{mid}", "числитель"),
            (f"metric_den_{mid}", "знаменатель"),
            (f"metric_precol_{mid}", "pre-period"),
        ):
            value = st.session_state.get(field_key)
            if value and value != "(нет)" and value not in numeric_columns:
                st.session_state[field_key] = "(нет)"
                warnings.append(
                    f"Ранее выбранная колонка «{value}» ({label}) не найдена в новых "
                    "числовых колонках, сброшено на «(нет)»."
                )

        metric_type = st.session_state.get(f"metric_type_{mid}")
        name_key = f"metric_name_{mid}"
        name_value = st.session_state.get(name_key)
        if metric_type != "ratio" and name_value and name_value not in columns:
            del st.session_state[name_key]
            warnings.append(
                f"Ранее выбранное имя метрики «{name_value}» (колонка) не найдено "
                "в новых данных."
            )

    return warnings


# --------------------------------------------------------------------------
# Design
# --------------------------------------------------------------------------


def _init_design_state() -> None:
    if "design_group_ids" not in st.session_state:
        st.session_state.design_group_ids = [_next_row_id("group"), _next_row_id("group")]
        gids = st.session_state.design_group_ids
        st.session_state[f"group_name_{gids[0]}"] = "Control"
        st.session_state[f"group_prop_{gids[0]}"] = 0.5
        st.session_state[f"group_name_{gids[1]}"] = "Test"
        st.session_state[f"group_prop_{gids[1]}"] = 0.5
    if "design_metric_ids" not in st.session_state:
        st.session_state.design_metric_ids = [_next_row_id("metric")]


def _render_groups_editor() -> None:
    st.markdown("**Группы** (сумма долей должна быть равна 1)")
    header_cols = st.columns([3, 2, 1])
    header_cols[0].caption("Имя группы")
    header_cols[1].caption("Доля")
    for gid in list(st.session_state.design_group_ids):
        st.session_state.setdefault(f"group_name_{gid}", "")
        st.session_state.setdefault(f"group_prop_{gid}", 0.5)
        cols = st.columns([3, 2, 1])
        # label_visibility="collapsed" здесь допустим: колонки "Имя группы"/"Доля"
        # подписаны один раз общим заголовком выше (header_cols) — повторять его
        # на каждой строке было бы избыточно для повторяющегося редактора строк.
        cols[0].text_input("Имя группы", key=f"group_name_{gid}", label_visibility="collapsed")
        cols[1].number_input(
            "Доля", key=f"group_prop_{gid}", min_value=0.0, max_value=1.0, step=0.05,
            label_visibility="collapsed",
        )
        if cols[2].button("Удалить", key=f"group_del_{gid}"):
            st.session_state.design_group_ids.remove(gid)
            st.rerun()
    if st.button("+ Добавить группу", key="design_add_group"):
        st.session_state.design_group_ids.append(_next_row_id("group"))
        st.rerun()


def _render_metrics_editor(data: pd.DataFrame) -> None:
    st.markdown("**Метрики** (минимум одна)")
    numeric_columns = list(data.select_dtypes(include="number").columns)
    numeric_options = ["(нет)"] + numeric_columns
    binary_like_columns = [
        col
        for col in data.columns
        if pd.api.types.is_bool_dtype(data[col])
        or (
            pd.api.types.is_numeric_dtype(data[col])
            and set(data[col].dropna().unique()) <= {0, 1}
        )
    ]

    for mid in list(st.session_state.design_metric_ids):
        with st.container(border=True):
            cols = st.columns([2, 3, 2, 1])
            metric_type = cols[0].selectbox(
                "Тип", ["continuous", "binary", "ratio"], key=f"metric_type_{mid}"
            )
            if metric_type == "ratio":
                st.session_state.setdefault(f"metric_name_{mid}", "")
                cols[1].text_input(
                    "Имя метрики (ярлык)", key=f"metric_name_{mid}",
                    placeholder="например conv_rate",
                )
            else:
                cols[1].selectbox(
                    "Столбец датафрейма", list(data.columns), key=f"metric_name_{mid}",
                )
            cols[2].selectbox("Роль", ["primary", "secondary"], key=f"metric_role_{mid}")
            if cols[3].button("Удалить", key=f"metric_del_{mid}"):
                st.session_state.design_metric_ids.remove(mid)
                st.rerun()

            if metric_type == "ratio":
                sub = st.columns(2)
                sub[0].selectbox("Числитель (num)", numeric_options, key=f"metric_num_{mid}")
                sub[1].selectbox("Знаменатель (den)", numeric_options, key=f"metric_den_{mid}")
            else:
                st.selectbox(
                    "pre-period колонка (для CUPED, опционально)", numeric_options,
                    key=f"metric_precol_{mid}",
                )
                if metric_type == "binary":
                    hint = ", ".join(binary_like_columns) if binary_like_columns else "не найдено"
                    st.caption(f"Подходящие 0/1 колонки: {hint}")
    if st.button("+ Добавить метрику", key="design_add_metric"):
        st.session_state.design_metric_ids.append(_next_row_id("metric"))
        st.rerun()


def _render_absolute_mde_input(data: pd.DataFrame) -> None:
    """UI для режима "абсолютный MDE": выбор метрики + величина в ее единицах,
    с живым пересчетом в относительный MDE через baseline метрики."""
    metric_choices: list[tuple[str, str, str]] = []  # (mid, name, type)
    for mid in st.session_state.get("design_metric_ids", []):
        mname = st.session_state.get(f"metric_name_{mid}", "").strip()
        if mname:
            mtype = st.session_state.get(f"metric_type_{mid}", "continuous")
            metric_choices.append((mid, mname, mtype))

    if not metric_choices:
        st.warning("Сначала добавьте хотя бы одну метрику выше.")
        return

    metric_names = [name for _mid, name, _type in metric_choices]
    st.session_state.setdefault("design_mde_abs_metric", metric_names[0])
    if st.session_state["design_mde_abs_metric"] not in metric_names:
        st.session_state["design_mde_abs_metric"] = metric_names[0]
    chosen_name = st.selectbox(
        "Метрика, для которой задается абсолютный MDE", metric_names, key="design_mde_abs_metric",
    )
    chosen_mid, _name, chosen_type = next(m for m in metric_choices if m[1] == chosen_name)

    unit_hint = "в процентных пунктах, например 0.01 = +1 п.п." if chosen_type == "binary" else "в единицах метрики"
    st.session_state.setdefault("design_mde_abs", 0.0)
    st.number_input(f"Абсолютный MDE ({unit_hint})", step=0.01, key="design_mde_abs")

    kwargs = _metric_kwargs_from_session(chosen_mid)
    metric = None
    if kwargs is not None:
        try:
            metric = MetricConfig(**kwargs)
        except ValidationError:
            metric = None

    baseline_mean = compute_metric_baseline_mean(metric, data) if metric is not None else None
    abs_value = st.session_state.get("design_mde_abs", 0.0)

    if baseline_mean is None:
        st.error(
            f"Для метрики «{chosen_name}» не удалось определить baseline (среднее по "
            "pre-period данным) — для абсолютного MDE нужен baseline. Проверьте, что "
            "у метрики указана колонка с реальными значениями (для continuous/binary — "
            "имя метрики; для ratio — числитель и знаменатель)."
        )
    elif baseline_mean == 0:
        st.error(f"Baseline метрики «{chosen_name}» равен нулю — относительный MDE неопределен.")
    else:
        rel_mde = abs_value / baseline_mean
        st.caption(f"≈ {rel_mde:.1%} относительного MDE при текущем среднем {baseline_mean:.4g}")


def _metric_kwargs_from_session(mid: str) -> dict[str, Any] | None:
    """Собирает сырые kwargs для MetricConfig из session_state одного ряда
    формы метрик. None, если ряд не заполнен (имя пустое) — вызывающая
    сторона просто пропускает такой ряд."""
    mname = st.session_state.get(f"metric_name_{mid}", "").strip()
    if not mname:
        return None
    mtype = st.session_state.get(f"metric_type_{mid}", "continuous")
    mrole = st.session_state.get(f"metric_role_{mid}", "primary")
    kwargs: dict[str, Any] = dict(name=mname, type=mtype, role=mrole)
    if mtype == "ratio":
        num = st.session_state.get(f"metric_num_{mid}")
        den = st.session_state.get(f"metric_den_{mid}")
        kwargs["num"] = None if num == "(нет)" else num
        kwargs["den"] = None if den == "(нет)" else den
    else:
        pre_col = st.session_state.get(f"metric_precol_{mid}")
        if pre_col and pre_col != "(нет)":
            kwargs["pre_col"] = pre_col
    return kwargs


def _build_design_config_from_form(data: pd.DataFrame) -> DesignConfig | None:
    name = st.session_state.get("design_name", "").strip()
    unit_col = st.session_state.get("design_unit_col")

    groups: dict[str, float] = {}
    for gid in st.session_state.design_group_ids:
        gname = st.session_state.get(f"group_name_{gid}", "").strip()
        if gname:
            groups[gname] = float(st.session_state.get(f"group_prop_{gid}", 0.0))

    metrics: list[MetricConfig] = []
    for mid in st.session_state.design_metric_ids:
        kwargs = _metric_kwargs_from_session(mid)
        if kwargs is None:
            continue
        try:
            metrics.append(MetricConfig(**kwargs))
        except ValidationError as e:
            st.session_state.design_error = f"Ошибка в метрике '{kwargs['name']}': {e}"
            return None

    strata = st.session_state.get("design_strata", [])
    size_mode = st.session_state.get("design_size_mode")
    mde: float | None = None
    mde_abs_input: float | None = None
    mde_source_metric: str | None = None
    if size_mode == "mde_rel":
        mde = st.session_state.get("design_mde")
    elif size_mode == "mde_abs":
        chosen_name = st.session_state.get("design_mde_abs_metric")
        abs_value = st.session_state.get("design_mde_abs")
        metric = next((m for m in metrics if m.name == chosen_name), None)
        if metric is None:
            st.session_state.design_error = "Не удалось определить метрику для абсолютного MDE."
            return None
        baseline_mean = compute_metric_baseline_mean(metric, data)
        if baseline_mean is None:
            st.session_state.design_error = (
                f"Для метрики «{chosen_name}» не удалось определить baseline (нужна "
                "pre-period колонка с реальными значениями) — абсолютный MDE недоступен."
            )
            return None
        if baseline_mean == 0:
            st.session_state.design_error = (
                f"Baseline метрики «{chosen_name}» равен нулю — относительный MDE неопределен."
            )
            return None
        mde = abs_value / baseline_mean
        mde_abs_input = abs_value
        mde_source_metric = chosen_name
    sample_size = int(st.session_state["design_sample_size"]) if size_mode == "sample_size" else None
    split_method = st.session_state.get("design_split_method", "stratified")
    isolation_mode = st.session_state.get("design_isolation", "exclude")
    isolation_selected = st.session_state.get("design_isolation_selected", [])
    nan_strategy = st.session_state.get("design_nan_strategy", "separate_stratum")

    try:
        return DesignConfig(
            name=name,
            unit_col=unit_col,
            groups=groups,
            metrics=metrics,
            strata=strata,
            mde=mde,
            mde_abs_input=mde_abs_input,
            mde_source_metric=mde_source_metric,
            sample_size=sample_size,
            split_method=split_method,
            isolation=isolation_mode,
            isolation_selected_experiments=isolation_selected,
            nan_strategy=nan_strategy,
        )
    except ValidationError as e:
        st.session_state.design_error = f"Ошибка в конфиге дизайна: {e}"
        return None


def render_design_tab(experiments_dir: Path, current_user: CurrentUser | None = None) -> None:
    st.header("Дизайн эксперимента")
    if current_user is not None and current_user.role == "viewer":
        st.info("Недостаточно прав для создания экспериментов (нужна роль Editor или Admin).")
        return
    _init_design_state()
    _render_design_intro()

    col_upload, col_demo = st.columns([3, 1])
    with col_upload:
        uploaded = st.file_uploader(
            "Исторические данные (.csv/.parquet)", type=["csv", "parquet"], key="design_file"
        )
    with col_demo:
        st.write("")
        st.write("")
        if st.button("Загрузить демо-данные", key="design_load_demo"):
            n_demo = 5000
            st.session_state.design_data = generate_demo_design_data(n_demo, seed=0)
            st.session_state.design_name = _next_demo_name(experiments_dir)
            st.session_state.design_unit_col = "user_id"

            demo_config = make_demo_design_config("_template", n_demo, seed=0)

            st.session_state.design_group_ids = []
            for gname, gprop in demo_config.groups.items():
                gid = _next_row_id("group")
                st.session_state.design_group_ids.append(gid)
                st.session_state[f"group_name_{gid}"] = gname
                st.session_state[f"group_prop_{gid}"] = gprop

            st.session_state.design_metric_ids = []
            for metric in demo_config.metrics:
                mid = _next_row_id("metric")
                st.session_state.design_metric_ids.append(mid)
                st.session_state[f"metric_name_{mid}"] = metric.name
                st.session_state[f"metric_type_{mid}"] = metric.type
                st.session_state[f"metric_role_{mid}"] = metric.role
                if metric.type == "ratio":
                    st.session_state[f"metric_num_{mid}"] = metric.num
                    st.session_state[f"metric_den_{mid}"] = metric.den
                elif metric.pre_col:
                    st.session_state[f"metric_precol_{mid}"] = metric.pre_col

            st.session_state.design_strata = demo_config.strata
            st.session_state.design_split_method = demo_config.split_method
            st.session_state.design_size_mode = "sample_size"
            st.session_state.design_sample_size = demo_config.sample_size
            st.rerun()

    if uploaded is not None and st.session_state.get("_design_file_id") != uploaded.file_id:
        st.session_state.design_data = _load_uploaded(uploaded)
        st.session_state["_design_file_id"] = uploaded.file_id
    data = st.session_state.get("design_data")
    if data is None:
        st.info("Загрузите исторические данные или нажмите «Загрузить демо-данные».")
        return

    st.success(
        f"Файл загружен: {len(data)} строк, {len(data.columns)} колонок.\n\n"
        "**Что нужно сделать дальше:**\n"
        "1. Указать колонку с ID пользователя\n"
        "2. Настроить группы теста (control/treatment и их доли)\n"
        "3. Указать метрики, которые будете мерить, и их тип (continuous / binary / ratio)\n"
        "4. Указать колонки для стратификации\n"
        "5. Задать MDE или размер выборки"
    )
    st.caption(f"Колонки: {', '.join(data.columns)}")
    st.dataframe(data.head(20))

    for w in _sanitize_column_selections(data):
        st.warning(w)

    columns = list(data.columns)
    st.text_input("Имя эксперимента", key="design_name")
    st.selectbox("Колонка юнита (unit_col)", columns, key="design_unit_col")

    _render_groups_editor()
    _render_metrics_editor(data)

    strata_selected = st.multiselect("Страты (опционально)", columns, key="design_strata")

    if strata_selected:
        st.selectbox(
            "Что делать с пропусками в стратах",
            ["separate_stratum", "drop", "error"],
            format_func=lambda v: {
                "separate_stratum": "Выделить в отдельную страту 'unknown' (по умолчанию)",
                "drop": "Удалить юзеров с пропусками",
                "error": "Считать ошибкой дизайна",
            }[v],
            key="design_nan_strategy",
        )
        nan_strategy_selected = st.session_state.get("design_nan_strategy", "separate_stratum")
        for col in strata_selected:
            n_missing = int(data[col].isna().sum())
            if n_missing == 0:
                continue
            pct = n_missing / len(data) * 100
            if nan_strategy_selected == "drop":
                st.warning(
                    f"В колонке «{col}» {n_missing} пропусков ({pct:.1f}%). Эти юзеры "
                    "будут удалены из кандидатов (nan_strategy='drop')."
                )
            elif nan_strategy_selected == "error":
                st.warning(
                    f"В колонке «{col}» {n_missing} пропусков ({pct:.1f}%). При "
                    "nan_strategy='error' дизайн упадет с ошибкой — исправьте данные "
                    "или смените стратегию выше."
                )
            else:
                st.warning(
                    f"В колонке «{col}» {n_missing} пропусков ({pct:.1f}%). Они будут "
                    "выделены в отдельную страту 'unknown'."
                )

    st.radio(
        "Размер эксперимента",
        ["mde_rel", "mde_abs", "sample_size", "все доступные"],
        format_func=lambda v: {
            "mde_rel": "Задать целевой относительный MDE",
            "mde_abs": "Задать целевой абсолютный MDE",
            "sample_size": "Задать размер выборки",
            "все доступные": "Использовать все доступные данные",
        }[v],
        key="design_size_mode",
    )
    size_mode = st.session_state.get("design_size_mode")
    if size_mode == "mde_rel":
        st.session_state.setdefault("design_mde", 0.05)
        st.number_input(
            "Относительный MDE (например 0.05 = 5%)", min_value=0.0001, step=0.01, key="design_mde",
        )
    elif size_mode == "mde_abs":
        _render_absolute_mde_input(data)
    elif size_mode == "sample_size":
        st.session_state.setdefault("design_sample_size", 1000)
        st.number_input("Общий размер выборки", min_value=1, step=100, key="design_sample_size")

    st.selectbox("Метод сплита", ["stratified", "simple", "hash"], key="design_split_method")
    st.selectbox(
        "Изоляция от других активных экспериментов",
        ["exclude", "warn", "off", "exclude_selected"],
        format_func=lambda v: {
            "exclude": "exclude — исключить участников всех активных тестов (рекомендуется)",
            "warn": "warn — показать пересечение и спросить подтверждение",
            "off": "off — не исключать никого (осознанный риск пересечения)",
            "exclude_selected": "exclude_selected — исключить участников только выбранных тестов",
        }[v],
        key="design_isolation",
    )
    if st.session_state.get("design_isolation") == "exclude_selected":
        active_registry = _list_experiment_names(experiments_dir, active_only=True)
        current_name = st.session_state.get("design_name")
        active_names = [name for name in active_registry if name != current_name]
        if not active_names:
            st.warning("Нет активных (designed/running) экспериментов для выбора.")
        st.multiselect(
            "Эксперименты, из которых исключить пересекающихся участников",
            active_names,
            key="design_isolation_selected",
        )

    st.session_state.setdefault("design_running", False)
    design_clicked = st.button(
        "Спроектировать эксперимент", type="primary", key="design_submit",
        disabled=st.session_state.design_running,
    )
    if design_clicked:
        st.session_state.design_running = True
        st.session_state.design_error = None
        st.rerun()

    if st.session_state.design_running:
        config = _build_design_config_from_form(data)
        if config is None:
            st.session_state.design_running = False
            st.rerun()
        with st.status("Проектируем эксперимент...", expanded=True) as status:
            try:
                if current_user is not None:
                    from abkit import jobs

                    experiment = jobs.run_design(
                        current_user, config, data, experiments_dir=experiments_dir,
                        progress_callback=lambda label: st.write(label),
                    )
                else:
                    experiment = Experiment.design(
                        config, data, experiments_dir=experiments_dir,
                        progress_callback=lambda label: st.write(label),
                    )
            except (DesignError, storage.StorageError, AuthError) as e:
                status.update(label="Ошибка дизайна", state="error")
                st.session_state.design_running = False
                st.session_state.design_error = f"Ошибка дизайна: {e}"
                st.rerun()
            else:
                status.update(label="Эксперимент спроектирован ✓", state="complete")
                st.session_state.last_designed_experiment = experiment
                st.session_state.design_running = False
                _flash_success(f"Эксперимент «{experiment.name}» спроектирован.")
                st.rerun()

    if st.session_state.get("design_error"):
        st.error(st.session_state.design_error)

    experiment = st.session_state.get("last_designed_experiment")
    if experiment is not None:
        _render_design_summary(experiment)


def _render_design_summary(experiment: Experiment) -> None:
    st.subheader(f"Сводка: {experiment.name}")
    report = experiment.report

    group_sizes = report.group_sizes
    fig = go.Figure(go.Bar(x=list(group_sizes.keys()), y=list(group_sizes.values())))
    fig.update_layout(title="Размеры групп", yaxis_title="n", height=350)
    st.plotly_chart(fig)

    power_rows = []
    for metric_name, pr in report.power_results.items():
        power_rows.append(
            {
                "метрика": metric_name,
                "MDE (отн.)": f"{pr.mde_rel:.2%}" if pr.mde_rel is not None else "-",
                "MDE c CUPED": f"{pr.mde_rel_cuped:.2%}" if pr.mde_rel_cuped is not None else "-",
                "размер группы": f"{pr.sample_size_per_group:.0f}" if pr.sample_size_per_group is not None else "-",
            }
        )
    st.dataframe(pd.DataFrame(power_rows), hide_index=True)
    _help_expander("mde_table", table=True)

    nan_rows = []
    nan_pool = report.n_available + report.n_dropped_for_nan_strata
    for col, count in report.strata_nan_counts.items():
        if count == 0:
            continue
        pct = count / nan_pool * 100 if nan_pool else 0.0
        nan_rows.append({"колонка": col, "пропусков": count, "доля": f"{pct:.1f}%"})
    if nan_rows:
        st.markdown("**Пропуски в стратах**")
        st.dataframe(pd.DataFrame(nan_rows), hide_index=True)

    if report.warnings:
        for w in report.warnings:
            st.warning(w)

    design_report_path = experiment.path / "design_report.html"
    if design_report_path.exists():
        st.download_button(
            "Скачать design_report.html",
            data=design_report_path.read_bytes(),
            file_name="design_report.html",
            mime="text/html",
        )

    _render_samples_section(experiment.path, experiment.name, key_prefix="design")


def _zip_samples(csv_paths: list[Path]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for csv_path in csv_paths:
            zf.write(csv_path, arcname=csv_path.name)
    return buffer.getvalue()


def _render_samples_section(exp_path: Path, exp_name: str, key_prefix: str = "") -> None:
    """Секция «Выборки для передачи»: список CSV из samples/ + кнопки скачивания.

    Переиспользуется в табе Design (сразу после дизайна) и в табе Experiments
    (для любого ранее спроектированного эксперимента) — key_prefix нужен, чтобы
    ключи виджетов не конфликтовали при одновременном рендере в обоих табах.
    """
    samples_dir = exp_path / "samples"
    csv_paths = sorted(samples_dir.glob("*.csv")) if samples_dir.exists() else []
    if not csv_paths:
        st.info(
            "Выборки (samples/*.csv) для этого эксперимента не найдены — возможно, "
            "он был спроектирован до появления этой функции."
        )
        return

    st.markdown("### Выборки для передачи")
    for csv_path in csv_paths:
        n_rows = len(pd.read_csv(csv_path))
        size_kb = csv_path.stat().st_size / 1024
        col_label, col_button = st.columns([3, 1])
        col_label.write(f"**{csv_path.name}** — {n_rows} строк, {size_kb:.1f} КБ")
        col_button.download_button(
            "Скачать",
            data=csv_path.read_bytes(),
            file_name=csv_path.name,
            mime="text/csv",
            key=f"download_sample_{key_prefix}_{csv_path.name}",
        )

    st.download_button(
        "Скачать все выборки (ZIP)",
        data=_zip_samples(csv_paths),
        file_name=f"{exp_name}_samples.zip",
        mime="application/zip",
        key=f"download_all_samples_zip_{key_prefix}",
    )


# --------------------------------------------------------------------------
# Analyze
# --------------------------------------------------------------------------


_VERDICT_COLOR = {
    "significant_positive": "green",
    "significant_negative": "red",
    "no_effect_detected": "gray",
}


def _detailed_results_to_df(results, control_name: str) -> pd.DataFrame:
    return pd.DataFrame(results.detailed_display_rows(control_name))


def _render_analysis_results(results) -> None:
    for w in results.global_warnings:
        st.warning(w)

    context = results.context or {}
    control_name = context.get("control_name", "")

    st.markdown("### Вердикты")
    metrics = results.metrics
    cols = st.columns(len(metrics)) if metrics else []
    for col, metric_name in zip(cols, metrics):
        metric_results = [r for r in results[metric_name] if r.is_designed_method]
        with col:
            role_tag = " _(exploratory)_" if metric_results and metric_results[0].role == "secondary" else ""
            st.markdown(f"**{metric_name}**{role_tag}")
            for r in metric_results:
                verdict = results.verdict(metric_name, treatment_group=r.treatment_group)
                color = _VERDICT_COLOR[verdict]
                st.markdown(f":{color}[{verdict}]")
                st.caption(f"{r.treatment_group}: {r.effect_rel:.2%} (p={r.p_value:.4g})")

    st.markdown("### Детальная таблица результатов")
    detailed_df = _detailed_results_to_df(results, control_name)
    st.dataframe(
        detailed_df,
        hide_index=True,
        column_config={
            "Эффект (отн, %)": st.column_config.NumberColumn(format="%.2f%%"),
            "p-value": st.column_config.NumberColumn(format="%.4f"),
            "p-adj": st.column_config.NumberColumn(format="%.4f"),
        },
    )
    st.download_button(
        "Скачать таблицу CSV",
        data=detailed_df.to_csv(index=False).encode("utf-8"),
        file_name="detailed_results.csv",
        mime="text/csv",
        key="download_detailed_results_csv",
    )
    _help_expander("verdicts_table", table=True)

    st.markdown("### Графики")
    raw_values = context.get("raw_values", {})
    segment_results = context.get("segment_results", {})
    daily_results = context.get("daily_results", {})
    control_name = context.get("control_name")
    config = context.get("config")
    metrics_by_name = {m.name: m for m in config.metrics} if config else {}

    for metric_name in metrics:
        st.markdown(f"#### {metric_name}")
        st.plotly_chart(
            forest_plot(results[metric_name], title=f"{metric_name}: forest plot"),
        )
        _help_expander("forest")

        metric_config = metrics_by_name.get(metric_name)
        metric_type = metric_config.type if metric_config else "continuous"
        distribution_chart_type = "distribution_binary" if metric_type == "binary" else "distribution_continuous"
        metric_raw = raw_values.get(metric_name, {})
        control_series = metric_raw.get(control_name)
        show_full_range = False
        if metric_type != "binary" and control_series is not None:
            show_full_range = st.toggle(
                "Показать полный диапазон", key=f"dist_full_range_{metric_name}", value=False,
            )
        for treat_name, series in metric_raw.items():
            if treat_name == control_name or control_series is None:
                continue
            st.plotly_chart(
                distribution_plot(
                    control_series,
                    series,
                    metric_name=metric_name,
                    metric_type=metric_type,
                    control_name=control_name,
                    treat_name=treat_name,
                    clip_to_p99=not show_full_range,
                ),
            )
            if metric_type != "binary" and not show_full_range:
                combined = pd.concat([control_series.dropna(), series.dropna()])
                threshold, n_above, pct_above = p99_clip_stats(combined)
                if n_above > 0:
                    st.caption(
                        f"Для наглядности ось ограничена 99-м перцентилем ({threshold:.4g}). "
                        f"{n_above} наблюдений ({pct_above:.1f}%) выше порога собраны в "
                        "последний столбец."
                    )
            _help_expander(distribution_chart_type)

        for treat_name, seg_list in segment_results.get(metric_name, {}).items():
            if seg_list:
                st.warning(get_warning("segment_forest"))
                st.plotly_chart(
                    segment_forest_plot(seg_list, title=f"по стратам: {treat_name}"),
                )
                _help_expander("segment_forest")

        for treat_name, daily_df in daily_results.get(metric_name, {}).items():
            if daily_df is not None and not daily_df.empty:
                st.warning(get_warning("cumulative_lift"))
                st.plotly_chart(
                    cumulative_lift_plot(daily_df, title=f"кумулятивный лифт: {treat_name}"),
                )
                _help_expander("cumulative_lift")

    report_path = st.session_state.get("last_analysis_report_path")
    if report_path and Path(report_path).exists():
        st.markdown("### Скачать отчеты")
        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                "Скачать report.html", data=Path(report_path).read_bytes(),
                file_name="report.html", mime="text/html",
            )
        results_json_path = Path(report_path).parent / "results.json"
        if results_json_path.exists():
            with col2:
                st.download_button(
                    "Скачать results.json", data=results_json_path.read_bytes(),
                    file_name="results.json", mime="application/json",
                )


_ANALYZE_DEMO_EFFECT = 0.03


def render_analyze_tab(experiments_dir: Path, current_user: CurrentUser | None = None) -> None:
    st.header("Анализ по фактическим данным")
    registry = _list_experiment_names(experiments_dir)
    if not registry:
        st.info("Нет ни одного спроектированного эксперимента. Сначала перейдите в таб Design.")
        return

    exp_name = st.selectbox("Эксперимент", sorted(registry.keys()), key="analyze_exp_select")
    _render_analyze_intro()

    try:
        current_experiment = Experiment.load(exp_name, experiments_dir=experiments_dir)
    except storage.StorageError:
        current_experiment = None
    has_assignments = (
        current_experiment is not None
        and current_experiment.assignments is not None
        and len(current_experiment.assignments) > 0
    )

    col_upload, col_demo = st.columns([3, 1])
    with col_upload:
        uploaded = st.file_uploader(
            "Фактические данные (.csv/.parquet)", type=["csv", "parquet"], key="analyze_file"
        )
    with col_demo:
        st.write("")
        st.write("")
        demo_clicked = st.button(
            "Сгенерировать demo пост-данные (с эффектом)",
            key="analyze_load_demo",
            disabled=not has_assignments,
            help=None if has_assignments else "У этого эксперимента нет сохраненных assignments.",
        )
        if demo_clicked and current_experiment is not None:
            generated = generate_demo_post_data_for_config(
                current_experiment.config, current_experiment.assignments,
                effect=_ANALYZE_DEMO_EFFECT, seed=1,
            )
            st.session_state.analyze_data = generated
            metric_names = ", ".join(m.name for m in current_experiment.config.metrics)
            st.session_state.analyze_demo_info = (
                f"Сгенерированы демо пост-данные: {len(generated)} юзеров, метрики "
                f"[{metric_names}], подсажен эффект +{_ANALYZE_DEMO_EFFECT:.0%} в тестовой группе"
            )
            st.rerun()

    if uploaded is not None and st.session_state.get("_analyze_file_id") != uploaded.file_id:
        st.session_state.analyze_data = _load_uploaded(uploaded)
        st.session_state["_analyze_file_id"] = uploaded.file_id
        st.session_state.pop("analyze_demo_info", None)
    data = st.session_state.get("analyze_data")
    if data is not None:
        st.caption(f"{len(data)} строк, колонки: {', '.join(data.columns)}")
        if st.session_state.get("analyze_demo_info"):
            st.info(st.session_state.analyze_demo_info)

    compare = st.checkbox("Посчитать альтернативные методы (compare_methods)", key="analyze_compare")
    correction = st.selectbox("Поправка на множественность", ["holm", "bonferroni", "bh"], key="analyze_correction")

    date_col = None
    agg_methods: dict[str, str] = {}
    if data is not None:
        prev_date_col = st.session_state.get("analyze_date_col")
        if prev_date_col and prev_date_col != "(нет)" and prev_date_col not in data.columns:
            del st.session_state["analyze_date_col"]
            st.warning(
                f"Ранее выбранная колонка даты «{prev_date_col}» не найдена в новых "
                "данных, сброшено на «(нет)»."
            )
        date_choice = st.selectbox(
            "Колонка даты (опционально)", ["(нет)"] + list(data.columns),
            key="analyze_date_col",
            help=(
                "Опционально. Если ваши данные — одна строка на юзера, оставьте "
                "«(нет)». Если данные с разбивкой по дням (одна строка = юзер × "
                "день) — укажите колонку даты, программа сама агрегирует по юзеру "
                "для основного анализа и построит кумулятивный лифт по дням."
            ),
        )
        if date_choice != "(нет)":
            date_col = date_choice

        exp_for_check = current_experiment
        if exp_for_check is not None and exp_for_check.config.unit_col in data.columns:
            has_duplicates = data[exp_for_check.config.unit_col].duplicated().any()
            if has_duplicates and not date_col:
                st.warning(
                    f"В данных обнаружены дубли по «{exp_for_check.config.unit_col}» — "
                    "несколько строк на юзера. Укажите колонку даты выше, чтобы "
                    "программа агрегировала данные по юзеру автоматически, либо "
                    "агрегируйте их заранее (одна строка = один юзер)."
                )
            elif has_duplicates and date_col:
                n_users = data[exp_for_check.config.unit_col].nunique()
                n_days = data[date_col].nunique()
                st.info(
                    f"Данные содержат разбивку по дням ({n_users} уникальных юзеров × "
                    f"{n_days} дней). Программа автоматически агрегирует их для "
                    "основного анализа — способ агрегации можно настроить под каждой "
                    "метрикой ниже."
                )
                st.markdown("**Способ агрегации по дням**")
                for metric in exp_for_check.config.metrics:
                    agg_key = f"analyze_agg_{metric.name}"
                    st.session_state.setdefault(agg_key, _AGG_DEFAULT_LABEL_BY_TYPE[metric.type])
                    label = st.selectbox(
                        f"«{metric.name}»", list(_AGG_LABEL_TO_CODE.keys()), key=agg_key
                    )
                    agg_methods[metric.name] = _AGG_LABEL_TO_CODE[label]

    st.session_state.setdefault("analyze_running", False)
    analyze_clicked = st.button(
        "Проанализировать", type="primary", key="analyze_submit",
        disabled=st.session_state.analyze_running,
    )
    if analyze_clicked:
        if data is None:
            st.session_state.analyze_error = "Загрузите фактические данные"
        else:
            st.session_state.analyze_running = True
            st.session_state.analyze_error = None
            st.rerun()

    if st.session_state.analyze_running:
        with st.status("Анализируем результаты...", expanded=True) as status:
            try:
                experiment = Experiment.load(exp_name, experiments_dir=experiments_dir)
                results = experiment.analyze(
                    data, correction=correction, compare_methods=compare, date_col=date_col,
                    agg_methods=agg_methods or None,
                    progress_callback=lambda label: st.write(label),
                )
            except (checks.AnalysisError, DesignError, PipelineError, storage.StorageError, ValueError) as e:
                status.update(label="Ошибка анализа", state="error")
                st.session_state.analyze_running = False
                st.session_state.analyze_error = f"Ошибка анализа: {e}"
                st.rerun()
            else:
                st.write("Строим графики и сохраняем отчет...")
                report_path = results.report()
                status.update(label="Анализ завершен ✓", state="complete")
                st.session_state.last_analysis_results = results
                st.session_state.last_analysis_report_path = report_path
                st.session_state.analyze_running = False
                _flash_success(f"Анализ «{exp_name}» завершен.")
                st.rerun()

    if st.session_state.get("analyze_error"):
        st.error(st.session_state.analyze_error)

    if st.session_state.get("last_analysis_results") is not None:
        _render_analysis_results(st.session_state.last_analysis_results)


# --------------------------------------------------------------------------
# Experiments
# --------------------------------------------------------------------------


def _audit_log_to_df(entries: list) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "время": e.ts.strftime("%Y-%m-%d %H:%M:%S") if e.ts else "-",
                "действие": e.action,
                "пользователь": e.user_email or "-",
                "объект": e.object_name or "-",
                "детали": e.details or {},
            }
            for e in entries
        ]
    )


def _render_audit_history(exp_name: str) -> None:
    """DOCKER.md §6.2: события ЭТОГО эксперимента, видна ЛЮБОЙ роли (не только
    Admin) — в отличие от общей страницы «Аудит»."""
    from abkit.db.repositories import AuditRepo

    entries = AuditRepo().list_recent(object_name=exp_name, limit=200)
    if not entries:
        st.caption("Событий пока нет.")
        return
    st.dataframe(_audit_log_to_df(entries), hide_index=True)


def render_experiments_tab(experiments_dir: Path, current_user: CurrentUser | None = None) -> None:
    st.header("Реестр экспериментов")
    status_filter = st.selectbox(
        "Фильтр по статусу", ["все", "designed", "running", "completed", "archived"],
        key="exp_status_filter",
    )
    registry = _list_experiment_names(experiments_dir)
    if status_filter != "все":
        registry = {k: v for k, v in registry.items() if v["status"] == status_filter}

    if not registry:
        st.info("Экспериментов с таким статусом нет.")
        return

    df = pd.DataFrame(
        [{"эксперимент": k, **v} for k, v in sorted(registry.items())]
    )
    df = df[["эксперимент", "created_at", "path", "status", "started_at", "completed_at"]]
    st.dataframe(df, hide_index=True)

    st.divider()
    st.subheader("Управление статусом")
    all_registry = _list_experiment_names(experiments_dir)
    exp_name = st.selectbox("Эксперимент", sorted(all_registry.keys()), key="exp_status_select")
    current_status = all_registry[exp_name]["status"]
    st.write(f"Текущий статус: **{current_status}**")

    allowed = STATUS_TRANSITIONS.get(current_status, ())
    cols = st.columns(max(len(allowed), 1))
    for col, new_status in zip(cols, allowed):
        with col:
            if st.button(f"→ {new_status}", key=f"status_btn_{new_status}"):
                try:
                    if current_user is not None:
                        from abkit import jobs

                        jobs.run_update_status(current_user, exp_name, new_status)
                    else:
                        storage.update_status(experiments_dir, exp_name, new_status)
                except (storage.StorageError, AuthError) as e:
                    st.error(str(e))
                else:
                    st.success(f"«{exp_name}» переведен в статус «{new_status}»")
                    st.rerun()
    if not allowed:
        st.caption("Дальнейших переходов статуса нет (архивный эксперимент).")

    if current_user is not None and current_user.role == "admin":
        with st.expander("⚠️ Удалить эксперимент безвозвратно"):
            st.warning("Удаляются все данные эксперимента: назначения, датасеты, результаты анализов.")
            if st.button("Удалить безвозвратно", key="exp_delete_btn"):
                try:
                    from abkit import jobs

                    jobs.run_delete_experiment(current_user, exp_name)
                except (storage.StorageError, AuthError) as e:
                    st.error(str(e))
                else:
                    st.success(f"«{exp_name}» удален.")
                    st.rerun()

    if current_user is not None:
        st.divider()
        with st.expander("История"):
            _render_audit_history(exp_name)

    st.divider()
    st.subheader("Отчеты")
    exp_path = Path(all_registry[exp_name]["path"])
    report_choice = st.radio(
        "Какой отчет посмотреть?", ["design_report.html", "report.html"], key="exp_report_choice"
    )
    report_path = exp_path / report_choice
    if report_path.exists():
        with st.spinner("Загружаем отчет..."):
            st.iframe(report_path, height=800)
    else:
        st.info(f"{report_choice} еще не создан для этого эксперимента.")

    st.divider()
    _render_samples_section(exp_path, exp_name, key_prefix="experiments")


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def _aa_report_to_df(report: AAReport) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "метрика": m.metric,
                "группа": m.treatment_group,
                "метод": m.method,
                "n_sims": m.n_sims,
                "FPR": f"{m.fpr:.2%}",
                "ДИ (95%)": f"[{m.ci_low:.2%}, {m.ci_high:.2%}]",
                "статус": "ок" if m.passed else "ПРОВАЛ",
            }
            for m in report.methods
        ]
    )


def _ab_report_to_df(report: ABReport) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "метрика": m.metric,
                "группа": m.treatment_group,
                "метод": m.method,
                "n_sims": m.n_sims,
                "мощность (эмп.)": f"{m.empirical_power:.2%}",
                "мощность (аналит.)": f"{m.analytical_power:.2%}" if m.analytical_power is not None else "-",
            }
            for m in report.methods
        ]
    )


def render_validation_tab(experiments_dir: Path, current_user: CurrentUser | None = None) -> None:
    st.header("Валидация симуляциями")
    if current_user is not None and current_user.role == "viewer":
        st.info("Недостаточно прав для запуска валидации (нужна роль Editor или Admin).")
        return
    registry = _list_experiment_names(experiments_dir)
    if not registry:
        st.info("Нет ни одного спроектированного эксперимента.")
        return

    exp_name = st.selectbox("Эксперимент", sorted(registry.keys()), key="val_exp_select")
    uploaded = st.file_uploader(
        "Исторические данные для симуляции (.csv/.parquet)", type=["csv", "parquet"], key="val_file"
    )
    n_sims = st.number_input("Число симуляций", min_value=10, value=500, step=50, key="val_n_sims")
    compare = st.checkbox("Включить альтернативные методы", key="val_compare")
    run_ab_too = st.checkbox("Также посчитать A/B (нужен эффект)", key="val_run_ab")
    effect = None
    if run_ab_too:
        effect = st.number_input("Относительный эффект", value=0.05, step=0.01, key="val_effect")

    if st.button("Запустить валидацию", type="primary", key="val_submit"):
        if uploaded is None:
            st.error("Загрузите исторические данные")
            return
        data = _load_uploaded(uploaded)
        try:
            experiment = Experiment.load(exp_name, experiments_dir=experiments_dir)
        except storage.StorageError as e:
            st.error(str(e))
            return

        progress = st.progress(0.0, text="A/A симуляции: 0%")

        def _aa_cb(done: int, total: int) -> None:
            progress.progress(done / total, text=f"A/A симуляции: {done}/{total}")

        try:
            if current_user is not None:
                from abkit import jobs

                aa_report = jobs.run_validate_aa(
                    current_user, data, experiment.config, n_sims=int(n_sims), compare_methods=compare,
                    show_progress=False, progress_callback=_aa_cb,
                )
            else:
                aa_report = run_aa(
                    data, experiment.config, n_sims=int(n_sims), compare_methods=compare,
                    show_progress=False, progress_callback=_aa_cb,
                )
        except (checks.AnalysisError, KeyError, ValueError, AuthError) as e:
            st.error(f"Ошибка валидации: {e}")
            return
        progress.empty()

        st.markdown("### A/A: эмпирический FPR")
        st.dataframe(_aa_report_to_df(aa_report), hide_index=True)

        if effect is not None:
            progress2 = st.progress(0.0, text="A/B симуляции: 0%")

            def _ab_cb(done: int, total: int) -> None:
                progress2.progress(done / total, text=f"A/B симуляции: {done}/{total}")

            try:
                if current_user is not None:
                    from abkit import jobs

                    ab_report = jobs.run_validate_ab(
                        current_user, data, experiment.config, n_sims=int(n_sims), effect=float(effect),
                        compare_methods=compare, show_progress=False, progress_callback=_ab_cb,
                    )
                else:
                    ab_report = run_ab(
                        data, experiment.config, n_sims=int(n_sims), effect=float(effect),
                        compare_methods=compare, show_progress=False, progress_callback=_ab_cb,
                    )
            except (checks.AnalysisError, KeyError, ValueError, AuthError) as e:
                st.error(f"Ошибка валидации: {e}")
                return
            progress2.empty()

            st.markdown("### A/B: эмпирическая мощность")
            st.dataframe(_ab_report_to_df(ab_report), hide_index=True)


# --------------------------------------------------------------------------
# Admin (DOCKER.md §4.3) — виден только роли Admin, только в серверном режиме
# --------------------------------------------------------------------------

_ROLE_OPTIONS = ("viewer", "editor", "admin")


def _render_admin_user_form(current_user: CurrentUser, users: list) -> None:
    """Форма в стиле Superset "Edit User" — используется и для создания (+),
    и для редактирования существующего (email тогда read-only: это логин,
    смена требует отдельной логики уникальности/переизобретения токена)."""
    from abkit.auth.service import admin_create_user, admin_set_active, admin_set_role, admin_update_name

    editing_email = st.session_state.get("admin_edit_user_email")
    is_new = editing_email == "__new__"
    existing = None if is_new else next((u for u in users if u.email == editing_email), None)
    if not is_new and existing is None:
        st.session_state.admin_edit_user_email = None
        return

    st.markdown(f"**{'Новый пользователь' if is_new else 'Изменить пользователя'}**")
    with st.form("admin_user_edit_form"):
        name = st.text_input("Имя", value=existing.name if existing else "")
        if is_new:
            email = st.text_input("Email")
        else:
            st.text_input("Email", value=existing.email, disabled=True)
            email = existing.email
            st.caption("Email нельзя изменить — это логин пользователя.")
        active = st.checkbox("Активен", value=existing.is_active if existing else True)
        st.caption("Лучше деактивировать пользователя, чем удалять.")
        role = st.selectbox(
            "Роль", _ROLE_OPTIONS, index=_ROLE_OPTIONS.index(existing.role) if existing else 0,
        )
        password = None
        if is_new:
            password = st.text_input(
                "Временный пароль (опционально — если пусто, сгенерируется автоматически)",
                type="password",
            )
        col_save, col_back = st.columns(2)
        save_clicked = col_save.form_submit_button("Сохранить", type="primary")
        back_clicked = col_back.form_submit_button("Назад")

    if back_clicked:
        st.session_state.admin_edit_user_email = None
        st.rerun()

    if not save_clicked:
        return

    try:
        if is_new:
            _, generated = admin_create_user(
                current_user, email=email, name=name, role=role, password=password or None,
            )
            st.session_state.admin_last_password_reset = None if password else (email, generated)
        else:
            if name != existing.name:
                admin_update_name(current_user, target_email=email, name=name)
            if role != existing.role:
                admin_set_role(current_user, target_email=email, role=role)
            if active != existing.is_active:
                admin_set_active(current_user, target_email=email, is_active=active)
    except AuthError as e:
        st.error(str(e))
    else:
        st.session_state.admin_edit_user_email = None
        st.rerun()


def _render_admin_users_section(current_user: CurrentUser) -> None:
    from abkit.auth.service import admin_reset_password
    from abkit.db.repositories import UserRepo

    users = UserRepo().list_all()

    if st.session_state.get("admin_last_password_reset"):
        reset_email, generated = st.session_state.admin_last_password_reset
        st.info(f"Временный пароль для «{reset_email}» (сохраните — показывается один раз): `{generated}`")
        if st.button("Скрыть", key="admin_dismiss_password_banner"):
            st.session_state.admin_last_password_reset = None
            st.rerun()

    if st.session_state.get("admin_edit_user_email"):
        _render_admin_user_form(current_user, users)
        return

    col_title, col_add = st.columns([5, 1])
    col_title.markdown("**Пользователи**")
    if col_add.button("+ Добавить", key="admin_add_user_btn"):
        st.session_state.admin_edit_user_email = "__new__"
        st.rerun()

    if not users:
        st.info("Пользователей пока нет.")
        return

    widths = [2, 3, 1.2, 1, 1.6, 1.6, 0.7, 0.7, 0.7]
    header_cols = st.columns(widths)
    for col, label in zip(
        header_cols, ["Имя", "Email", "Роль", "Активен", "Создан", "Последний вход", "", "", ""]
    ):
        col.markdown(f"**{label}**")

    for u in users:
        cols = st.columns(widths)
        cols[0].write(u.name)
        cols[1].write(u.email)
        cols[2].write(u.role)
        cols[3].write("✅" if u.is_active else "🚫")
        cols[4].write(u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "-")
        cols[5].write(u.last_login_at.strftime("%Y-%m-%d %H:%M") if u.last_login_at else "-")
        if cols[6].button("✏️", key=f"admin_edit_{u.id}", help="Изменить"):
            st.session_state.admin_edit_user_email = u.email
            st.rerun()
        if cols[7].button("🔑", key=f"admin_reset_{u.id}", help="Сбросить пароль"):
            try:
                generated = admin_reset_password(current_user, target_email=u.email)
            except AuthError as e:
                st.error(str(e))
            else:
                st.session_state.admin_last_password_reset = (u.email, generated)
                st.rerun()
        block_help = "Заблокировать" if u.is_active else "Разблокировать"
        if cols[8].button("🚫" if u.is_active else "✅", key=f"admin_toggle_{u.id}", help=block_help):
            try:
                from abkit.auth.service import admin_set_active

                admin_set_active(current_user, target_email=u.email, is_active=not u.is_active)
            except AuthError as e:
                st.error(str(e))
            else:
                st.rerun()


def _render_admin_audit_section() -> None:
    from datetime import datetime, time as dt_time

    from abkit.db.repositories import AuditRepo, UserRepo

    st.subheader("Аудит")
    users = UserRepo().list_all()
    emails = [u.email for u in users]

    col1, col2 = st.columns(2)
    filter_email = col1.selectbox("Пользователь", ["(все)"] + emails, key="admin_audit_user")
    filter_action = col2.text_input(
        "Действие (например experiment.create)", key="admin_audit_action"
    )
    col3, col4, col5 = st.columns(3)
    filter_date_from = col3.date_input("С даты", value=None, key="admin_audit_date_from")
    filter_date_to = col4.date_input("По дату", value=None, key="admin_audit_date_to")
    page_size = col5.number_input(
        "Строк на странице", min_value=10, max_value=500, value=50, step=10, key="admin_audit_page_size"
    )

    user_id_filter = None
    if filter_email != "(все)":
        matched = next((u for u in users if u.email == filter_email), None)
        user_id_filter = matched.id if matched else None
    date_from = datetime.combine(filter_date_from, dt_time.min) if filter_date_from else None
    date_to = datetime.combine(filter_date_to, dt_time.max) if filter_date_to else None

    audit_repo = AuditRepo()
    total = audit_repo.count(
        user_id=user_id_filter, action=filter_action or None, date_from=date_from, date_to=date_to
    )
    page_size_int = int(page_size)
    total_pages = max(1, (total + page_size_int - 1) // page_size_int)
    st.session_state.setdefault("admin_audit_page", 0)
    st.session_state.admin_audit_page = min(st.session_state.admin_audit_page, total_pages - 1)

    col_prev, col_info, col_next = st.columns([1, 2, 1])
    if col_prev.button("← Назад", disabled=st.session_state.admin_audit_page == 0):
        st.session_state.admin_audit_page -= 1
        st.rerun()
    col_info.write(f"Страница {st.session_state.admin_audit_page + 1} из {total_pages} (всего {total})")
    if col_next.button("Вперед →", disabled=st.session_state.admin_audit_page >= total_pages - 1):
        st.session_state.admin_audit_page += 1
        st.rerun()

    entries = audit_repo.list_recent(
        user_id=user_id_filter, action=filter_action or None, date_from=date_from, date_to=date_to,
        limit=page_size_int, offset=st.session_state.admin_audit_page * page_size_int,
    )
    if not entries:
        st.info("Событий не найдено.")
    else:
        st.dataframe(_audit_log_to_df(entries), hide_index=True)


def render_admin_tab(current_user: CurrentUser | None) -> None:
    from abkit.auth.guards import require_admin

    try:
        require_admin(current_user)
    except AuthError as e:
        st.error(str(e))
        return

    st.header("Администрирование")
    users_subtab, audit_subtab = st.tabs(["👤 Пользователи", "📋 Аудит"])
    with users_subtab:
        _render_admin_users_section(current_user)
    with audit_subtab:
        _render_admin_audit_section()


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def render_sidebar(experiments_dir: Path, current_user: CurrentUser | None = None) -> None:
    st.sidebar.markdown(
        "<h2 style='margin:0; padding:0.2rem 0 0.6rem 0; font-weight:800;'>🧪 abkit</h2>",
        unsafe_allow_html=True,
    )
    if current_user is not None:
        st.sidebar.caption(f"{current_user.email} · {current_user.role}")
        if st.sidebar.button("Выйти", key="logout_btn"):
            _do_logout()
        st.sidebar.divider()
    else:
        st.sidebar.caption(f"experiments_dir:\n`{experiments_dir}`")
    registry = _list_experiment_names(experiments_dir)
    if not registry:
        st.sidebar.info("Экспериментов пока нет.")
        return
    df = pd.DataFrame(
        [{"эксперимент": k, "статус": v["status"]} for k, v in sorted(registry.items())]
    )
    st.sidebar.dataframe(df, hide_index=True)


def main() -> None:
    st.set_page_config(page_title="abkit", page_icon="🧪", layout="wide")
    st.markdown(
        """<style>
        /* поднимаем табы к верху страницы — не обрезаются, но лишний воздух
        над ними убран (стандартный отступ Streamlit — под "шапку" тулбара) */
        .block-container { padding-top: 2rem; }
        [data-testid="stSidebarContent"] { padding-top: 0.5rem; }

        /* sticky tabs (как навбар в Superset): панель вкладок остается видна
        при скролле, не уезжает с контентом. top учитывает высоту стандартного
        тулбара Streamlit (stHeader, ~60px) — без этого смещения панель
        прилипала бы ПОД тулбаром (обе прибиты к верху scroll-контейнера
        [data-testid="stMain"], тулбар просто рисуется поверх с более высоким
        z-index).

        Фон — ПРОВЕРЕНО через реальный браузер (Streamlit 1.59 emotion-css не
        выставляет ни CSS-переменной вроде --background-color, ни data-theme
        атрибута на html/.stApp — их просто нет в DOM, только сгенерированные
        st-emotion-cache-* классы). Единственный рабочий вариант без хрупкой
        привязки к внутренним классам Streamlit — prefers-color-scheme с
        реальными измеренными цветами темы Streamlit (light #ffffff, dark
        #0e1117 = rgb(14,17,23), подтверждено getComputedStyle(.stApp)).
        Не покрывает случай, когда пользователь вручную переключил тему в
        настройках Streamlit НЕ так, как у него в ОС — у самого Streamlit нет
        стабильного публичного способа узнать это из чистого CSS. */
        [data-testid="stTabs"] [role="tablist"] {
            position: sticky;
            top: 60px;
            z-index: 100;
            background-color: #ffffff;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.08);
        }
        @media (prefers-color-scheme: dark) {
            [data-testid="stTabs"] [role="tablist"] {
                background-color: #0e1117;
                box-shadow: 0 2px 4px rgba(0, 0, 0, 0.4);
            }
        }
        </style>""",
        unsafe_allow_html=True,
    )

    current_user: CurrentUser | None = None
    if _db_mode():
        from abkit.auth.tokens import TokenError, get_secret_key

        try:
            get_secret_key()
        except TokenError as e:
            st.error(f"Ошибка конфигурации сервера: {e}")
            st.stop()
            return
        current_user = _render_login_gate()
        if current_user is None:
            return

    experiments_dir = storage.get_experiments_dir()
    render_sidebar(experiments_dir, current_user)

    tab_labels = ["Design", "Analyze", "Experiments", "Validation"]
    if current_user is not None and current_user.role == "admin":
        tab_labels.append("Admin")
    tabs = st.tabs(tab_labels)
    tab_design, tab_analyze, tab_experiments, tab_validation = tabs[:4]
    with tab_design:
        render_design_tab(experiments_dir, current_user)
    with tab_analyze:
        render_analyze_tab(experiments_dir, current_user)
    with tab_experiments:
        render_experiments_tab(experiments_dir, current_user)
    with tab_validation:
        render_validation_tab(experiments_dir, current_user)
    if "Admin" in tab_labels:
        with tabs[4]:
            render_admin_tab(current_user)


main()
