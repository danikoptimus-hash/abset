#!/bin/bash
# Старт legacy-контейнера (Streamlit, временный — до R8 FRONTEND.md).
# Миграции и bootstrap-админ НЕ дублируются здесь — их делает только
# entrypoint-backend.sh; docker-compose.yml держит `depends_on: backend:
# condition: service_healthy`, так что к моменту старта Streamlit схема БД
# уже актуальна и первый админ (если задан ABKIT_ADMIN_EMAIL) уже есть.
set -euo pipefail

echo "[entrypoint-legacy] Ждём доступности Postgres..."
python -m abkit.db.wait "${ABKIT_DB_WAIT_TIMEOUT:-60}"

echo "[entrypoint-legacy] Запускаем Streamlit..."
# --server.baseUrlPath=legacy: nginx проксирует /legacy/* сюда (FRONTEND.md
# §2/§6) — без этого флага Streamlit генерирует ссылки на статику/websocket
# от корня "/", что ломается за реверс-прокси на непустом префиксе.
exec streamlit run app.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --server.baseUrlPath legacy \
    --server.maxUploadSize "${ABKIT_MAX_UPLOAD_MB:-400}"
