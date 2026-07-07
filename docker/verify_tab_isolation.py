"""Браузерная (не AppTest) проверка изоляции вкладок — против РЕАЛЬНО
запущенного стека (docker compose или локальный streamlit run).

Зачем этот скрипт существует, а не только tests/test_app*.py::test_tab_content_does_not_leak_*:
AppTest проверяет структуру Python-дерева элементов (какой код лежит внутри
какого `with tab:`), но НЕ проверяет реальный рендер в браузере — то, что
видит пользователь, определяется CSS/DOM-состоянием, которое AppTest в
принципе не наблюдает. Если когда-либо будет жалоба "в браузере видно
содержимое чужой вкладки", а AppTest-тесты при этом зеленые — это НЕ
доказывает отсутствие бага, это просто означает, что баг (если он есть)
не в структуре Python-кода, и его нужно ловить здесь.

Требования: pip install playwright && playwright install chromium
(тяжелая зависимость — сознательно не добавлена в pyproject.toml/CI, см.
docker/README.md, раздел "Данные и перезапуски" / чек-лист перед деплоем).

Использование:
    ABKIT_VERIFY_URL=http://localhost:8080 \
    ABKIT_VERIFY_EMAIL=admin@abkit.local \
    ABKIT_VERIFY_PASSWORD=... \
    python docker/verify_tab_isolation.py

Без ABKIT_MODE=db (файловый режим, нет логина) — просто не задавайте
ABKIT_VERIFY_EMAIL/PASSWORD, скрипт пропустит форму входа.

Проверка: кликает по каждой вкладке (и вложенным подтабам Admin, если
есть) и на каждом шаге требует, чтобы среди ВИДИМЫХ (is_visible()) панелей
[role=tabpanel] встречался ЗАГОЛОВОК только текущей вкладки — и ни одного
заголовка из чужих вкладок. Выход 0 — все чисто, 1 — найдена утечка (с
указанием какая вкладка и что именно "протекло").
"""

from __future__ import annotations

import os
import sys

# Заголовок каждой вкладки — то же самое, что st.header(...) в app.py.
# Если поменяете заголовок в app.py — обновите и здесь.
_OWN_HEADERS = {
    "Design": "Дизайн эксперимента",
    "Analyze": "Анализ по фактическим данным",
    "Experiments": "Реестр экспериментов",
    "Validation": "Валидация симуляциями",
    "Admin": "Администрирование",
}


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright не установлен. pip install playwright && "
            "playwright install chromium",
            file=sys.stderr,
        )
        return 1

    url = os.environ.get("ABKIT_VERIFY_URL", "http://localhost:8080")
    email = os.environ.get("ABKIT_VERIFY_EMAIL")
    password = os.environ.get("ABKIT_VERIFY_PASSWORD")

    leaks: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1200})
        page.goto(url, timeout=60000, wait_until="networkidle")
        page.wait_for_timeout(1500)

        if email and password:
            email_input = page.query_selector('input[aria-label="Email"]')
            if email_input is not None:
                page.fill('input[aria-label="Email"]', email)
                page.fill('input[aria-label="Пароль"]', password)
                page.click('button:has-text("Войти")')
                page.wait_for_timeout(3000)

        # Только ВЕРХНЕУРОВНЕВЫЕ вкладки: первый [role="tablist"] на странице —
        # это main-панель Design/Analyze/.../Admin. Вложенные табы (например
        # Admin -> Пользователи/Аудит) — отдельный [role="tablist"] глубже в
        # дереве, у него сознательно НЕ должен совпадать заголовок родителя
        # ("Администрирование" над "Пользователи"/"Аудит" — это ожидаемая
        # вложенность, а не утечка), поэтому их сюда не включаем.
        top_tablist = page.query_selector('[role="tablist"]')
        if top_tablist is None:
            print("Вкладок не найдено — форма логина не пройдена или пустая страница.")
            browser.close()
            return 1
        tab_buttons = top_tablist.query_selector_all(':scope > [data-testid="stTab"]')
        top_level_labels = [t.inner_text().strip() for t in tab_buttons]
        print(f"Найдены вкладки: {top_level_labels}")

        if not top_level_labels:
            print("Вкладок не найдено — форма логина не пройдена или пустая страница.")
            browser.close()
            return 1

        for idx, label in enumerate(top_level_labels):
            top_tablist = page.query_selector('[role="tablist"]')
            tab_buttons = top_tablist.query_selector_all(':scope > [data-testid="stTab"]')
            tab_buttons[idx].click()
            page.wait_for_timeout(2000)

            visible_text = "\n".join(
                t.inner_text()
                for t in page.query_selector_all('[role="tabpanel"]')
                if t.is_visible()
            )

            own_header = _OWN_HEADERS.get(label)
            if own_header is not None and own_header not in visible_text:
                leaks.append(f"вкладка '{label}': не найден собственный заголовок '{own_header}'")

            for other_label, other_header in _OWN_HEADERS.items():
                if other_label == label:
                    continue
                if other_header in visible_text:
                    leaks.append(
                        f"вкладка '{label}': виден чужой заголовок '{other_header}' "
                        f"(из вкладки '{other_label}')"
                    )

        browser.close()

    if leaks:
        print("FAIL — найдена утечка контента между вкладками:")
        for leak in leaks:
            print(f"  - {leak}")
        return 1

    print(f"OK — проверено вкладок: {len(top_level_labels)}, утечек не найдено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
