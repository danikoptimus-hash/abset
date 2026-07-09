# FRONTEND.md — единое ТЗ: React-интерфейс abkit в стиле Apache Superset

Самодостаточное техническое задание. Заменяет собой все предыдущие ТЗ по фронтенду (REACT.md и др. — считать недействительными, ориентироваться ТОЛЬКО на этот документ). Итог: интерфейс на React + FastAPI, по концепции, UI и дизайну — копия Apache Superset с зеленым акцентом; все поднимается в docker одной командой; Streamlit в финале полностью удаляется.

> **Примечание (после R8, этапы DB1-DB5):** этот документ описывает состояние R1-R8 (визард дизайна и Анализ/Валидация принимали файл напрямую через drag&drop). После R8 добавлена датасет-центричная модель — Database Connections + датасеты из SQL, по образцу Superset Datasets — раздел 9 ниже описывает, что изменилось; §5.2 (описание страниц) оставлен как есть для истории миграции с upload-first на dataset-first UX.

## 0. Как работать по этому документу

- Работай автономно по этапам R1→R8, без промежуточных подтверждений пользователя, кроме одной стоп-точки перед этапом R8 (удаление Streamlit).
- Коммить после каждого логически завершенного куска (роутер, страница, компонент), push после каждого этапа. CI должен быть зеленым: при падении чини сам до 3 итераций, затем остановись и покажи находки.
- Этапы R1-R2 могли быть уже частично реализованы ранее (каркас FastAPI, auth, read-only эндпоинты). Перед стартом сверь фактическое состояние кода и тестов с этим ТЗ: сделанное и совпадающее — не переделывай; несоответствия этому документу — исправь в рамках ближайшего этапа.
- Ядро НЕ трогать: `abkit/design`, `abkit/analysis`, `abkit/validation`, `abkit/preprocessing`, `abkit/pipeline`, `abkit/checks.py`, статистические части `experiment.py`. Все 450+ существующих тестов ядра/БД остаются зелеными на каждом этапе. CLI (`cli.py`, `cli_admin.py`) сохраняется.
- БД-слой (`abkit/db/`), auth (`abkit/auth/`), аудит, jobs-обертки — переиспользуются. Новые поля/таблицы — только через миграции Alembic, описанные ниже.
- Streamlit (`app.py`) живет на `/legacy` КАК ВРЕМЕННЫЙ эталон поведения до этапа R8, затем удаляется полностью.

## 1. Концепция интерфейса: эксперимент = дашборд Superset

Страницы-сущности, а не страницы-функции. Никаких «табов Design/Analyze».

- **Верхняя навигация** (как Dashboards/Charts/Datasets в Superset): `A/B тесты` (/experiments, главная) · `Датасеты` (/datasets) · `Валидация` (/validation); справа — меню пользователя (Профиль, Admin — только для админов, Выйти).
- **Список тестов** — как список дашбордов: таблица с поиском/фильтрами и кнопкой **«+ Создать A/B тест»**, открывающей визард дизайна.
- **Страница теста** `/experiments/{id}` — центральная сущность (как страница дашборда): на одной странице гипотеза, дизайн, анализ, выводы, история.
- **Draft / Published** — редакционный статус, независимый от операционного (designed/running/completed/archived). Тест создается draft; публикуется, когда результаты финальны. Published видят все роли; draft — владелец и admin.
- **Режим Edit** — как у дашборда: редактирование markdown-блоков и названия.
- **Текстовые markdown-блоки** на странице теста: Гипотеза, Выводы, Решение + произвольные custom-блоки.

## 2. Целевая архитектура

```
┌────────────────────────── nginx :8080 ──────────────────────────┐
│  /            → статика React (frontend/dist)                    │
│  /api/*       → FastAPI (uvicorn :8000)                          │
│  /legacy/*    → Streamlit :8501 (ВРЕМЕННО, до R8)                 │
└──────────────────────────────────────────────────────────────────┘
        │                          │
   ┌────▼─────┐              ┌─────▼─────┐
   │ frontend │              │  backend  │ FastAPI + ядро abkit
   └──────────┘              └─────┬─────┘
                        ┌──────────┼──────────┐
                   ┌────▼───┐            ┌────▼────────┐
                   │Postgres│            │ volume /data │
                   └────────┘            └─────────────┘
```

Директории:

