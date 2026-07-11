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
- E2E (Playwright): **`bash scripts/e2e.sh`** из корня — ЕДИНСТВЕННЫЙ способ гонять e2e локально. Поднимает одноразовый стек под отдельным compose project name (свои volumes/сеть/порт :8090, `docker compose down -v` на выходе всегда), не трогает персистентный dev-стек на :8080. НИКОГДА не гонять `npx playwright test` вручную с `E2E_BASE_URL`, указывающим на dev-стек (:8080) — это и есть root cause накопления мусора в БД (см. `abkit/jobs.py::run_cleanup_dev` docstring, «Правило: гигиена dev-артефактов» ниже). CI (`.github/workflows/ci.yml` job `e2e`) достигает той же изоляции иначе — одноразовый раннер-VM + `docker compose down -v` в конце, поэтому там `npx playwright test` напрямую — нормально, это не тот контекст, где накапливается мусор.
- После правок backend-роутов — перегенерировать типы фронта: `cd frontend && npm run gen:api`.
- `scripts/*.sh` — `shellcheck scripts/*.sh` (локально `shellcheck` не стоит по умолчанию — ставится `pip install shellcheck-py`, либо `docker run --rm -v "$(pwd)/scripts:/scripts" koalaman/shellcheck:stable /scripts/*.sh`).

## Правило: пересборка локального стека после каждого пакета правок

После завершения КАЖДОГО пакета изменений, затрагивающего `frontend/` или `backend/` (не после каждого отдельного файла — после законченного пакета задачи), самостоятельно пересобрать и переподнять локальный Docker-стек, ПРЕЖДЕ чем отчитываться о завершении:

1. `docker compose up -d --build` — пересобирает измененные образы и поднимает стек.
2. Дождаться healthy всех сервисов (`docker compose ps`, все — `healthy`/`running`, не `starting`/`unhealthy`).
3. Smoke-проверка: страница логина отвечает (`curl -sf http://localhost:8080/login` или эквивалент) и API живо (`curl -sf http://localhost:8080/api/v1/version`).
4. В финальном отчете явно указать: "Стек пересобран и поднят, можно смотреть на localhost:8080 (Ctrl+Shift+R)" — либо, если пересборка не удалась, честно сообщить об ошибке вместо того чтобы промолчать про этот шаг.

Не применяется к пакетам, не трогающим `frontend/`/`backend/` (например, чисто `abkit/`-ядро без API-поверхности, только тесты, только документация) — в таких случаях пересборка ничего не меняет и не нужна.

## Правило: гигиена dev-артефактов на живом стеке (жесткий чек-лист)

