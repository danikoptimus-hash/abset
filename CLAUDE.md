# abkit — заметки для Claude Code

Инструмент для A/B-тестирования (дизайн выборки, анализ результатов, A/A-/A/B-валидация). Полная техническая спецификация — в отдельных документах, читать их, а не пересказ здесь:

- [DESIGN.md](DESIGN.md) — ядро (`abkit/`): дизайн эксперимента, статистика, отчеты.
- [DOCKER.md](DOCKER.md) — командный Docker-режим: Postgres, роли/аутентификация, аудит-лог.
- [FRONTEND.md](FRONTEND.md) — история миграции интерфейса со Streamlit на React+FastAPI (этапы R1-R8, все завершены), архитектура `backend/` и `frontend/`.
- [README.md](README.md) — пользовательская документация (файловый и Docker-режимы).
- [docker/README.md](docker/README.md) — развертывание.

## Текущее состояние (после R8, миграция завершена)

Интерфейс — **React-UI** (`frontend/`, за `backend/` на FastAPI) плюс минимальный CLI (`cli.py`/`cli_admin.py`). Streamlit (`app.py`) полностью удален вместе с сервисом `legacy` и маршрутом `/legacy` — DESIGN.md §7 и DOCKER.md описывают его только как историю (см. примечания в начале этих файлов), актуальная архитектура — FRONTEND.md + `docker-compose.yml`.

После R8 добавлена датасет-центричная модель данных (этапы DB1-DB5, ниже) — Database Connections + датасеты из SQL, по образцу Superset. Своего отдельного .md-документа у этой фичи нет (не было отдельного «большого» ТЗ-файла как DOCKER.md/FRONTEND.md) — архитектура описана прямо здесь.

## Тесты

- Backend/ядро: `python -m pytest -q` (из корня, venv `.venv`), lint — `python -m pyflakes abkit backend tests migrations cli.py cli_admin.py conftest.py`.
- Frontend: `cd frontend && npm run typecheck && npm run lint && npm run build`.
- E2E (Playwright, против реального docker-compose стека, НЕ dev-сервера): `cd frontend && npx playwright test` с `E2E_BASE_URL`/`E2E_API_BASE`, см. `.github/workflows/ci.yml` job `e2e`.
- После правок backend-роутов — перегенерировать типы фронта: `cd frontend && npm run gen:api`.

## Правило: пересборка локального стека после каждого пакета правок

После завершения КАЖДОГО пакета изменений, затрагивающего `frontend/` или `backend/` (не после каждого отдельного файла — после законченного пакета задачи), самостоятельно пересобрать и переподнять локальный Docker-стек, ПРЕЖДЕ чем отчитываться о завершении:

1. `docker compose up -d --build` — пересобирает измененные образы и поднимает стек.
2. Дождаться healthy всех сервисов (`docker compose ps`, все — `healthy`/`running`, не `starting`/`unhealthy`).
3. Smoke-проверка: страница логина отвечает (`curl -sf http://localhost:8080/login` или эквивалент) и API живо (`curl -sf http://localhost:8080/api/v1/version`).
4. В финальном отчете явно указать: "Стек пересобран и поднят, можно смотреть на localhost:8080 (Ctrl+Shift+R)" — либо, если пересборка не удалась, честно сообщить об ошибке вместо того чтобы промолчать про этот шаг.

Не применяется к пакетам, не трогающим `frontend/`/`backend/` (например, чисто `abkit/`-ядро без API-поверхности, только тесты, только документация) — в таких случаях пересборка ничего не меняет и не нужна.

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

## Database Connections + датасет-центричная модель (DB1-DB5)

По образцу Superset (Database Connections + Datasets как единственный источник данных).

**Подключения к БД** (`abkit/db_connections/`, таблица `database_connections`, `backend/routers/db_connections.py`): движки v1 — PostgreSQL (`psycopg`, уже основная зависимость), ClickHouse (`clickhouse-connect`, HTTP-диалект `clickhousedb://`, порты 8123/8443 — не «родной» TCP 9000/9440), MSSQL (`pymssql`/FreeTDS — выбран вместо `pyodbc`, чтобы не тянуть системный ODBC-драйвер в образ). Зависимости — extras `[db-connectors]` в `pyproject.toml`, ставятся в `docker/Dockerfile`. Пароль шифруется Fernet (`abkit/db_connections/crypto.py`), ключ выводится из `ABKIT_SECRET_KEY` (SHA256 → urlsafe base64) — отдельного `ABKIT_DB_ENCRYPTION_KEY` нет (см. `.env.example`: ротация `ABKIT_SECRET_KEY` делает сохраненные пароли подключений нерасшифровываемыми). API никогда не возвращает пароль (write-only поле). Управление — только admin, чтение списка (без пароля) — editor+. `POST .../{id}/test` и черновой `POST .../test-draft` (форма еще не сохранена) — `SELECT 1` с человеческим результатом (ok/host unreachable/auth failed/db not found), таймаут 5-10с.

