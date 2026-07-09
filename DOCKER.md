# DOCKER.md — ТЗ: упаковка abkit в полноценный Docker-контейнер с учетками, ролями и БД

Техническое задание для реализации поверх текущего (на момент написания) состояния проекта abkit (Streamlit-приложение + библиотека). Цель — превратить локальный инструмент в командный сервис, который разворачивается так же просто, как Apache Superset: `git clone` → `docker compose up` → работает.

> **Примечание (после R8 FRONTEND.md):** сервис `app` (Streamlit) ниже по документу — историческая архитектура на момент написания этого ТЗ. Streamlit удален, интерфейс — React-UI + FastAPI backend (сервисы `backend`/`frontend`), см. [FRONTEND.md](FRONTEND.md) и актуальные [docker-compose.yml](docker-compose.yml) / [docker/README.md](docker/README.md). Модель ролей/аутентификации/аудита/БД (разделы 3-6, 9-11) не менялась и остается актуальной — только транспорт (UI) сменился.
>
> **Примечание (Database Connections, DB1-DB5):** схема раздела 5 ниже не включает таблицы, добавленные позже — `database_connections` (подключения к внешним БД, пароль зашифрован Fernet) и `experiment_datasets` (связь эксперимент↔датасет many-to-many с `kind`); таблица `datasets` дополнена полями `source` (upload|sql|demo), `connection_id`, `sql_text`, `fetched_at`. Новые env: `ABKIT_SQL_MAX_ROWS`, `ABKIT_SQL_TIMEOUT_SEC` (см. `.env.example`). Архитектура и мотивация — [CLAUDE.md](CLAUDE.md) §«Database Connections + датасет-центричная модель», настройка — [docker/README.md](docker/README.md) §«Подключения к базам данных».

Референс по UX учетных записей — Apache Superset: администратор создает пользователей через веб-интерфейс или CLI, назначает роли, пользователи логинятся по email/паролю. Повторяем эту модель.

## 1. Целевой результат

После выполнения этого ТЗ:

1. Развертывание с нуля: `git clone ... && cd abkit && cp .env.example .env && docker compose up -d` — через 1-2 минуты сервис доступен на `http://<host>:8080`.
2. Первый администратор создается одной командой: `docker compose exec app abkit-admin create-admin --email admin@company.com` (аналог `superset fab create-admin`).
3. Все пользователи логинятся по email/паролю. Незалогиненный не видит ничего, кроме страницы входа.
4. Три роли: **Admin**, **Editor**, **Viewer** (описание в разделе 4).
5. Все данные — эксперименты, конфиги, выборки (control/treatment assignments), загруженные пост-данные, статусы, даты, пользователи, аудит действий — живут в Postgres и на именованном docker-volume. Контейнер приложения полностью stateless: его можно убить и пересоздать без потери данных.
6. Структурированное логирование (JSON) + аудит-лог действий пользователей, доступный в UI.
7. Обновление версии: `git pull && docker compose build && docker compose up -d` — миграции БД применяются автоматически при старте.

## 2. Архитектура: сервисы docker-compose

```
┌─────────────────────────────────────────────────┐
│                    nginx :8080                   │  reverse proxy, gzip,
│                                                  │  websocket-проброс для Streamlit
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│                app (abkit)                       │  Streamlit + abkit
│  streamlit run app.py --server.port 8501         │  auth middleware, UI
└──────┬──────────────────────────────┬───────────┘
       │                              │
┌──────▼───────────┐        ┌─────────▼───────────┐
│  postgres:16     │        │  volume: abkit_data  │  тяжелые файлы:
│  метаданные,     │        │  /data/experiments   │  parquet, HTML-отчеты
│  users, roles,   │        │                      │
│  assignments,    │        └──────────────────────┘
│  audit_log       │
└──────────────────┘
```

Состав `docker-compose.yml`:

| Сервис    | Образ                    | Назначение                                             |
|-----------|--------------------------|--------------------------------------------------------|
| app       | build: ./docker/Dockerfile | Streamlit-приложение + библиотека abkit + CLI abkit-admin |
| postgres  | postgres:16-alpine       | Вся реляционная часть (раздел 5)                       |
| nginx     | nginx:alpine             | Reverse proxy на :8080, websocket upgrade для Streamlit |

