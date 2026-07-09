#!/bin/bash
# Старт backend-контейнера (FastAPI/uvicorn, FRONTEND.md §6): применяет
# миграции и делает bootstrap-админа перед запуском uvicorn.
set -euo pipefail

echo "[entrypoint-backend] Waiting for Postgres..."
python -m abkit.db.wait "${ABKIT_DB_WAIT_TIMEOUT:-60}"

echo "[entrypoint-backend] Applying migrations (alembic upgrade head)..."
alembic upgrade head

if [ -n "${ABKIT_ADMIN_EMAIL:-}" ] && [ -n "${ABKIT_ADMIN_PASSWORD:-}" ]; then
    echo "[entrypoint-backend] Checking bootstrap admin..."
    python - <<'PYEOF'
import os
from abkit.auth.service import admin_create_user
from abkit.db.repositories import UserRepo

if UserRepo().count() == 0:
    email = os.environ["ABKIT_ADMIN_EMAIL"]
    # ABKIT_ADMIN_NAME остается одной строкой в .env (не ломаем формат для
    # существующих деплоев) — делим тем же правилом, что и миграция 0003
    # (первое слово -> first_name, остальное -> last_name).
    admin_name = os.environ.get("ABKIT_ADMIN_NAME", "Admin")
    first_name, _, last_name = admin_name.partition(" ")
    admin_create_user(
        None,
        email=email,
        first_name=first_name,
        last_name=last_name,
        role="admin",
        password=os.environ["ABKIT_ADMIN_PASSWORD"],
    )
    print(f"[entrypoint-backend] Bootstrap admin created: {email}")
else:
    print("[entrypoint-backend] Users already exist — bootstrap skipped.")
PYEOF
fi

echo "[entrypoint-backend] Starting FastAPI backend (uvicorn)..."
exec uvicorn backend.main:app --host 0.0.0.0 --port 8000
