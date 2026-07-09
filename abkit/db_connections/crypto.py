"""Шифрование паролей внешних БД (DB1, CLAUDE.md).

Ключ Fernet выводится из ABKIT_SECRET_KEY (SHA-256 -> urlsafe-base64), а не
берется из отдельной ABKIT_DB_ENCRYPTION_KEY — ABKIT_SECRET_KEY и так
обязателен в серверном режиме (abkit/auth/tokens.py::get_secret_key, fail-
fast на старте), заводить под эту фичу еще один обязательный секрет было бы
лишним операционным шагом без выигрыша в безопасности (оба хранятся тем же
способом — переменная окружения процесса). Задокументированный компромисс
(.env.example, DOCKER.md): ротация ABKIT_SECRET_KEY делает уже сохраненные
пароли подключений нерасшифровываемыми — перед ротацией нужно пересоздать
подключения или временно сохранить старый ключ отдельно для миграции.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from abkit.auth.tokens import get_secret_key


class DecryptionError(Exception):
    """Пароль не расшифровывается текущим ABKIT_SECRET_KEY (ключ сменился)."""


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_secret_key().encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_password(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_password(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise DecryptionError(
            "Could not decrypt the stored password — ABKIT_SECRET_KEY may have changed "
            "since this connection was created"
        ) from e