Redis/Celery в этой версии НЕ вводим (симуляции продолжают выполняться в процессе приложения с st.status-прогрессом). Заложить в код изоляцию «тяжелых» вызовов в отдельный модуль `abkit/jobs.py`, чтобы воркеры можно было добавить позже без переписывания.

Требования к compose:
- `restart: unless-stopped` у всех сервисов.
- healthcheck у postgres (`pg_isready`) и app (`curl -f http://localhost:8501/_stcore/health`).
- app стартует только после healthy postgres (`depends_on: condition: service_healthy`).
- Именованные volumes: `abkit_pgdata` (база), `abkit_data` (файлы экспериментов).
- Все порты наружу — только nginx :8080. Postgres наружу не публикуется.

## 3. Конфигурация через переменные окружения

Файл `.env.example` (копируется в `.env`, git-ignored):

```env
# --- Обязательные ---
ABKIT_SECRET_KEY=change-me-long-random-string   # подпись сессий; генерировать: openssl rand -hex 32
POSTGRES_PASSWORD=change-me

# --- С разумными дефолтами ---
POSTGRES_USER=abkit
POSTGRES_DB=abkit
DATABASE_URL=postgresql+psycopg://abkit:${POSTGRES_PASSWORD}@postgres:5432/abkit
ABKIT_DATA_DIR=/data/experiments        # путь внутри контейнера (volume abkit_data)
ABKIT_PORT=8080                         # внешний порт nginx
ABKIT_LOG_LEVEL=INFO
ABKIT_LOG_FORMAT=json                   # json | text
ABKIT_SESSION_LIFETIME_HOURS=72
ABKIT_MAX_UPLOAD_MB=400
ABKIT_ALLOW_SELF_REGISTRATION=false     # как в Superset: по умолчанию учетки заводит админ
```

Правило: в коде не должно остаться ни одного пути/секрета, зашитого в settings.yaml. `settings.yaml` остается только для statistical-дефолтов (alpha, power, correction) и читается как fallback; все инфраструктурное — из env. Валидация env при старте: отсутствие ABKIT_SECRET_KEY или дефолтное значение "change-me..." в production-режиме → приложение не стартует, в лог пишется понятная ошибка.

## 4. Пользователи, роли, аутентификация (модель Superset)

### 4.1 Роли

| Право                                                        | Viewer | Editor | Admin |
|--------------------------------------------------------------|:------:|:------:|:-----:|
| Видеть список экспериментов, отчеты, скачивать выборки и CSV  |   ✓    |   ✓    |   ✓   |
| Создавать эксперименты (Design), запускать Analyze/Validation |        |   ✓    |   ✓   |
| Менять статус / редактировать / архивировать СВОИ эксперименты|        |   ✓    |   ✓   |
| Менять/архивировать ЧУЖИЕ эксперименты                        |        |        |   ✓   |
| Удалять эксперименты                                          |        |        |   ✓   |
| Управлять пользователями (создание, роли, сброс пароля, блокировка) |  |        |   ✓   |
| Смотреть общий аудит-лог                                      |        |        |   ✓   |

У каждого эксперимента есть владелец (`owner_id`) — тот, кто его создал. «Свои» = owner совпадает с текущим пользователем.

### 4.2 Аутентификация

- Email + пароль. Пароли хранить только как hash (argon2id, библиотека `argon2-cffi`; bcrypt допустим как fallback).
- Сессии: подписанный токен (itsdangerous / PyJWT, HS256 с ABKIT_SECRET_KEY) в cookie. В Streamlit использовать `st.context.cookies` для чтения и компонент/JS-мост для установки cookie, либо проверенную библиотеку streamlit-authenticator — НО с заменой ее хранилища на нашу БД. Выбор обосновать в PR: критерий — надежная работа logout и истечения сессии.
- Экран логина — единственное, что видит незалогиненный пользователь. Никакие данные экспериментов не рендерятся до проверки сессии (проверка — первой строкой app.py, до любого обращения к БД).
- Блокировка перебора: после 5 неудачных попыток подряд — блокировка входа для email на 15 минут (запись в БД, не в память процесса).
- Смена пароля пользователем: страница «Профиль». Сброс пароля — только через админа (генерируется временный пароль, флаг must_change_password=true). Почтовую рассылку в этой версии не делаем.
- `ABKIT_ALLOW_SELF_REGISTRATION=true` включает страницу самостоятельной регистрации: новый пользователь получает роль Viewer. По умолчанию выключено — учетки заводит админ, как в Superset.

