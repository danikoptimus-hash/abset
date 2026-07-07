#!/bin/bash
# Последовательность старта app-контейнера (DOCKER.md §7.2).
set -euo pipefail

echo "[entrypoint] Ждём доступности Postgres..."
python -m abkit.db.wait "${ABKIT_DB_WAIT_TIMEOUT:-60}"

echo "[entrypoint] Применяем миграции (alembic upgrade head)..."
alembic upgrade head

if [ -n "${ABKIT_ADMIN_EMAIL:-}" ] && [ -n "${ABKIT_ADMIN_PASSWORD:-}" ]; then
    echo "[entrypoint] Проверяем bootstrap-администратора..."
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
    print(f"[entrypoint] Bootstrap-администратор создан: {email}")
else:
    print("[entrypoint] Пользователи уже есть — bootstrap пропущен.")
PYEOF
fi

echo "[entrypoint] Запускаем Streamlit..."
exec streamlit run app.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --server.maxUploadSize "${ABKIT_MAX_UPLOAD_MB:-400}"
