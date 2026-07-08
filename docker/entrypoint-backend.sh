#!/bin/bash
# Старт backend-контейнера (FastAPI/uvicorn, FRONTEND.md §6). Единственный
# entrypoint, который применяет миграции и делает bootstrap-админа — legacy
# (Streamlit) больше этого не делает (см. entrypoint-legacy.sh), чтобы оба
# сервиса не гонялись за одной и той же миграцией/вставкой первого юзера при
# параллельном старте; docker-compose.yml гарантирует порядок через
# `legacy: depends_on: backend: condition: service_healthy`.
set -euo pipefail

echo "[entrypoint-backend] Ждём доступности Postgres..."
python -m abkit.db.wait "${ABKIT_DB_WAIT_TIMEOUT:-60}"

echo "[entrypoint-backend] Применяем миграции (alembic upgrade head)..."
alembic upgrade head

if [ -n "${ABKIT_ADMIN_EMAIL:-}" ] && [ -n "${ABKIT_ADMIN_PASSWORD:-}" ]; then
    echo "[entrypoint-backend] Проверяем bootstrap-администратора..."
    python - <<'PYEOF'
import os
from abkit.auth.service import admin_create_user
from abkit.db.repositories import UserRepo

if UserRepo().count() == 0:
    email = os.environ["ABKIT_ADMIN_EMAIL"]
    admin_create_user(
        None,
        email=email,
        name=os.environ.get("ABKIT_ADMIN_NAME", "Admin"),
        role="admin",
        password=os.environ["ABKIT_ADMIN_PASSWORD"],
    )
    print(f"[entrypoint-backend] Bootstrap-администратор создан: {email}")
else:
    print("[entrypoint-backend] Пользователи уже есть — bootstrap пропущен.")
PYEOF
fi

echo "[entrypoint-backend] Запускаем FastAPI backend (uvicorn)..."
exec uvicorn backend.main:app --host 0.0.0.0 --port 8000