### 4.3 Управление пользователями

Вкладка **Admin** в UI (видна только роли Admin):
- Таблица пользователей: email, имя, роль, активен/заблокирован, дата создания, последний вход.
- Кнопки: создать пользователя (email, имя, роль, временный пароль), изменить роль, заблокировать/разблокировать, сбросить пароль.
- Все действия пишутся в аудит-лог.

CLI-утилита `abkit-admin` (entrypoint в pyproject.toml), работает внутри контейнера:

```bash
abkit-admin create-admin --email admin@co.com [--name "Admin"] [--password ...]
abkit-admin create-user  --email u@co.com --role editor
abkit-admin reset-password --email u@co.com
abkit-admin list-users
```

Если пароль не передан — сгенерировать и напечатать в stdout один раз.

## 5. База данных: схема Postgres

Использовать SQLAlchemy 2.x + Alembic (миграции). Схема v1:

```sql
users (
    id            uuid PK default gen_random_uuid(),
    email         citext UNIQUE NOT NULL,
    name          text NOT NULL DEFAULT '',
    password_hash text NOT NULL,
    role          text NOT NULL CHECK (role IN ('viewer','editor','admin')),
    is_active     boolean NOT NULL DEFAULT true,
    must_change_password boolean NOT NULL DEFAULT false,
    failed_logins int NOT NULL DEFAULT 0,
    locked_until  timestamptz NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz NULL
)

experiments (
    id            uuid PK,
    name          text UNIQUE NOT NULL,           -- человекочитаемое имя, как сейчас
    owner_id      uuid NOT NULL REFERENCES users(id),
    status        text NOT NULL CHECK (status IN ('designed','running','completed','archived')),
    config        jsonb NOT NULL,                 -- полный DesignConfig + вычисленное (бывший config.yaml)
    design_summary jsonb NULL,                    -- MDE-таблица, размеры групп, проверки сплита
    created_at    timestamptz NOT NULL DEFAULT now(),
    started_at    timestamptz NULL,
    completed_at  timestamptz NULL,
    archived_at   timestamptz NULL
)

assignments (                                     -- контрольные/тестовые выборки
    experiment_id uuid REFERENCES experiments(id) ON DELETE CASCADE,
    unit_id       text NOT NULL,
    group_name    text NOT NULL,
    stratum       text NULL,
    assigned_at   timestamptz NOT NULL,
    PRIMARY KEY (experiment_id, unit_id)
)
-- индексы: (experiment_id, group_name); (unit_id) — для изоляции между экспериментами

datasets (                                        -- загруженные файлы: pre-данные дизайна и пост-данные
    id            uuid PK,
    experiment_id uuid REFERENCES experiments(id) ON DELETE CASCADE,
    kind          text NOT NULL CHECK (kind IN ('pre_design','post_analysis','validation')),
    filename      text NOT NULL,
    n_rows        bigint NOT NULL,
    columns       jsonb NOT NULL,
    storage_path  text NOT NULL,                  -- путь к parquet на volume /data
    sha256        text NOT NULL,                  -- контроль целостности и дедупликация
    uploaded_by   uuid REFERENCES users(id),
    uploaded_at   timestamptz NOT NULL DEFAULT now()
)

analysis_results (
    id            uuid PK,
    experiment_id uuid REFERENCES experiments(id) ON DELETE CASCADE,
    dataset_id    uuid REFERENCES datasets(id),
    results       jsonb NOT NULL,                 -- бывший results.json целиком
    report_path   text NOT NULL,                  -- HTML-отчет на volume
    created_by    uuid REFERENCES users(id),
    created_at    timestamptz NOT NULL DEFAULT now()
)

audit_log (
    id            bigserial PK,
    ts            timestamptz NOT NULL DEFAULT now(),
    user_id       uuid NULL REFERENCES users(id),
    user_email    text NULL,                      -- денормализовано, чтобы лог жил после удаления юзера
    action        text NOT NULL,                  -- experiment.create, experiment.status_change, user.create, auth.login, auth.login_failed, dataset.upload, analysis.run, validation.run, export.download ...
    object_type   text NULL, object_id text NULL, object_name text NULL,
    details       jsonb NULL                      -- {"from":"designed","to":"running"} и т.п.
)
```

