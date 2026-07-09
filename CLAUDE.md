# abkit — заметки для Claude Code

Инструмент для A/B-тестирования (дизайн выборки, анализ результатов, A/A-/A/B-валидация). Полная техническая спецификация — в отдельных документах, читать их, а не пересказ здесь:

- [DESIGN.md](DESIGN.md) — ядро (`abkit/`): дизайн эксперимента, статистика, отчеты.
- [DOCKER.md](DOCKER.md) — командный Docker-режим: Postgres, роли/аутентификация, аудит-лог.
- [FRONTEND.md](FRONTEND.md) — история миграции интерфейса со Streamlit на React+FastAPI (этапы R1-R8, все завершены), архитектура `backend/` и `frontend/`.
- [README.md](README.md) — пользовательская документация (файловый и Docker-режимы).
- [docker/README.md](docker/README.md) — развертывание.

## Текущее состояние (после R8, миграция завершена)

Интерфейс — **React-UI** (`frontend/`, за `backend/` на FastAPI) плюс минимальный CLI (`cli.py`/`cli_admin.py`). Streamlit (`app.py`) полностью удален вместе с сервисом `legacy` и маршрутом `/legacy` — DESIGN.md §7 и DOCKER.md описывают его только как историю (см. примечания в начале этих файлов), актуальная архитектура — FRONTEND.md + `docker-compose.yml`.

## Тесты

- Backend/ядро: `python -m pytest -q` (из корня, venv `.venv`), lint — `python -m pyflakes abkit backend tests migrations cli.py cli_admin.py conftest.py`.
- Frontend: `cd frontend && npm run typecheck && npm run lint && npm run build`.
- E2E (Playwright, против реального docker-compose стека, НЕ dev-сервера): `cd frontend && npx playwright test` с `E2E_BASE_URL`/`E2E_API_BASE`, см. `.github/workflows/ci.yml` job `e2e`.
- После правок backend-роутов — перегенерировать типы фронта: `cd frontend && npm run gen:api`.

## Осознанные решения по скоупу

- Сырой список файлов эксперимента (легаси-таб «Файлы»: имя+размер каждого файла без действий) в React-UI не портирован — чисто отладочная информация, реально не используется. См. FRONTEND.md §8 «Вне скоупа».
- Продукт называется **ABKit** (пользовательский брендинг) — технический идентификатор пакета/репозитория/путей остается `abkit` (нижний регистр), не переименовывается. Единый источник: `abkit.PRODUCT_NAME` (Python) и `frontend/src/branding.ts` (TS, синхронизировать вручную).
- Интерфейс (React-UI, HTML-отчеты, CLI `--help`) — полностью на английском. Внутренняя документация проекта (эти .md-файлы, комментарии в коде, docstrings, `git log`) остается на русском — так было и продолжает быть осознанным решением, не путать со scope перевода UI.

## Permissions model

Роли: `viewer` < `editor` < `admin` (см. DOCKER.md §4.1) — базовая ролевая матрица не изменилась. Поверх нее — per-experiment права (UX-пакет, `abkit/access.py`): у эксперимента есть один изначальный `owner_id` (создатель) плюс необязательные дополнительные владельцы/редакторы в таблице `experiment_access` (Edit Properties modal, как в Superset), и опциональное `experiments.visible_roles` (ограничение видимости published-эксперимента конкретными ролями).

`access-editor` в таблице ниже = пользователь с строкой в `experiment_access` (access `owner` или `editor` — обе дают одинаковые права редактирования, различие между ними чисто информационное в Properties modal).

| Действие                                   | viewer | editor (не owner/access) | access-editor | owner (`owner_id`) | admin |
|---------------------------------------------|:------:|:-------------------------:|:--------------:|:-------------------:|:-----:|
| View (список/детали, с учетом видимости)     |   ✓¹   |            ✓¹              |       ✓        |          ✓           |   ✓   |
| Analyze / Validate                          |        |            ✓¹              |       ✓¹        |          ✓¹           |   ✓   |
| Edit blocks (Hypothesis/Conclusions/Decision)|        |                            |       ✓        |          ✓           |   ✓   |
| Rename                                       |        |                            |       ✓        |          ✓           |   ✓   |
| Operational status (running/completed/...)   |        |                            |       ✓        |          ✓           |   ✓   |
| Publication status (draft/published)         |        |                            |       ✓        |          ✓           |   ✓   |
| Edit Properties (owners/editors/visible_roles)|        |                            |       ✓        |          ✓           |   ✓   |
| Delete                                       |        |                            |       ✓        |          ✓           |   ✓   |
| Admin-функции (users, action log)            |        |                            |                |                      |   ✓   |

¹ Analyze/Validate — намеренно НЕ ограничены владением/грантом (см. `tests/test_jobs_permission_matrix.py::test_run_analyze_editor_allowed_on_others_experiment`): любой editor+ может анализировать/валидировать ЛЮБОЙ эксперимент, который он ВИДИТ (колонка View) — гейт по видимости применяется на уровне HTTP-роутера (`backend/routers/experiments.py`, `_visible_or_404` перед постановкой job в очередь), не на уровне `abkit/jobs.py`. Draft-эксперимент без гранта editor'у не видим (View = ✗ для чужого editor) → следовательно и Analyze/Validate для него недоступны, хотя формально это два разных, независимо применяемых правила, а не одна проверка. Тест на саму видимость: `backend/tests/test_analyze_validate_jobs.py::test_analyze_blocked_on_experiment_editor_cannot_see`.

Видимость (`can_view_experiment`, `abkit/access.py`): owner/access-editor/admin видят всегда; published без `visible_roles` видят все; published с `visible_roles` — только перечисленные роли (плюс owner/access-editor/admin); draft — только owner/access-editor/admin.

Тесты на всю эту модель разом (GET/PUT `/properties`, грант через `experiment_access`, фильтрация списка по `visible_roles`): `backend/tests/test_experiment_properties.py`. Regression-guard на английский UI (0.8): `tests/test_no_cyrillic_in_ui.py` — AST-скан Python (без docstring/attribute-docstring) + regex-скан `frontend/src/**/*.ts*` (без комментариев, `schema.ts` исключен как генерируемый) на кириллицу.
