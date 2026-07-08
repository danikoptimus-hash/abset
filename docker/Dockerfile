# syntax=docker/dockerfile:1
# Мультистейдж-сборка (DOCKER.md §7.1). Тег версии (abkit:1.4.0 + abkit:latest)
# проставляется снаружи при `docker build -t ...` (см. .github/workflows/ci.yml,
# этап D6) — сюда попадает только как OCI-лейбл для трассируемости образа.
#
# Один образ, два сервиса (FRONTEND.md §6): docker-compose.yml запускает его
# и как `backend` (entrypoint-backend.sh, uvicorn — теперь дефолтная роль),
# и как `legacy` (entrypoint-legacy.sh, Streamlit, временно до R8) — они
# делят одни и те же Python-зависимости (fastapi/uvicorn уже в основных
# dependencies), поэтому не нужен отдельный образ ради разных команд запуска.

FROM python:3.12-slim AS builder
WORKDIR /app

# Сначала только pyproject.toml + пакет abkit — чтобы этот слой (самый долгий,
# тянет все зависимости) кэшировался, пока не меняется код/зависимости.
COPY pyproject.toml ./
COPY abkit ./abkit
RUN pip install --no-cache-dir --upgrade pip \
 && pip wheel --no-cache-dir --wheel-dir /wheels .

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

# cli.py/cli_admin.py/app.py — не часть пакета abkit (setuptools
# include=["abkit*"]), а top-level скрипты в /app. Консольный энтрипоинт
# abkit-admin (из pyproject.toml: abkit-admin = "cli_admin:app") — это
# обычный shebang-стаб в /usr/local/bin, он НЕ добавляет CWD в sys.path сам по
# себе (в отличие от `python cli_admin.py`) — без PYTHONPATH=/app он не найдет
# модуль cli_admin при вызове `docker compose exec app abkit-admin ...`.
ENV PYTHONPATH=/app

# Устанавливаем все колёса (abkit + все его зависимости) одним слоем.
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-compile /wheels/*.whl \
 && rm -rf /wheels /root/.cache

# Остальной код (app.py/cli.py/cli_admin.py/alembic.ini/migrations/templates) —
# он не входит в пакет abkit (setuptools include=["abkit*"]), поэтому нужен
# отдельной копией; abkit/ копируется тоже — report.py вычисляет путь к
# templates/ относительно расположения ФАЙЛА пакета (Path(__file__).parent...),
# что работает только если abkit/ и templates/ — соседи в одном дереве, как при
# локальной editable-разработке. Локальная копия в sys.path имеет приоритет
# перед версией из site-packages (тем же wheel'ом) — это осознанно и безвредно.
COPY . .
RUN mkdir -p /data/experiments \
 && chown -R abkit:abkit /app /data \
 && chmod +x /app/docker/entrypoint-backend.sh /app/docker/entrypoint-legacy.sh

USER abkit
# 8000 — backend (uvicorn), 8501 — legacy (Streamlit); один и тот же образ
# слушает разный порт в зависимости от того, каким entrypoint его запускают
# (см. docker-compose.yml). HEALTHCHECK ниже — дефолт для backend (новая
# основная роль образа); сервис legacy переопределяет healthcheck: в compose.
EXPOSE 8000 8501

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -f http://localhost:8000/api/health || exit 1

ENTRYPOINT ["/app/docker/entrypoint-backend.sh"]