Принцип разделения БД/файлы: **вся структурированная информация — в Postgres** (включая выборки-assignments — они и есть «тестовые и контрольные данные» экспериментов); **тяжелые бинарники** (загруженные исходные CSV/parquet как есть, HTML-отчеты) — на volume `/data`, в БД лежат метаданные, путь и sha256. Так `docker compose down` без удаления volumes ничего не теряет, а бэкап = pg_dump + tar /data.

Изоляция экспериментов (isolation) переезжает с чтения parquet-файлов всех экспериментов на один SQL-запрос:
`SELECT DISTINCT unit_id FROM assignments a JOIN experiments e ON ... WHERE e.status IN ('designed','running')` — это быстрее и проще текущей реализации. registry.json и filelock упраздняются: их роль выполняет БД.

Выгрузка выборок (кнопки control.csv/treatment.csv/ZIP) — генерируется из таблицы assignments на лету, поведение UI не меняется.

## 6. Логирование

Два независимых контура:

1. **Технические логи приложения** — stdout контейнера (докер-стандарт, смотреть `docker compose logs -f app`). Формат по умолчанию JSON (structlog или logging + python-json-logger): `{"ts", "level", "logger", "msg", "user", "experiment", "duration_ms", ...}`. ABKIT_LOG_FORMAT=text — человекочитаемый формат для отладки. Уровни: INFO — действия и тайминги ключевых операций (design, analyze, simulate: старт/финиш/длительность/размеры данных), WARNING — SRM-провалы, деградации, ERROR — исключения со stacktrace. В логи не должны попадать пароли, токены, сырые пользовательские данные.
2. **Аудит-лог действий** — таблица audit_log (раздел 5). Пишется на уровне сервисных функций (не UI), чтобы CLI-действия тоже логировались. В UI: вкладка «История» внутри карточки эксперимента (события этого эксперимента, видна всем ролям) и страница «Аудит» у Admin (все события, фильтры по пользователю/действию/дате, пагинация).

## 7. Файлы Docker

### 7.1 docker/Dockerfile (multi-stage)

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir build && pip wheel --no-deps --wheel-dir /wheels .

