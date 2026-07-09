# syntax=docker/dockerfile:1
# Мультистейдж-сборка (DOCKER.md §7.1). Тег версии (abkit:1.4.0 + abkit:latest)
# проставляется снаружи при `docker build -t ...` (см. .github/workflows/ci.yml,
# этап D6) — сюда попадает только как OCI-лейбл для трассируемости образа.
#
# Образ backend-сервиса (FastAPI/uvicorn, FRONTEND.md) — интерфейс React
# (сервис frontend в docker-compose.yml, отдельный образ frontend/Dockerfile).
# Streamlit-интерфейс (app.py) удален на этапе R8 FRONTEND.md.

FROM python:3.12-slim AS builder
WORKDIR /app

# Сначала только pyproject.toml + пакет abkit — чтобы этот слой (самый долгий,
# тянет все зависимости) кэшировался, пока не меняется код/зависимости.
COPY pyproject.toml ./
COPY abkit ./abkit
# [db-connectors]: clickhouse-connect + pymssql (Database Connections
# feature) — pymssql ships prebuilt manylinux wheels (bundled FreeTDS), no
# system ODBC driver/EULA needed in the image, unlike pyodbc.
RUN pip install --no-cache-dir --upgrade pip \
 && pip wheel --no-cache-dir --wheel-dir /wheels ".[db-connectors]"

FROM python:3.12-slim

ARG ABKIT_VERSION=dev
LABEL org.opencontainers.image.title="abkit" \
      org.opencontainers.image.version="${ABKIT_VERSION}" \
      org.opencontainers.image.source="https://github.com/danikoptimus-hash/abkit"

RUN useradd -m -u 1000 abkit \
 && apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# cli.py/cli_admin.py — не часть пакета abkit (setuptools include=["abkit*"]),
# а top-level скрипты в /app. Консольный энтрипоинт abkit-admin (из
# pyproject.toml: abkit-admin = "cli_admin:app") — это обычный shebang-стаб в
# /usr/local/bin, он НЕ добавляет CWD в sys.path сам по себе (в отличие от
# `python cli_admin.py`) — без PYTHONPATH=/app он не найдет модуль cli_admin
# при вызове `docker compose exec backend abkit-admin ...`.
ENV PYTHONPATH=/app

# Устанавливаем все колёса (abkit + все его зависимости) одним слоем.
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-compile /wheels/*.whl \
 && rm -rf /wheels /root/.cache

# Остальной код (cli.py/cli_admin.py/backend/alembic.ini/migrations/templates) —
# он не входит в пакет abkit (setuptools include=["abkit*"]), поэтому нужен
# отдельной копией; abkit/ копируется тоже — report.py вычисляет путь к
# templates/ относительно расположения ФАЙЛА пакета (Path(__file__).parent...),
# что работает только если abkit/ и templates/ — соседи в одном дереве, как при
# локальной editable-разработке. Локальная копия в sys.path имеет приоритет
# перед версией из site-packages (тем же wheel'ом) — это осознанно и безвредно.
COPY . .
RUN mkdir -p /data/experiments \
 && chown -R abkit:abkit /app /data \
 && chmod +x /app/docker/entrypoint-backend.sh

USER abkit
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -f http://localhost:8000/api/health || exit 1

ENTRYPOINT ["/app/docker/entrypoint-backend.sh"]