```
backend/
├── main.py            # FastAPI app, middleware, единый обработчик ошибок
├── deps.py            # DI: сессия БД, текущий пользователь, guard'ы ролей
├── routers/           # auth, experiments, design, analyze, validation,
│                      # datasets, blocks, admin, audit, jobs
├── schemas/           # Pydantic-схемы API (/api/v1)
├── jobs/              # менеджер фоновых задач (раздел 4)
└── tests/             # pytest + httpx AsyncClient

frontend/
├── src/
│   ├── api/           # клиент; типы генерируются из /api/openapi.json
│   │                  # (openapi-typescript) — ручных типов ответов не писать
│   ├── pages/         # ExperimentsList, ExperimentPage, ExperimentWizard,
│   │                  # Datasets, Validation, Admin, Audit, Login, Profile
│   ├── components/    # таблицы, формы, бейджи, Modal удаления, markdown-блоки
│   ├── charts/        # ECharts: forest, распределения, кумулятивный лифт и др.
│   ├── theme/tokens.ts# ЕДИНЫЙ источник цветов/шрифтов
│   └── auth/          # контекст пользователя, guard'ы роутов
├── vite.config.ts, tsconfig.json (strict), package.json
└── e2e/               # Playwright
```

Стек фронта: Vite + React 18 + TypeScript strict + **Ant Design 5** (компонентная база Superset) + TanStack Query + React Router + **ECharts** (echarts-for-react; plotly на фронт не тянуть).

## 3. Бэкенд: REST API (/api/v1)

### 3.1 Общее

- Аутентификация: существующий механизм abkit/auth — argon2, подписанный токен в HttpOnly cookie (Secure при TLS, SameSite=Lax + CSRF double-submit для мутаций, либо SameSite=Strict — выбери и зафиксируй решение в комментарии). Rate-limit логина (5 неудач → блок 15 мин, из БД) — подключить существующий.
- Все мутации — через сервисные функции с проверкой ролей и записью в audit_log. Проверки в UI — удобство; безопасность — на сервере.
- Единый формат ошибок: `{"error": {"code", "message", "details"}}`, сообщения на русском (тексты ошибок дизайна/анализа переиспользовать из текущих).
- OpenAPI на /api/openapi.json — источник типов фронта.
- Upload: стриминг на диск, лимит из env ABKIT_MAX_UPLOAD_MB.

### 3.2 Эндпоинты

```
POST   /auth/login {email,password} → cookie+user ; POST /auth/logout
GET    /auth/me ; POST /auth/change-password

GET    /experiments?status=&pub=&owner=&q=&page=   # список: фильтры по обоим
                                                    # статусам, владельцу, поиск
GET    /experiments/{id}            # конфиг + design_summary + файлы + blocks
POST   /experiments/{id}/status {to}       # designed→running→completed, →archived
PATCH  /experiments/{id} {publication_status?, name?}   # draft↔published, переименование
DELETE /experiments/{id} {confirm}  # сервер ТРЕБУЕТ confirm == "DELETE"
GET    /experiments/{id}/samples.zip ; /samples/{group}.csv
GET    /experiments/{id}/reports/design | /reports/analysis   # HTML-отчеты
GET    /experiments/{id}/results    # results.json последнего анализа
GET    /experiments/{id}/audit
GET/PUT /experiments/{id}/blocks    # markdown-блоки, upsert списком

POST   /datasets {file, kind, experiment_id?} → dataset_id, n_rows, columns, dtypes
GET    /datasets?page= ; GET /datasets/{id}/preview?rows=20

POST   /design {config, dataset_id} → job_id       # те же pydantic-валидации
POST   /experiments/{id}/analyze {dataset_id, options} → job_id
POST   /experiments/{id}/analyze/demo → job_id     # демо пост-данные + анализ
POST   /experiments/{id}/validate {n_sims, options} → job_id
GET    /jobs/{id} → {status, progress:{stage,pct,message}, result?, error?}

GET/POST /admin/users ; PATCH /admin/users/{id} ; POST .../reset-password  # admin
GET    /audit?user=&action=&page=                                          # admin
```

Изоляция при дизайне: режимы exclude/warn/off/exclude_selected — существующая логика. Для warn: job возвращает `requires_confirmation {overlap, by_experiment}`; повторный вызов с `confirmed: true` продолжает.

### 3.3 Новые сущности БД (миграции Alembic)

- `experiments.publication_status` text NOT NULL default 'draft' CHECK (draft|published). Переходы обратимы; право — владелец/admin; оба направления в audit_log. Видимость draft — владелец и admin (фильтр на уровне репозитория списка).
- Таблица `experiment_blocks` (id uuid PK, experiment_id FK ON DELETE CASCADE, kind CHECK (hypothesis|conclusion|decision|custom), title text, content_md text, position int, updated_by FK users, updated_at). При создании эксперимента автосоздаются пустые hypothesis/conclusion/decision.
- Таблица `jobs` (id, type, status, progress jsonb, result_ref, error, created_by, created_at, finished_at) — см. раздел 4.