**Датасет из SQL** (`abkit/db_connections/{sql_guard,sql_dataset}.py`, `POST /datasets/from-sql`): `sql_guard.py` на `sqlglot` — только SELECT/CTE (обходит попытки протащить мутацию через CTE типа `WITH t AS (INSERT ... RETURNING *) SELECT * FROM t`, проверяя ВСЕ узлы AST, не только корень), запрет multi-statement и `FOR UPDATE`. `sql_dataset.py::execute_select_to_parquet` — чанкинг через `pd.read_sql(..., chunksize=...)` (честный server-side stream курсор гарантирован только для Postgres/psycopg — у ClickHouse/MSSQL best-effort), пустой-но-типизированный `WHERE 1=0`-проб для схемы при 0 строках, обрезка по `ABKIT_SQL_MAX_ROWS` (default 5_000_000) с флагом `truncated`, таймаут `ABKIT_SQL_TIMEOUT_SEC` (default 300). **Известная ловушка**: psycopg возвращает postgres-колонку `uuid` как Python `uuid.UUID`, а `pa.Table.from_pandas` молча превращает такие объекты в сырые 16 байт вместо ошибки (данные тихо портятся) — `execute_select_to_parquet` явно стрингифицирует UUID-колонки (`_stringify_uuid_columns`) перед конвертацией в pyarrow; при добавлении новых типов из внешних БД проверяй тем же способом (`pd.read_parquet(...).dtypes` + `type(df[col].iloc[0])`), не доверяй молчаливому успеху записи. `POST /db-connections/{id}/preview` — первые 100 строк без материализации, тот же SELECT-only контроль. `POST /datasets/{id}/refresh` — перевыполняет сохраненный `sql_text`, обновляет parquet и `fetched_at`.

**Датасет-центричная модель**: страница `/datasets` — единственное место загрузки файлов/создания SQL-датасетов (`frontend/src/pages/datasets/CreateDatasetModal.tsx`, две вкладки Upload/From SQL). Дизайн-визард (Step1Data), Analyze (AnalyzeSection), Validation — файлы напрямую больше не принимают, только `DatasetSelect` (`frontend/src/components/DatasetSelect.tsx`, поиск по существующим датасетам + ссылка «Create new dataset») или генерация демо-данных. `datasets.source` (upload|sql|demo), для source=sql — `connection_id`/`sql_text`/`fetched_at`. Таблица `experiment_datasets(experiment_id, dataset_id, kind)` — связь many-to-many, заполняется при ФАКТИЧЕСКОМ использовании датасета (design/analyze/validate), а не при простом выборе в UI; старые поля `datasets.experiment_id`/`kind` остаются как «primary/first-use» для обратной совместимости чтения.

**Frontend**: `/admin/db-connections` (Settings → Data → Database Connections, только admin) — CRUD-страница в стиле Superset (`frontend/src/pages/admin/DatabaseConnections.tsx`). SQL-редактор в "From SQL" — обычный `Input.TextArea` (моно-шрифт), НЕ подсвеченный редактор: пробовали `react-simple-code-editor`, но библиотека стабильно роняла приложение (React error #130, чистый белый экран) после 2+ жестких переходов между страницами в одной сессии браузера — баг внутри самого `<Editor>` (воспроизводился даже с тривиальным `highlight={(code) => code}`, без Prism), библиотека полностью удалена (`npm uninstall`). Стабильность важнее подсветки синтаксиса.

Тесты: `tests/test_db_connections_core.py`, `backend/tests/test_db_connections.py`, `tests/test_sql_guard.py`, `tests/test_sql_dataset_core.py`, `backend/tests/test_dataset_from_sql.py`, `tests/test_experiment_dataset_repo.py`, `backend/tests/test_experiment_datasets_link.py` — все против `testcontainers-postgres` (реальная БД, не моки), см. `feedback` в общем описании тестов выше. E2E: `frontend/e2e/database-connections.spec.ts` (полный цикл: создать подключение → test → preview SQL → создать датасет → дизайн на нем), `frontend/e2e/datasets-page.spec.ts`.