Локальный docker-compose стек — персистентный (данные в volume переживают
рестарты, см. `docker/README.md`). Мягкая версия этого правила ("используй
префикс и убирай за собой") на практике не соблюдалась — на живом стеке
накопилось 171 тестовый эксперимент, 247 датасетов, 10 подключений к БД и 73
лишних пользователя за несколько сессий, прежде чем это заметили (разбор
причин и разовая уборка — см. историю; корень — п. (а) ниже). Действующая
версия — обязательный чек-лист, не рекомендация:

**(а) e2e ТОЛЬКО через одноразовое окружение.** `bash scripts/e2e.sh` —
единственный способ гонять Playwright локально: поднимает стек под отдельным
compose project name (свои volumes/сеть/порт :8090), тестирует, всегда
делает `docker compose down -v` на выходе. НИКОГДА не запускать
`npx playwright test`/`npm run test:e2e:raw` вручную с `E2E_BASE_URL`,
указывающим на персистентный dev-стек (:8080) — это и есть корень проблемы
(root cause), не гипотетический риск. CI (`e2e` job) изолирован иначе —
одноразовый раннер-VM + `down -v`, там прямой `npx playwright test` — ок,
это другой контекст.

**(б) Ручное создание сущностей — ТОЛЬКО через `abkit.dev_helpers.DevSession`**
(не pytest/Playwright — у них своя изоляция через (а)/conftest.py, это
правило их не касается). Прямой вызов `jobs.run_design`/`DatasetRepo().create()`/
`create_connection()`/т.п. в отладочном скрипте — считать ошибкой, не
шорткатом:

```python
from abkit.dev_helpers import DevSession
with DevSession() as dev:
    dev.design(current_user, config, data)         # name -> _dev_<name> принудительно
    dev.dataset(filename="probe.csv", ...)          # -> _dev_probe.csv
    dev.connection(current_user, display_name=...)  # -> _dev_<name>
# teardown() уже отработал на выходе из `with`
```

Префикс `_dev_` ставится автоматически (забыть — нельзя), все созданное
трекается, `teardown()` (или выход из `with`) убирает одним вызовом, не
полагаясь на память о том, что именно было создано.

**(в) Страховка — `abkit-admin cleanup-dev`** (`abkit/jobs.py::run_cleanup_dev`,
полный docstring там же): удаляет сущности с префиксом `_dev_` (любого
возраста) и все, что принадлежит аккаунтам `*@e2e.test`, старше
`--min-age-hours` (default 1 — не трогает то, что может быть еще в процессе).
Аккаунты `admin@e2e.test`/`viewer@e2e.test` (жестко зашиты в
`frontend/e2e/helpers.ts` как логин для e2e) не деактивируются никогда —
только то, что они создали. Ничего, принадлежащее email вне `@e2e.test` и не
с префиксом `_dev_`, не трогается — реальные данные невидимы для этой
команды по построению, не по списку исключений.

**(г) Обязательный вызов в конце КАЖДОГО пакета работ**, шагом перед
финальным отчетом:

```bash
docker compose exec backend abkit-admin cleanup-dev
```

И обязательная строка в отчете: **"cleanup: removed X dev artifacts"** или
**"cleanup: nothing to clean"** — не молчать про этот шаг ни в одном из двух
случаев.

## Правило: релизный процесс

Регламент эксплуатации (архитектура, деплой, обновление, откат, бэкап,
диагностика) — [docs/OPERATIONS.md](docs/OPERATIONS.md), скрипты —
`scripts/backup.sh`/`scripts/update.sh`. Постоянные правила (действуют во всех
будущих сессиях):

(а) **На серверы деплоятся ТОЛЬКО теги `v*`.** `main` — ветка разработки, на
    прод не катится напрямую (`scripts/update.sh` сам отказывается запускаться
    на чем-то, что не матчит `v*`). CI публикует образы в `ghcr.io` только на
    push тега `v*` (`.github/workflows/ci.yml`, джоба `build-and-push`).

(б) **Миграции БД — только аддитивные/обратно-совместимые.** Новые
    таблицы/колонки — можно всегда; удаление/переименование ИСПОЛЬЗУЕМОЙ
    колонки/таблицы — не раньше, чем через один релизный тег после того, как
    код перестал ее использовать (старый тег должен суметь безопасно
    откатиться на новый шаг схемы). Пример уже примененного паттерна —
    `analysis_results.dataset_id` (миграция 0009): `SET NULL` вместо жесткого
    удаления/CASCADE, старое значение не пропадает резко.

(в) **Перед тегом — полный прогон тестов и smoke на свежем стеке.** pytest +
    pyflakes + frontend typecheck/lint/build + Playwright e2e (`bash scripts/e2e.sh`,
    не `npx playwright test` вручную — см. «Правило: гигиена dev-артефактов»
    ниже) — все зеленые — и `docker compose up -d --build` с чистого
    состояния + smoke-чек (login, `/api/v1/version`) — до, не после,
    простановки тега.

**CHANGELOG.md** — ведется вручную при подготовке каждого релизного тега:
секция `## vX.Y.Z — <дата>` с перечнем крупных изменений с прошлого тега (не
построчный git log — смысловая выжимка, как существующие записи).

## Правило: актуальность документации

После КАЖДОГО пакета изменений, затрагивающего функциональность, UI, API,
схему БД или процессы эксплуатации — перед коммитом проверять и
актуализировать три документа:

- **[docs/USER_GUIDE.md](docs/USER_GUIDE.md)** — если изменилось что-то, что
  видит или делает пользователь (новые поля, кнопки, вкладки, изменение
  воркфлоу, новые метрики/методы).
- **[docs/OPERATIONS.md](docs/OPERATIONS.md)** — если изменились: сервисы
  compose, env-переменные, схема БД (новые таблицы/миграции), процедуры
  бэкапа/обновления, зависимости.
- **[docs/PRESENTATION.md](docs/PRESENTATION.md)** — только при
  появлении/удалении КРУПНЫХ возможностей (уровня «подключения к БД»,
  «guardrail-метрики»), мелочи сюда не тащить.

Механика: в конце каждого пакета — явный шаг «docs check»: пройтись по diff'у
изменений и определить, какие из трех файлов затронуты; обновить затронутые В
ТОМ ЖЕ пуше (можно отдельным коммитом `docs: sync with <feature>`); если не
затронут ни один — явно писать в отчете «docs: no changes needed», а не
промалчивать про этот шаг. При подготовке каждого релизного тега — секция в
CHANGELOG.md (правило релизного процесса выше). Это же правило распространяется
и на существующие технические ТЗ (DESIGN.md/DOCKER.md/FRONTEND.md) и
`docker/README.md`, где применимо — три файла выше просто НОВЫЕ обязательные
пункты чек-листа, не единственные документы в проекте.

CI-подстраховка (не блокирующая, `.github/workflows/ci.yml`, шаг «docs
freshness reminder»): если push меняет файлы в `frontend/src`, `backend/` или
`abkit/`, а `docs/` не тронуты и в сообщении коммита нет маркера `[docs:none]`
— шаг выводит warning-аннотацию, не валит билд. `[docs:none]` — осознанная
пометка «в этом коммите действительно нечего актуализировать в docs/», не
способ обойти проверку не думая.

## Осознанные решения по скоупу

- Сырой список файлов эксперимента (легаси-таб «Файлы»: имя+размер каждого файла без действий) в React-UI не портирован — чисто отладочная информация, реально не используется. См. FRONTEND.md §8 «Вне скоупа».
- Продукт называется **ABKit** (пользовательский брендинг) — технический идентификатор пакета/репозитория/путей остается `abkit` (нижний регистр), не переименовывается. Единый источник: `abkit.PRODUCT_NAME` (Python) и `frontend/src/branding.ts` (TS, синхронизировать вручную).
- Интерфейс (React-UI, HTML-отчеты, CLI `--help`) — полностью на английском. Внутренняя документация проекта (эти .md-файлы, комментарии в коде, docstrings, `git log`) остается на русском — так было и продолжает быть осознанным решением, не путать со scope перевода UI.

## Известный техдолг

- **Эксперименты адресуются по имени** (`experiments.name`, `Text unique`), не по `id`. Это работает корректно сегодня — уникальность имени обеспечена на уровне БД, и весь роутинг (фронт `encodeURIComponent` → nginx passthrough без URI-части в `proxy_pass` → FastAPI автоматически декодирует path-параметр → `ExperimentRepo.get_by_name`) проверен end-to-end на реальном кейсе с кириллицей/пробелом/двоеточием в имени (ref edb716f1, разбор — см. историю сессии) и работает без проблем. Известное ограничение: **переименование эксперимента (Properties) молча ломает существующие ссылки/закладки** — старый URL с прежним именем перестает резолвиться. Полная миграция адресации на `uuid` (роут `/experiments/{id}`, имя — только отображение) была спроектирована как системный фикс, но осознанно отложена — blast radius (~20 backend-роутов, вся фронтовая маршрутизация, ~60 e2e-спеков, десятки pytest-тестов, адресующих эксперимент по имени) сочтен непропорциональным при отсутствии живого бага в самом роутинге. Планировать эту миграцию отдельным пакетом в тихое окно, не смешивая с другими крупными изменениями.
- Две узкие проблемы из того же «класса» (адресация по имени) уже закрыты точечно, без миграции на uuid: скачивание samples с кириллицей в имени падало на `Content-Disposition`-заголовке (коммит `c0c8aea`, RFC 5987 `filename*=`); вкладка History путала события удаленного и заново созданного эксперимента с тем же именем из-за фильтрации `audit_log` по `object_name` вместо `object_id` (см. правки в `backend/routers/experiments.py`/`abkit/db/repositories.py::AuditRepo`).

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

**Подключения к БД** (`abkit/db_connections/`, таблица `database_connections`, `backend/routers/db_connections.py`): движки v1 — PostgreSQL (`psycopg`, уже основная зависимость), ClickHouse (`clickhouse-connect`, HTTP-диалект `clickhousedb://`, порты 8123/8443 — не «родной» TCP 9000/9440), MSSQL (`pymssql`/FreeTDS — выбран вместо `pyodbc`, чтобы не тянуть системный ODBC-драйвер в образ). Зависимости — extras `[db-connectors]` в `pyproject.toml`, ставятся в `docker/Dockerfile`. Пароль шифруется Fernet (`abkit/db_connections/crypto.py`), ключ выводится из `ABKIT_SECRET_KEY` (SHA256 → urlsafe base64) — отдельного `ABKIT_DB_ENCRYPTION_KEY` нет (см. `.env.example`: ротация `ABKIT_SECRET_KEY` делает сохраненные пароли подключений нерасшифровываемыми). API никогда не возвращает пароль (write-only поле). Управление — только admin, чтение списка (без пароля) — editor+. `POST .../{id}/test` и черновой `POST .../test-draft` (форма еще не сохранена) — `SELECT 1` с человеческим результатом (`abkit/db_connections/testing.py::_classify`, категории: ok/dns_error/tcp_timeout/auth_failed/db_not_found/error), таймаут 5-10с. dns_error и tcp_timeout — раньше были одной категорией `host_unreachable` (сообщение "Host unreachable or connection timed out" смешивало "хост не резолвится" с "хост резолвится, но порт не отвечает" — два разных поля формы нужно чинить в каждом случае); разделены после разбора жалобы на ложную диагностику. `test_connection()` логирует каждую попытку (`abkit.db_connections.testing`, engine/host/port/database/username/outcome — БЕЗ пароля).

**Датасет из SQL** (`abkit/db_connections/{sql_guard,sql_dataset}.py`, `POST /datasets/from-sql`): `sql_guard.py` на `sqlglot` — только SELECT/CTE (обходит попытки протащить мутацию через CTE типа `WITH t AS (INSERT ... RETURNING *) SELECT * FROM t`, проверяя ВСЕ узлы AST, не только корень), запрет multi-statement и `FOR UPDATE`. `sql_dataset.py::execute_select_to_parquet` — чанкинг через `pd.read_sql(..., chunksize=...)` (честный server-side stream курсор гарантирован только для Postgres/psycopg — у ClickHouse/MSSQL best-effort), пустой-но-типизированный `WHERE 1=0`-проб для схемы при 0 строках, обрезка по `ABKIT_SQL_MAX_ROWS` (default 5_000_000) с флагом `truncated`, таймаут `ABKIT_SQL_TIMEOUT_SEC` (default 300). **Известная ловушка**: psycopg возвращает postgres-колонку `uuid` как Python `uuid.UUID`, а `pa.Table.from_pandas` молча превращает такие объекты в сырые 16 байт вместо ошибки (данные тихо портятся) — `execute_select_to_parquet` явно стрингифицирует UUID-колонки (`_stringify_uuid_columns`) перед конвертацией в pyarrow; при добавлении новых типов из внешних БД проверяй тем же способом (`pd.read_parquet(...).dtypes` + `type(df[col].iloc[0])`), не доверяй молчаливому успеху записи. `POST /db-connections/{id}/preview` — первые 100 строк без материализации, тот же SELECT-only контроль. `POST /datasets/{id}/refresh` (Editor+, `run_refresh_sql_dataset`) — перевыполняет сохраненный `sql_text`, обновляет parquet и `fetched_at`; фетчит в отдельный временный файл и подменяет им живой `storage_path` только при полном успехе (`Path.replace`, атомарно) — обрыв соединения/исчезнувшая таблица посреди чанкинга не должны портить уже существующий снапшот. UI: hover-иконка в Actions списка датасетов + кнопка «Refresh from source» в drawer превью, обе за подтверждающим Modal, обе видны только Editor+.

**Датасет-центричная модель**: страница `/datasets` — единственное место загрузки файлов/создания SQL-датасетов (`frontend/src/pages/datasets/CreateDatasetModal.tsx`, две вкладки Upload/From SQL). Дизайн-визард (Step1Data), Analyze (AnalyzeSection), Validation — файлы напрямую больше не принимают, только `DatasetSelect` (`frontend/src/components/DatasetSelect.tsx`, поиск по существующим датасетам + ссылка «Create new dataset») или генерация демо-данных. `datasets.source` (upload|sql|demo), для source=sql — `connection_id`/`sql_text`/`fetched_at`/`source_schema`/`source_table` (последние два — см. «Schema/table browser» ниже). Таблица `experiment_datasets(experiment_id, dataset_id, kind)` — связь many-to-many, заполняется при ФАКТИЧЕСКОМ использовании датасета (design/analyze/validate), а не при простом выборе в UI; старые поля `datasets.experiment_id`/`kind` остаются как «primary/first-use» для обратной совместимости чтения.

**Schema/table browser** (`abkit/db_connections/introspection.py`, `GET /db-connections/{id}/schemas` и `.../schemas/{schema}/tables`, public_router, editor+): `sqlalchemy.inspect(engine)` для postgres/mssql; ClickHouse — прямые запросы к `system.databases`/`system.tables` (инспектор диалекта `clickhousedb` не поддерживает это надежно). In-process TTL-кэш 60с, ключ `(connection_id, schema)` — без Redis (проект принципиально однопроцессный, ThreadPoolExecutor jobs), `?refresh=true` (кнопка 🗘) обходит кэш. Frontend — общий компонент `frontend/src/components/datasets/SchemaTableCascade.tsx` (контролируемый: schema/table-состояние и связка с SQL-полем — у родителя), используется И в создании (`CreateDatasetModal.tsx::FromSqlTab`), И в редактировании (`EditDatasetModal.tsx`), чтобы правки не расходились между формами (см. ниже). Выбор Schema→Table автозаполняет `SELECT * FROM "schema"."table"` в SQL-поле (`buildSelectAllSql` в `parseSchemaTableFromSql.ts`) — при создании молча, если запрос еще не редактировался вручную (сравнение с последним автозаполненным значением, не просто "было пусто"); при редактировании — через `Modal.confirm`, потому что редактор при открытии Edit всегда содержит уже сохраненный (то есть заведомо "ручной") запрос. Там же — общий `frontend/src/components/datasets/QueryResultPreview.tsx` (кнопка + таблица результата, выполняет ТЕКУЩИЙ текст SQL-поля через `/db-connections/{id}/preview`).

**Persisted source schema/table** (баг-фикс поверх исходной Schema/table browser фичи — Edit открывался с пустым каскадом, потому что единственным механизмом было разобрать `sql_text` заново, а парсинг молча падал на кавычках, которые сам же каскад и генерирует, `"schema"."table"` — regex с финальным `\b` не матчится после закрывающей кавычки, не word-char): `datasets.source_schema`/`source_table` (миграция `0010_dataset_source_schema_table.py`, nullable, бэкофилл существующих `source='sql'` строк тем же парсингом) записываются ЯВНО при создании/правке — `DatasetFromSqlRequest`/`PatchDatasetRequest` получают эти два поля, фронт шлет их, только если текущий текст SQL байт-в-байт совпадает с `buildSelectAllSql(schema, table)` (иначе — `undefined`, то есть "не было осознанного выбора / запрос теперь другой"). `abkit/jobs.py::run_update_dataset` → `DatasetRepo.update_sql_source()` ВСЕГДА перезаписывает `source_schema`/`source_table` вместе с `sql_text` (в null, если не переданы) — не оставляет устаревшее значение при правке SQL руками, "не врать" про источник данных важнее, чем сохранить когда-то верную подсказку. `EditDatasetModal.tsx` при открытии предзаполняет каскад ИЗ ЭТИХ КОЛОНОК напрямую (не парсит `sql_text` заново); `parseSchemaTableFromSql.ts`/`abkit/db_connections/sql_parsing.py` (два независимых, вручную синхронизируемых порта одной логики — TS для live-фолбэка в Edit, Python только для бэкофилла в миграции) остаются резервным вариантом ТОЛЬКО для строк без этих колонок (старые датасеты, либо в принципе никогда не парсящийся сложный запрос) — если и это не сработало (JOIN/CTE/подзапрос/несколько `FROM`), каскад пустой с подсказкой "Custom query — table picker not applicable" вместо угадывания. Превью-drawer списка датасетов показывает те же поля строкой "Source: `<connection>` · `<schema>.<table>`" (или "· custom query").

**Edit/Delete датасета** (`abkit/jobs.py::run_update_dataset`/`run_delete_dataset`, право — владелец `uploaded_by` или Admin, не просто Admin как было в DB4): Delete проверяет использование (`experiment_datasets` + legacy `datasets.experiment_id`) — если ни один эксперимент не использует, обычный confirm; если использует — `DatasetInUseError` (маппится в 400 `confirmation_required` в `backend/errors.py`, как и остальные глобальные хендлеры), фронт показывает список экспериментов и требует ввести `DELETE`; повторный вызов с `confirm="DELETE"` проходит. Удаление датасета, на который ссылаются `analysis_results`, больше не падает с FK-violation — `analysis_results.dataset_id` теперь `ON DELETE SET NULL` (миграция 0009); поскольку это обнуляет сам dataset_id, отдельная колонка `analysis_results.dataset_filename` замораживает имя файла В МОМЕНТ АНАЛИЗА (`_save_analysis` в `backend/routers/experiments.py`) — `GET /experiments/{name}/results` читает именно её, не делает live-lookup, так что "какие данные анализировались" переживает удаление датасета. Edit для source=sql: смена `connection_id`/`sql_text` обновляет строку синхронно и запускает тот же джоб, что Refresh (переиспользует `run_refresh_sql_dataset`, которая читает поля СВЕЖИМИ из БД — отдельного кода "применить отредактированный SQL" нет). `EditDatasetModal.tsx` внизу показывает Collapse "Data preview" (развернут по умолчанию): для `source=sql` — Tabs с вкладками "Stored snapshot" (`components/datasets/DatasetSnapshotPreview.tsx`, первые 10 строк текущего сохраненного снапшота через `/datasets/{id}/preview`, тот же query-key `['dataset-preview', id]`, что и превью-drawer в списке) и "Query result" (тот же `QueryResultPreview`, кнопка "Preview query result" — можно сравнить сохраненный снапшот с результатом еще не сохраненной правки запроса); для `source=upload`/`demo` — только "Stored snapshot" (без Tabs, без Connection/SQL).

**Bulk select/delete** (`frontend/src/pages/Datasets.tsx`, паттерн переиспользован из `ExperimentsList.tsx`'s bulk select — `POST /datasets/bulk-delete`, `backend/routers/datasets.py`): чекбокс-колонка + панель действий, как у экспериментов, но подтверждение ОДНО на весь батч (не два уровня, как у одиночного удаления) — модалка сразу подтягивает usage (`GET /datasets/{id}/usage`) на каждый выбранный id и показывает "used by: ..." построчно, ввод `DELETE` разрешает удалить всё сразу, включая используемые (сервер вызывает `run_delete_dataset(..., confirm="DELETE")` безусловно). Права — per-item (owner/admin) на сервере, несовпадающие пропускаются с "no permission" в итоговом отчете, а не роняют весь запрос.

**Frontend**: `/admin/db-connections` (Settings → Data → Database Connections, только admin) — CRUD-страница в стиле Superset (`frontend/src/pages/admin/DatabaseConnections.tsx`). SQL-редактор в "From SQL" — обычный `Input.TextArea` (моно-шрифт), НЕ подсвеченный редактор: пробовали `react-simple-code-editor`, но библиотека стабильно роняла приложение (React error #130, чистый белый экран) после 2+ жестких переходов между страницами в одной сессии браузера — баг внутри самого `<Editor>` (воспроизводился даже с тривиальным `highlight={(code) => code}`, без Prism), библиотека полностью удалена (`npm uninstall`). Стабильность важнее подсветки синтаксиса.

## Теги для A/B тестов

По образцу тегов дашбордов Superset — свободная группировка/поиск, не контролируемый словарь.

**Модель** (миграция `0011_tags.py`, аддитивная): `tags` (`id`, `name` — `CITEXT unique`, тот же паттерн, что `users.email`, регистронезависимая уникальность без ручного `lower()`; `color` nullable, `created_by`, `created_at`) + `experiment_tags` — голая composite-PK связка (`experiment_id`, `tag_id`, оба `ON DELETE CASCADE`), без суррогатного `id`, в отличие от `experiment_datasets` (у той есть `kind` — колонка, оправдывающая отдельный `id`; здесь оправдывать нечем).

**API** (`abkit/jobs.py`: `run_create_tag`/`search_tags`/`run_set_experiment_tags`/`get_tag_usage_count`/`run_delete_tag`; `backend/routers/tags.py` + `PUT /experiments/{name}/tags` в `backend/routers/experiments.py`, не `{id}` — весь остальной API адресует эксперимент по имени, R2-решение, тег-эндпоинт не исключение): `GET /tags?q=` (typeahead, viewer+), `POST /tags` (editor+, **get-or-create** — `TagRepo.get_or_create()` возвращает существующий тег при регистронезависимом совпадении имени вместо ошибки, специально ради «ввод нового имени = тег уже существует» на UI не будучи отдельным кейсом), `PUT /experiments/{name}/tags` (owner/access-editor/admin — тот же `require_experiment_edit_access`, что у остальных Properties, ВСЕГДА полная замена списка, не дельта), `GET /tags/{id}/usage` (сколько экспериментов используют — для текста подтверждения перед удалением), `DELETE /tags/{id}` (только admin, снимает тег со всех экспериментов через `ON DELETE CASCADE`, не отдельным шагом). Все мутации — `audit_log` (`tag.create`, `experiment.tags_change`, `tag.delete`).

**Список экспериментов**: `ExperimentSummary`/`ExperimentDetail`/`ExperimentPropertiesOut` несут `tags: list[TagOut]`, `ExperimentTagRepo.list_for_experiments()` — один запрос на страницу списка, не N+1. `GET /experiments` получил `tag` (repeatable query param, AND-логика: эксперимент должен нести ВСЕ выбранные теги, не любой) и живой поиск `q` теперь матчит и по имени тега, не только по имени эксперимента.

**Frontend**: `components/TagBadge.tsx` (`TagBadge`/`TagList`, `+N`-сворачивание с tooltip) + `components/hashColor.ts` (детерминированный приглушенный цвет по hash строки — тот же палитра/алгоритм, что у аватарок владельцев в `UserAvatar.tsx`, вынесен в общий модуль вместо копии; цвет теговой бейджи ВСЕГДА считается по имени на клиенте, `tags.color` в БД не читается нигде — задел под ручной пикер, вне скоупа v1). `ExperimentPropertiesModal.tsx`: поле Tags — AntD `Select mode="tags"`, значения формы — ИМЕНА тегов (не id); при Save каждое имя резолвится в id через `POST /tags` (безопасно вызывать и для уже существующих имен — get-or-create), затем один `PUT .../tags` с полным списком id — Select ничего не знает про "новый или существующий тег", это решается только на сохранении. `ExperimentsList.tsx`: колонка Tags, мультиселект-фильтр по тегам (typeahead, AND), клик по бейджу — фильтр по этому тегу; поиск переведен с `Input.Search` (по Enter) на живой debounced `Input` (`useDebouncedValue`, вынесен в `hooks/useDebouncedValue.ts` — тот же хук, что уже был в `Datasets.tsx`, не копия), т.к. поиск теперь ищет и по тегам. `ExperimentPage.tsx`: бейджи тегов — ОТДЕЛЬНОЙ строкой под шапкой (не втиснуты в строку с статус-бейджами/Last modified — там и так `flexWrap`, но осознанно вынесено ниже, чтобы не грозило переносом строки шапки), клик — переход на `/experiments?tag=<id>` (список читает `tag` из URL при монтировании как начальное состояние фильтра — единственный способ передать фильтр между страницами, отдельного глобального стора фильтров нет).

Тесты: `tests/test_db_connections_core.py`, `backend/tests/test_db_connections.py`, `tests/test_sql_guard.py`, `tests/test_sql_dataset_core.py`, `tests/test_sql_parsing.py` (Python-порт `parseSchemaTableFromSql`, используемый миграцией 0010), `backend/tests/test_dataset_from_sql.py`, `tests/test_experiment_dataset_repo.py`, `backend/tests/test_experiment_datasets_link.py` — все против `testcontainers-postgres` (реальная БД, не моки), см. `feedback` в общем описании тестов выше. E2E: `frontend/e2e/database-connections.spec.ts` (полный цикл: создать подключение → test → preview SQL → создать датасет → дизайн на нем; тест на Edit — каскад предзаполнен из сохраненного SQL, обе вкладки превью, "Preview query result" отражает несохраненную правку, плюс проверка JOIN-запроса → пустой каскад с подсказкой; тест на создание через каскад → `source_schema`/`source_table` реально сохранены в БД и предзаполнены в Edit БЕЗ повторного парсинга), `frontend/e2e/datasets-page.spec.ts`, `frontend/e2e/dataset-management.spec.ts` (Edit/Delete/поиск на source=upload, включая снапшот-превью без Connection/SQL).
