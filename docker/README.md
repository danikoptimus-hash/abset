# Развертывание abkit в Docker

Полноценный командный сервис: учетки, роли, Postgres, аудит-лог. Начиная с
FRONTEND.md R7 стек состоит из пяти сервисов за одним nginx:

- **frontend** — React-UI (статика, nginx), основной интерфейс;
- **backend** — FastAPI/uvicorn, REST API + миграции + bootstrap первого админа;
- **legacy** — старый Streamlit-интерфейс, временно доступен на `/legacy/` на
  период миграции (будет удален на этапе R8, см. FRONTEND.md);
- **postgres**, **nginx** (маршрутизация `/` → frontend, `/api/*` → backend,
  `/legacy/*` → legacy).

Техническая спецификация — [DOCKER.md](../DOCKER.md) и [FRONTEND.md](../FRONTEND.md).

## Требования

- Docker Engine 24+ и Docker Compose v2 (`docker compose version`).
- Открытый порт (по умолчанию 8080) на хосте.

## Быстрый старт

```bash
git clone <repo> && cd abkit
cp .env.example .env
# отредактировать .env: как минимум ABKIT_SECRET_KEY и POSTGRES_PASSWORD
#   ABKIT_SECRET_KEY генерировать так: openssl rand -hex 32
docker compose up -d
docker compose exec backend abkit-admin create-admin --email admin@co.com
# React-UI: http://<host>:8080, войдите под admin@co.com
# старый интерфейс на период миграции: http://<host>:8080/legacy/
```

Через 1-2 минуты (сборка образов + старт Postgres) сервис доступен на
`http://<host>:8080`. Миграции БД (`alembic upgrade head`) и bootstrap первого
администратора применяются автоматически при старте контейнера `backend` —
сервис `legacy` ждет, пока `backend` не станет healthy (`depends_on`), и сам
их не повторяет.

Если задать `ABKIT_ADMIN_EMAIL`/`ABKIT_ADMIN_PASSWORD` в `.env` (закомментированы
в `.env.example` по умолчанию) — первый администратор создастся автоматически
при первом старте, шаг `abkit-admin create-admin` можно пропустить.

## Управление пользователями

Все команды — через `docker compose exec backend abkit-admin <command>`:

```bash
docker compose exec backend abkit-admin create-admin --email admin@co.com [--name "Admin"] [--password ...]
docker compose exec backend abkit-admin create-user  --email u@co.com --role editor
docker compose exec backend abkit-admin reset-password --email u@co.com
docker compose exec backend abkit-admin list-users
```

Если `--password` не передан — пароль генерируется и печатается в stdout один
раз (сохраните сразу, повторно его не показать). Пользователь получает флаг
`must_change_password` и обязан сменить пароль при первом входе (страница
«Профиль»/принудительная форма смены пароля).

То же самое можно сделать через веб-интерфейс — раздел **Admin** (виден
только роли Admin): таблица пользователей, создание, смена роли,
блокировка/разблокировка, сброс пароля. Доступно и в React-UI, и в `/legacy/`
на период миграции.

## Роли

| Право                                                          | Viewer | Editor | Admin |
|-----------------------------------------------------------------|:------:|:------:|:-----:|
| Смотреть эксперименты, отчеты, скачивать выборки                |   ✓    |   ✓    |   ✓   |
| Создавать эксперименты, запускать Analyze/Validation             |        |   ✓    |   ✓   |
| Менять статус/архивировать СВОИ эксперименты                     |        |   ✓    |   ✓   |
| Менять/архивировать ЧУЖИЕ эксперименты                            |        |        |   ✓   |
| Удалять эксперименты                                             |        |        |   ✓   |
| Управлять пользователями, смотреть общий аудит-лог                |        |        |   ✓   |

По умолчанию самостоятельная регистрация выключена (`ABKIT_ALLOW_SELF_REGISTRATION=false`)
— учетки заводит администратор, как в Apache Superset. Включение самостоятельной
регистрации выдает роль Viewer автоматически.

## Импорт данных из файлового (не-Docker) режима

Если вы уже пользовались `abkit` локально (файловый режим, без Docker) и
хотите перенести накопленные эксперименты на сервер:

```bash
# 1. Раскомментируйте в docker-compose.yml проброс volume для сервиса backend:
#      - ./legacy_experiments:/import:ro
#    и положите туда старую папку экспериментов (ту, что указана как
#    experiments_dir/ABKIT_EXPERIMENTS_DIR в старой файловой установке —
#    там должны быть registry.json и папки экспериментов).
docker compose up -d --force-recreate backend

# 2. Импортируйте, указав существующего пользователя-владельца:
docker compose exec backend abkit-admin import-legacy --dir /import --owner admin@co.com
```

Команда идемпотентна: повторный запуск не создаст дублей — уже
импортированные (по имени) эксперименты просто пропускаются, с чёткой
пометкой в выводе. Импортируются конфиг, назначения групп (assignments),
HTML-отчеты и results.json, если они есть; статус и исторические даты
(created_at/started_at/completed_at) сохраняются как в исходной установке.

## Обновление версии

```bash
git pull
docker compose build
docker compose up -d
```

Миграции БД применяются автоматически при старте `backend` — ручных действий
не требуется. Даунтайм — время пересборки образов + перезапуска контейнеров
(Postgres не перезапускается, если его образ/конфиг не менялись).

## Данные и перезапуски

Все данные (пользователи, эксперименты, assignments — в Postgres; отчеты,
`config.yaml`, тяжелые артефакты — на volume `abkit_data`) живут в именованных
Docker volumes (`abkit_pgdata`, `abkit_data`), а не в самих контейнерах.
Контейнеры можно свободно останавливать/пересоздавать — данные это переживает:

