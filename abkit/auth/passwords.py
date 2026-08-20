"""Хеширование паролей (DOCKER.md §4.2): argon2id — основной алгоритм для
новых паролей; bcrypt verify — fallback для чтения уже существующих
bcrypt-хешей (например, при миграции пользователей из другой системы)."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError

_hasher = PasswordHasher()

# Значение password_hash для аккаунтов, у которых пароля НЕТ (заведены через
# Keycloak SSO). Не хеш, а заведомо не-хеш: verify_password ниже возвращает
# False для любой строки без известного префикса ($argon2/$2a$/$2b$/$2y$),
# поэтому подобрать к нему пароль невозможно в принципе — не потому, что он
# «сложный», а потому, что ветка сравнения для него не существует.
#
# Почему сентинел, а не NULL: колонка password_hash остается NOT NULL, чтобы
# откат на предыдущий тег приложения не приводил к падению (см. docstring
# миграции 0024). Старый код на таком значении честно скажет «Invalid email or
# password», а не упадет на None.
NO_PASSWORD_SENTINEL = "!no-password:oidc"


def hash_password(plain: str) -> str:
    """Возвращает argon2id-хеш. Никогда не хранить/логировать сам plain."""
    return _hasher.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    """True, если plain соответствует хешу. Понимает и argon2 (основной путь),
    и bcrypt (fallback verify для унаследованных хешей).

    Любая нераспознанная строка (в т.ч. NO_PASSWORD_SENTINEL и пустое
    значение) — False: «пароля нет» никогда не должно означать «подойдет
    любой»."""
    if not password_hash:
        return False
    if password_hash.startswith("$argon2"):
        try:
            return _hasher.verify(password_hash, plain)
        except (VerifyMismatchError, InvalidHash):
            return False
    if password_hash.startswith(("$2a$", "$2b$", "$2y$")):
        import bcrypt

        try:
            return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
        except ValueError:
            return False
    return False