## 4. Фоновые задачи (jobs)

Дизайн/анализ/симуляции — длительные; HTTP не должен висеть.

- Без Celery: ThreadPoolExecutor (env ABKIT_JOB_WORKERS, default 2) + таблица jobs в Postgres. Незавершенные при старте бэкенда помечаются failed с понятной ошибкой. Интерфейс JobRunner изолировать так, чтобы будущая замена на Celery не трогала роутеры.
- Прогресс по стадиям существующих функций (валидация→изоляция→мощность→сплит→сохранение; джойн→проверки→метрика i из N→поправка→отчет) транслируется в progress.
- Фронт поллит GET /jobs/{id} раз в 1с (TanStack Query refetchInterval).

## 5. Фронтенд

### 5.1 Тема — копия Superset, акцент зеленый (frontend/src/theme/tokens.ts + ConfigProvider)

```ts
colorPrimary: '#2E8B6D'   // hover #256F57, active #1F5C46
colorSuccess: '#2E8B6D'
colorWarning: '#C9A227'   // приглушенный, НЕ оранжевый
colorError:   '#D64545'
colorText:    '#484848'
colorBorder:  '#E0E0E0'
colorBgLayout:'#F7F7F7'
fontFamily:   'Inter, -apple-system, Helvetica, Arial, sans-serif'
fontSize: 14  // таблицы 13
borderRadius: 4
```

Требование «ни одного оранжевого»: проверить все состояния (hover/focus/active/disabled), бейджи, прогрессы, spinner'ы, палитры ECharts (задать глобальную тему графиков: значимое — зеленый, незначимое — серый, сетка светло-серая). Финальная проверка: grep собранного bundle на orange/#fa8c16/#ff7f0e + визуальный прогон.

Общий вид: светлая тема, много воздуха, компактные таблицы с тонкими границами, шапки таблиц 12px uppercase #666 на #F7F7F7, hover-строки #FAFAFA, secondary-кнопки outline-стиля, скругление 4px.

### 5.2 Страницы

**/login** — центрированная карточка, лого abkit, зеленая кнопка (как экран Superset).