| Команда | Данные? |
|---|---|
| `docker compose restart backend legacy` / `docker compose stop && start` | целы |
| `docker compose down` (без `-v`) + `docker compose up -d` | целы (volumes не трогаются) |
| `docker compose build && docker compose up -d --force-recreate` (обновление) | целы |
| `docker compose down -v` | **удалены безвозвратно** (и Postgres, и файловые артефакты) |

**`docker compose down -v` — единственная команда из обычного набора команд
этого README, которая реально уничтожает данные.** Используйте `-v` только
осознанно (например, чтобы намеренно начать с чистого листа локально) — на
проде эта команда фактически не нужна, для нужд выше строк таблицы (рестарт,
даунтайм-обновление) volumes трогать не требуется.

Активный уже открытый браузерный таб на `/legacy/`, переживший
`--force-recreate` (WebSocket переподключается автоматически), иногда стоит
обновить (F5 / Ctrl+Shift+R) — это чисто клиентское поведение
браузера/Streamlit-фронтенда, не связано с сохранностью данных на сервере.

**Перед деплоем на новый сервер и перед крупными обновлениями** (смена
мажорной версии образа, миграции схемы БД, правки docker-compose.yml/
entrypoint-*.sh) — прогоните `docker/test_persistence.sh` как обязательный шаг
чек-листа:

```bash
bash docker/test_persistence.sh
```

Скрипт создает тестового пользователя и небольшой эксперимент с assignments,
делает `docker compose down` + `up -d`, затем `docker compose build` +
`up -d --force-recreate backend`, и после каждого шага проверяет, что
пользователь, эксперимент, assignments (число строк совпадает) и
`design_report.html` никуда не делись. Выход 0 — все ок, 1 — что-то не
пережило рестарт (сообщения `FAIL:` в выводе укажут, что именно). Сам скрипт
за собой убирает тестового пользователя (деактивирует); тестовый эксперимент
маленький и безвреден, остается в реестре.

Тот же скрипт можно запустить в CI вручную: вкладка Actions -> workflow "CI"
-> "Run workflow" (джоба `persistence-test`, `workflow_dispatch`) — она не
входит в обязательный прогон на каждый push/PR (медленная, поднимает
настоящий docker compose стек), только по запросу.

## Бэкап и восстановление

Бэкап (структурные данные + бинарные артефакты — DOCKER.md §5):

```bash
docker compose exec postgres pg_dump -U "${POSTGRES_USER:-abkit}" "${POSTGRES_DB:-abkit}" > backup.sql
docker run --rm -v abkit_abkit_data:/data -v "$(pwd)":/backup alpine \
    tar -czf /backup/data.tgz -C /data .
```

Восстановление (на новом/пустом окружении):

```bash
docker compose up -d postgres
cat backup.sql | docker compose exec -T postgres psql -U "${POSTGRES_USER:-abkit}" "${POSTGRES_DB:-abkit}"
docker run --rm -v abkit_abkit_data:/data -v "$(pwd)":/backup alpine \
    sh -c "cd /data && tar -xzf /backup/data.tgz"
docker compose up -d
```

`docker compose down` (без `-v`) НЕ удаляет volumes — данные переживают
остановку/пересоздание контейнеров. `docker compose down -v` volumes удаляет
безвозвратно — используйте только осознанно (например, чтобы начать с чистого
листа локально).

## Логи

```bash
docker compose logs -f backend    # структурированные JSON-логи (ABKIT_LOG_FORMAT=json по умолчанию)
docker compose logs -f legacy
docker compose logs -f frontend
docker compose logs -f postgres
docker compose logs -f nginx
```

`ABKIT_LOG_FORMAT=text` в `.env` — человекочитаемый формат вместо JSON, для
отладки на живую руку (действует на `backend`/`legacy`). `ABKIT_LOG_LEVEL` —
стандартные уровни Python-логирования (`DEBUG`/`INFO`/`WARNING`/`ERROR`).

## Смена порта

Отредактируйте `ABKIT_PORT` в `.env` (по умолчанию 8080) и перезапустите
`nginx`:

```bash
docker compose up -d nginx
```

## TLS (опционально)

По умолчанию сервис работает по HTTP. Чтобы включить HTTPS: положите
сертификаты в `docker/certs/` (`fullchain.pem`/`privkey.pem`), раскомментируйте
проброс `./docker/certs:/etc/nginx/certs:ro` в `docker-compose.yml` (сервис
`nginx`) и блок `listen 443 ssl; ssl_certificate ...` в
`docker/nginx.conf.template`.

## Безопасность

Чек-лист — DOCKER.md §11 (пароли только argon2id-хеши, cookie SameSite=Lax,
Postgres не публикуется наружу, секреты не логируются, `.env` в `.gitignore`,
контейнеры `backend`/`legacy` работают от не-root пользователя). `ABKIT_SECRET_KEY`
и `POSTGRES_PASSWORD` в `.env` — обязательно смените дефолтные значения перед
продакшн-развертыванием; приложение откажется стартовать, если
`ABKIT_SECRET_KEY` не задан или похож на дефолтный `change-me...`.

## Миграция со Streamlit на React-UI (временный раздел, до R8)

На период миграции (FRONTEND.md) доступны оба интерфейса на одном origin:

- React-UI — `http://<host>:8080/` (основной, активная разработка);
- Streamlit — `http://<host>:8080/legacy/` (сохранен для параллельной
  проверки паритета функций перед полным отключением).

Оба используют одну и ту же БД и один и тот же cookie-based auth (общий
origin через nginx) — сессия, начатая в одном интерфейсе, действительна и в
другом. Сервис `legacy` будет удален из стека на этапе R8 после явного
подтверждения пользователя (см. FRONTEND.md) — как только React-UI достигнет
полного паритета функций.