FROM python:3.12-slim
RUN useradd -m -u 1000 abkit && apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /wheels /wheels
COPY . .
RUN pip install --no-cache-dir /wheels/* -r requirements.txt
USER abkit
EXPOSE 8501
ENTRYPOINT ["/app/docker/entrypoint.sh"]
```

Требования: не-root пользователь; итоговый образ < 1.5 GB; тег версии из git (`abkit:1.4.0`), плюс `abkit:latest`.

### 7.2 docker/entrypoint.sh

Последовательность старта app-контейнера:
1. Ждать доступности Postgres (цикл pg_isready, таймаут 60 с).
2. `alembic upgrade head` — миграции применяются автоматически.
3. Если таблица users пуста и заданы env `ABKIT_ADMIN_EMAIL`/`ABKIT_ADMIN_PASSWORD` — создать первого админа (bootstrap для полностью автоматического деплоя).
4. `exec streamlit run app.py --server.port 8501 --server.headless true --server.maxUploadSize $ABKIT_MAX_UPLOAD_MB`.

### 7.3 docker-compose.yml + nginx

- nginx: проксирование на app:8501, `proxy_set_header Upgrade/Connection` (websocket обязателен для Streamlit), `client_max_body_size` = ABKIT_MAX_UPLOAD_MB, gzip для text/html/json.
- Опционально закомментированный блок TLS (сертификаты кладутся в ./docker/certs) — по умолчанию HTTP, TLS включается раскомментированием.

### 7.4 Прочее

`.dockerignore` (venv, .git, tests, __pycache__, *.md кроме README), `docker/README.md` — раздел «Развертывание» (см. 10).

## 8. Изменения в коде abkit

1. **storage.py → abkit/db/**: репозитории ExperimentRepo, AssignmentRepo, DatasetRepo, ResultRepo, UserRepo, AuditRepo (SQLAlchemy). Файловый storage.py сохраняется как реализация для локального режима без докера (режим "lite": ABKIT_MODE=file — текущее поведение, ABKIT_MODE=db — серверное). Интерфейс общий (Protocol), выбор по env.
2. **abkit/auth/**: модели, хеширование, сессии, guard-функции `require_login()`, `require_role("editor")`, `require_owner_or_admin(exp)`. Вызовы guard'ов — в начале каждого табa и перед каждой мутацией.
3. **app.py**: страница логина; скрытие/дизейбл кнопок по роли (Viewer не видит кнопок Design/Analyze/смены статусов; Editor не видит админку); текущий пользователь и logout в сайдбаре.
4. **abkit/jobs.py**: обертка запуска design/analyze/simulate с записью в audit_log и логированием таймингов — единая точка, из UI и CLI.
5. **Загрузка файлов**: каждый upload сохраняется на /data + запись в datasets (sha256, kind). Analyze связывает результат с dataset_id — полная трассируемость «какие данные породили этот отчет».
6. Обновить DESIGN.md: раздел 6 (хранение) — пометить файловый режим как lite-вариант, добавить ссылку на DOCKER.md.

## 9. Миграция существующих данных

CLI-команда `abkit-admin import-legacy --dir /import`:
- читает старую папку экспериментов (registry.json, config.yaml, assignments.parquet, results.json, report.html);
- создает записи experiments/assignments/analysis_results, файлы копирует на /data;
- владельцем назначает указанного `--owner admin@co.com`;
- идемпотентна (повторный запуск не дублирует — сверка по имени эксперимента);
- в docker-compose закомментированный volume-проброс `./legacy_experiments:/import:ro`.

## 10. Развертывание (документация для docker/README.md)

Сценарий, который должен работать дословно:

```bash
git clone <repo> && cd abkit
cp .env.example .env
# отредактировать .env: ABKIT_SECRET_KEY, POSTGRES_PASSWORD
docker compose up -d
docker compose exec app abkit-admin create-admin --email admin@co.com
# открыть http://<host>:8080, войти, завести пользователей во вкладке Admin
```

Также описать: обновление версии, бэкап (`docker compose exec postgres pg_dump -U abkit abkit > backup.sql` + `tar -czf data.tgz` volume), восстановление, просмотр логов, смена порта.

## 11. Безопасность (чек-лист)

- Пароли: только argon2id-хеши; временные пароли печатаются один раз; must_change_password при первом входе.
- Cookie: HttpOnly, SameSite=Lax; Secure — при включенном TLS.
- Rate-limit логина (раздел 4.2) хранится в БД.
- Postgres не публикуется наружу; пароль БД только из env.
- Секреты не логируются; .env в .gitignore.
- Контейнер app работает от не-root.
- Загружаемые файлы: проверка расширения и парсинга (только csv/parquet), лимит размера, содержимое никогда не исполняется.

## 12. План реализации по этапам

Каждый этап заканчивается зелеными тестами; статистическое ядро (abkit/design, abkit/analysis, abkit/validation) НЕ трогать — все 260+ существующих тестов должны оставаться зелеными на каждом этапе.

**Этап D1 — БД-слой.** SQLAlchemy-модели, Alembic, репозитории, ABKIT_MODE=db|file, перевод изоляции на SQL. Готовность: интеграционные тесты репозиториев на testcontainers-postgres (или docker-compose для CI); design→analyze проходит end-to-end в режиме db; режим file работает как раньше.

**Этап D2 — auth и роли.** users, сессии, логин-страница, guard'ы, вкладка Admin, CLI abkit-admin. Готовность: матрица прав из 4.1 покрыта тестами (Viewer не может вызвать мутацию даже прямым вызовом сервисной функции — проверка не только в UI); rate-limit работает; AppTest-сценарий логин→design→logout.

**Этап D3 — логирование и аудит.** structlog, audit_log во всех мутациях, вкладки «История» и «Аудит». Готовность: тест «каждая мутирующая сервисная функция пишет audit-запись» (параметризованный список), логи в JSON валидны.

**Этап D4 — Docker.** Dockerfile, entrypoint, compose, nginx, healthchecks, .env.example, bootstrap-админ. Готовность: на чистой машине сценарий из раздела 10 проходит дословно; `docker compose down && up` не теряет данные; e2e-смоук внутри контейнера (создать эксперимент demo, проанализировать, скачать CSV).

**Этап D5 — миграция и документация.** import-legacy, docker/README.md, обновление DESIGN.md. Готовность: импорт реальной папки из текущей установки пользователя проходит, эксперименты видны в UI со статусами и отчетами.

## 13. Явно вне скоупа этой версии

SSO/OAuth/LDAP (заложить интерфейс AuthProvider, реализовать только PasswordAuth); Celery/Redis и фоновые воркеры; Kubernetes/Helm; почтовые уведомления; мультитенантность; S3-хранилище (интерфейс DatasetStorage с единственной реализацией LocalVolume).