**/experiments (главная)** — AntD Table: Название (ссылка) | Владелец | Операционный статус (бейдж: designed серый, running зеленый #E8F5F0/#2E8B6D, completed приглушенно-синий, archived блеклый) | Draft/Published (бейдж) | Изменен | Действия в строке (иконки с tooltip: перевести статус, открыть отчеты, скачать выборки, удалить). Поиск по названию, фильтры по обоим статусам и владельцу, пагинация. Кнопка «+ Создать A/B тест». Кнопки мутаций скрыты/disabled по правам (Viewer — ничего; Editor — только свои; Admin — все).

**/experiments/new — визард дизайна** (AntD Steps, состояние — один объект конфига):
1. *Данные*: drag&drop upload (csv/parquet) → превью строк, колонки с типами; кнопка «Демо-данные»; экспандеры-подсказки (перенести существующие тексты: «Что это за данные», пример таблицы, SQL-шаблон).
2. *Группы и метрики*: группы (дефолт Control/Test 0.5/0.5; пресеты 50/50, 90/10, 33/33/33, свое; live-валидация суммы, кнопка Нормализовать); метрики-карточки: отображаемое имя, столбец датафрейма (Select из колонок), тип, роль, pre-period колонка (Select числовых; для binary — подсказка 0/1-колонок).
3. *Параметры*: размер (относительный MDE / абсолютный MDE с live-пересчетом «≈ X% при среднем Y» / размер выборки / все данные); страты (мультиселект) + nan_strategy с предупреждением о доле пропусков; метод сплита с пояснениями; изоляция — 4 режима с человеческими подписями, для exclude_selected мультиселект активных экспериментов; режим warn → диалог подтверждения пересечения.
4. *Запуск*: сводка → «Спроектировать» → прогресс job по стадиям → редирект на страницу созданного эксперимента.

**/experiments/{id} — страница теста** (центральная):
- Шапка: название, оба бейджа статусов, владелец; кнопки: Edit, Publish/Unpublish, перевод операционного статуса, скачать выборки, удалить — Modal: текст «Будут удалены: назначения (N), датасеты (M), результаты (K)» с реальными числами + поле, кнопка удаления активна ТОЛЬКО при вводе ровно DELETE.
- Markdown-блок «Гипотеза».
- Секция «Дизайн»: конфиг (аккуратный вьювер), MDE-таблица (без/с CUPED), проверки сплита (SRM/баланс/pre-A/A бейджами с деталями), скачивание выборок, ссылка на design_report.html.
- Секция «Анализ»: upload пост-данных ИЛИ кнопка «Сгенерировать демо пост-данные (+3% эффект)» (disabled без assignments, tooltip-причина); опции (compare_methods, поправка, колонка даты с подсказкой про агрегацию дневных данных); запуск → прогресс по стадиям; результаты рендерятся ИЗ results.json НА ФРОНТЕ:
  карточки-вердикты; бейджи проверок честности; forest plot по каждой метрике (designed-цепочка выделена, ноль пунктиром); распределения (continuous — гистограммы+ECDF с клиппингом P99 и toggle полного диапазона; binary — bar-chart долей с ДИ Уилсона); кумулятивный лифт при наличии даты с обязательным peeking-предупреждением; эффект по сегментам с пометкой exploratory; детальная таблица всех сравнений (все колонки: метрика, сравнение, метод, designed✓, эффекты абс/отн, ДИ, p сырой/скорр., поправка, n, снижение дисперсии, вердикт) + экспорт CSV; у каждого графика Collapse «Как читать этот график?» (существующие тексты перенести).
  Кнопка «Скачать HTML-отчет» (серверная генерация остается).
- Markdown-блоки «Выводы», «Решение» (+ custom-блоки).
- Секция «История»: аудит эксперимента.
- Режим Edit: markdown-редактор с превью, добавление custom-блоков, переименование; Save/Discard; права владелец/admin.

**/datasets** — таблица: имя файла, kind, эксперимент (ссылка), строк, кто/когда загрузил; превью по клику (Drawer).

**/validation** — форма (эксперимент, данные/датасет, n_sims, альтернативные методы, A/B с эффектом) → прогресс → результат: FPR с ДИ и вердиктом «честный/врет», распределение p-value, мощность эмпирическая vs аналитическая.

**/admin** — Users (таблица + Modal create/edit: имя, email, активен с подписью «лучше деактивировать, чем удалять», роль; сброс пароля) и Audit (фильтры по пользователю/действию/дате, пагинация). **/profile** — смена пароля.

Роуты защищены по ролям (плюс сервер проверяет всегда).

## 6. Docker, nginx, CI

- docker-compose: сервис `backend` (uvicorn), сервис `frontend` собирается multi-stage в nginx-образ со статикой; текущий Streamlit-сервис переименовать в `legacy` (временно); nginx: `/`→статика, `/api`→backend, `/legacy`→streamlit (baseUrlPath). Порт наружу — только nginx :8080.
- Миграции Alembic и bootstrap-админ выполняет ТОЛЬКО entrypoint backend (убрать дублирование из legacy-entrypoint).
- .env.example дополнить: ABKIT_JOB_WORKERS, VITE_API_BASE (default /api/v1).
- CI: джоба test (pytest: ядро+БД+backend); джоба frontend (npm ci, typecheck, eslint, unit, build); джоба e2e (Playwright против docker compose) на PR и workflow_dispatch; build-and-push собирает оба образа. Опциональная джоба persistence (workflow_dispatch) — существующий docker/test_persistence.sh, обновить под новый состав сервисов.
- docker/README.md переписать под новый стек (deploy-сценарий, оба UI на период миграции, бэкап/восстановление — как раньше).

## 7. Этапы

**R1 — каркас API + auth.** main/deps, cookie-auth (login/logout/me/change-password), guard'ы ролей, формат ошибок, OpenAPI, rate-limit. Тесты httpx: логин/блокировка/роли/401/403. (Если уже реализовано ранее — сверить и доделать.)

**R2 — read-only API.** experiments список/деталь/файлы/отчеты/results, datasets список/preview, audit, admin users GET. Пагинация/фильтры/права. (Аналогично — сверить.)

**R3 — БД-новшества + jobs + мутации.** Миграции publication_status/experiment_blocks/jobs; менеджер jobs; design/analyze/demo/validate; статусы обоих типов; PATCH name/pub; blocks GET/PUT; DELETE с confirm; upload; admin-мутации. Интеграционный тест: полный цикл design→analyze через API на синтетике; изоляция warn→confirmed; удаление без DELETE → 400.

**R4 — каркас фронта + простые страницы.** Vite, тема-токены, навигация Superset-стиля, генерация типов из OpenAPI, Login, /experiments (список, без визарда — кнопка ведет на заглушку), /datasets, /admin, /audit, /profile. Playwright: логин, список, права, удаление с DELETE.

**R5 — визард + страница эксперимента (без Анализа).** /experiments/new полностью; /experiments/{id}: шапка, статусы, markdown-блоки с режимом Edit, секция Дизайн, История. Playwright: e2e создание теста на демо-данных → страница теста → publish → edit блока «Гипотеза».

**R6 — Анализ + Validation + графики.** Секция Анализ целиком, все ECharts-компоненты, «Как читать график», /validation. Playwright: демо пост-данные → анализ → вердикты и forest plot видны → экспорт таблицы.

**R7 — сборка.** Docker/nginx/CI по разделу 6; сценарий «git clone → cp .env → docker compose up -d → create-admin → полный цикл в React-UI» проходит на пересозданных с нуля контейнерах; test_persistence.sh зеленый.

>>> СТОП-ТОЧКА (единственная): после R7 остановись, покажи чек-лист паритета React-UI против /legacy по каждой странице/функции и жди явного подтверждения пользователя на R8. <<<

**R8 — полное удаление Streamlit.** После подтверждения: удалить app.py, abkit/ui/ (CSS-хаки/styles/theme для Streamlit), AppTest-тесты (tests/test_app.py и др.), зависимость streamlit из pyproject, сервис legacy и маршрут /legacy из compose/nginx, упоминания Streamlit из README (переписать README под новый стек: React UI + API + CLI + Python API), .streamlit/ каталог. Grep по репозиторию на «streamlit» — не должно остаться ничего, кроме истории в md-архивах, если пользователь их не удалил. Полный прогон тестов, зеленый CI, финальный тег версии v2.0.0.

## 8. Вне скоупа

Celery/Redis (интерфейс JobRunner заложен), SSE/WebSocket (поллинг), SSO/OAuth, мобильная верстка (десктоп-first), i18n (интерфейс русский), темная тема (токены не хардкодить — добавится позже).

Сырой список файлов эксперимента (легаси-таб «Файлы»: имя + размер каждого файла в experiment.path без дальнейшего действия) в React-UI сознательно не портирован — чисто отладочная информация, не используемая в реальной работе (осознанное решение при закрытии чек-листа паритета перед R8, см. CLAUDE.md).

## 9. После R8: Database Connections + датасет-центричная модель (DB1-DB5)

Загрузка файлов переехала полностью на страницу **/datasets** (кнопка **+
Dataset**, две вкладки: Upload file и From SQL —
`frontend/src/pages/datasets/CreateDatasetModal.tsx`). Визард дизайна (шаг
«Данные»), секция «Анализ» и **/validation** больше не принимают файл
напрямую — только `DatasetSelect` (`frontend/src/components/DatasetSelect.tsx`:
поиск по существующим датасетам, ссылка «Create new dataset» на /datasets)
или кнопка «Демо-данные»/«Демо пост-данные» (не изменилась).

**Settings → Data → Database Connections** (`/admin/db-connections`, только
Admin) — CRUD-страница в стиле Superset: список подключений (Name, Backend,
Last modified, Actions), модалка «+ Database» (движок с дефолтным портом,
host/port, database/username/password, display name, additional parameters
JSON, SSL-переключатель) с инлайн «Test connection». Датасеты из SQL —
вкладка From SQL в модалке создания датасета: выбор подключения → SQL
(`Input.TextArea`, без подсветки синтаксиса — см. ниже) → Preview (первые 100
строк) → имя → Create (джоб с прогрессом, как design/analyze).

Список датасетов (`/datasets`) получил колонку **Source** (Upload/SQL/Demo —
`SourceTag` в `DatasetSelect.tsx`), кнопку **Refresh** в действиях строки для
`source=sql` датасетов (перевыполняет сохраненный SQL) и показ сохраненного
SQL-запроса + «Last fetched» в drawer превью.

Backend/данные — см. [CLAUDE.md](CLAUDE.md) §«Database Connections +
датасет-центричная модель» (таблицы, шифрование, движки, sql_guard, лимиты) —
не дублируется здесь.

**Отказ от подсветки синтаксиса в SQL-редакторе**: пробовали
`react-simple-code-editor` — библиотека стабильно роняла всё приложение
(React error #130, белый экран) после нескольких жестких переходов между
страницами в одной сессии браузера, воспроизводимо даже с тривиальной
`highlight`-функцией без Prism. Удалена полностью; SQL вводится в обычную
`Input.TextArea` моношрифтом — стабильность важнее подсветки.
