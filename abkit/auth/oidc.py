"""OIDC (Keycloak) — протокольная часть SSO: конфиг, discovery, PKCE, проверка
ID-токена, резолв роли из групп.

Здесь НЕТ ни БД, ни HTTP-слоя приложения — только то, что можно проверить
юнит-тестами без Postgres и без поднятого Keycloak (провижининг пользователя
живет в abkit/auth/service.py::oidc_login, эндпоинты — в
backend/routers/auth.py). Тот же раздел ответственности, что у
abkit/exchange.py (чистый формат) против abkit/jobs.py (оркестрация).

Ключевое архитектурное решение: **токены Keycloak НЕ используются как токены
доступа к нашему API**. Успешный callback выпускает ОБЫЧНУЮ сессию ABSet
(abkit/auth/tokens.py) — ровно ту же, что и вход по паролю. Отсюда следует всё
остальное:

- нет refresh-token-машинерии, нет хранения access/refresh Keycloak,
  нет интроспекции на каждый запрос;
- временем жизни доступа управляет НАША сессия
  (ABKIT_SESSION_LIFETIME_HOURS);
- уволенный/заблокированный в AD сотрудник теряет доступ на СЛЕДУЮЩЕМ входе
  (Keycloak его не пустит) либо по истечении текущей сессии — то есть без
  каких-либо действий с нашей стороны, чего и требует ТЗ. Это осознанный
  компромисс: мгновенный отзыв потребовал бы либо backchannel logout, либо
  проверки в IdP на каждый запрос — и то, и другое несоразмерно задаче.

Всё выключено по умолчанию: без ABKIT_OIDC_ENABLED=true ни один эндпоинт
ничего не делает, и вход по паролю работает ровно как раньше.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
import jwt
from jwt import PyJWKClient

from abkit.logging_config import get_logger

log = get_logger(__name__)

# Допуск на расхождение часов между нами и IdP при проверке exp/iat/nbf.
# 60с — стандартная величина "small clock skew tolerance": закрывает обычный
# дрейф NTP-синхронизированных машин, но не превращает истекший токен в
# бессрочный.
CLOCK_SKEW_SECONDS = 60

# Сколько живет закэшированный документ discovery. Endpoints реалма меняются
# исключительно редко (переезд/пересоздание реалма), а ходить за
# .well-known на каждый логин — лишняя точка отказа.
DISCOVERY_CACHE_SECONDS = 15 * 60

# Таймауты на любой сетевой вызов к IdP. Без них зависший Keycloak вешает
# воркер: и discovery, и обмен кода — синхронные вызовы внутри обработки
# запроса пользователя.
HTTP_TIMEOUT_SECONDS = 10.0

_ROLE_PRECEDENCE = {"viewer": 0, "editor": 1, "admin": 2}
_WILDCARD = "*"


class OidcError(Exception):
    """Ошибка конфигурации/протокола OIDC. Роутер превращает ее в человеческую
    страницу (не в JSON и не в пустой экран) — см. ТЗ п.3."""


class OidcDisabledError(OidcError):
    """Эндпоинты SSO дернули, когда ABKIT_OIDC_ENABLED не выставлен."""


@dataclass(frozen=True)
class OidcSettings:
    """Снимок ABKIT_OIDC_* на момент запроса.

    Читается из окружения каждый раз (load_settings), а не кэшируется в
    модуле: тесты меняют env через monkeypatch, и залипший синглтон делал бы
    половину сценариев непроверяемыми.
    """

    enabled: bool
    issuer: str
    client_id: str
    client_secret: str
    role_claim: str
    role_map: dict[str, str]
    default_role: str | None
    public_url: str
    logout_upstream: bool
    internal_base_url: str = ""
    """Необязательный ВНУТРЕННИЙ адрес того же IdP для вызовов server-to-server
    (discovery, обмен кода, JWKS). Пусто — ходим по тем же URL, что и браузер;
    в проде так и есть, и трогать эту ручку не нужно.

    Зачем она вообще: `iss` в токене — ОДНА строка, и она обязана совпадать с
    настроенным issuer. Но в dev браузер видит Keycloak как localhost:8081, а
    backend внутри контейнера по этому адресу увидит СЕБЯ. Разводить их,
    подменяя `localhost` на host-gateway в /etc/hosts контейнера, нельзя —
    это ломает собственный loopback (healthcheck backend'а ходит на
    http://localhost:8000). Поэтому проверка `iss` остается на публичном
    issuer, а серверные запросы переписываются на этот внутренний origin —
    ровно то, что делают в любом деплое за NAT."""

    @property
    def redirect_uri(self) -> str:
        """ВСЕГДА собирается из ABKIT_PUBLIC_URL и никогда из заголовков
        запроса (Host/X-Forwarded-Host). Заголовок подконтролен клиенту:
        если бы redirect_uri строился из него, злоумышленник мог бы получить
        от Keycloak редирект с кодом на свой домен (при слабой проверке
        redirect_uri на стороне IdP) — классический covert redirect. Плюс
        значение обязано побайтово совпадать с тем, что зарегистрировано у
        IdP, а Host за парой прокси совпадать не обязан."""
        return f"{self.public_url.rstrip('/')}/api/v1/auth/oidc/callback"

    @property
    def post_logout_redirect_uri(self) -> str:
        return f"{self.public_url.rstrip('/')}/login"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _parse_role_map(raw: str | None) -> dict[str, str]:
    """ABKIT_OIDC_ROLE_MAP — JSON {группа: роль}. Битый JSON — ошибка
    конфигурации, а не "поехали с пустой картой": молча пустая карта означала
    бы, что все пользователи проваливаются в default_role, то есть тихую
    раздачу не тех прав."""
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise OidcError(f"ABKIT_OIDC_ROLE_MAP is not valid JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise OidcError("ABKIT_OIDC_ROLE_MAP must be a JSON object {group: role}")
    out: dict[str, str] = {}
    for group, role in parsed.items():
        if role not in _ROLE_PRECEDENCE:
            raise OidcError(
                f"ABKIT_OIDC_ROLE_MAP maps group '{group}' to unknown role '{role}' "
                f"(expected one of: viewer, editor, admin)"
            )
        out[str(group)] = role
    return out


def load_settings() -> OidcSettings:
    enabled = _env_bool("ABKIT_OIDC_ENABLED", False)
    default_role_raw = os.environ.get("ABKIT_OIDC_DEFAULT_ROLE", "viewer")
    # Пустая строка — ЗНАЧАЩЕЕ значение ("нет дефолтной роли, отклонять тех,
    # кто не попал ни в одну группу"), а не "не задано". Прямо из ТЗ п.1.
    default_role = default_role_raw.strip() or None
    if default_role is not None and default_role not in _ROLE_PRECEDENCE:
        raise OidcError(
            f"ABKIT_OIDC_DEFAULT_ROLE='{default_role}' is not a known role "
            f"(expected one of: viewer, editor, admin, or empty to reject unmapped users)"
        )
    settings = OidcSettings(
        enabled=enabled,
        issuer=(os.environ.get("ABKIT_OIDC_ISSUER") or "").rstrip("/"),
        client_id=os.environ.get("ABKIT_OIDC_CLIENT_ID") or "",
        client_secret=os.environ.get("ABKIT_OIDC_CLIENT_SECRET") or "",
        role_claim=os.environ.get("ABKIT_OIDC_ROLE_CLAIM") or "groups",
        role_map=_parse_role_map(os.environ.get("ABKIT_OIDC_ROLE_MAP")),
        default_role=default_role,
        public_url=os.environ.get("ABKIT_PUBLIC_URL") or "",
        logout_upstream=_env_bool("ABKIT_OIDC_LOGOUT_UPSTREAM", False),
        internal_base_url=(os.environ.get("ABKIT_OIDC_INTERNAL_BASE_URL") or "").rstrip("/"),
    )
    if enabled:
        missing = [
            name
            for name, value in (
                ("ABKIT_OIDC_ISSUER", settings.issuer),
                ("ABKIT_OIDC_CLIENT_ID", settings.client_id),
                ("ABKIT_OIDC_CLIENT_SECRET", settings.client_secret),
                ("ABKIT_PUBLIC_URL", settings.public_url),
            )
            if not value
        ]
        if missing:
            raise OidcError(
                "OIDC is enabled but required settings are missing: " + ", ".join(missing)
            )
    return settings


# --------------------------------------------------------------------------
# Роли из групп
# --------------------------------------------------------------------------


def normalize_groups(claims: dict[str, Any], role_claim: str) -> list[str]:
    """Значение claim'а групп -> список строк.

    Keycloak отдает группы как ["/abset-admins"] (с ведущим слэшем — это путь
    группы), а привычная запись в ABKIT_OIDC_ROLE_MAP — "abset-admins".
    Ведущие слэши срезаются, чтобы админу не приходилось угадывать формат;
    вложенные группы ("/dept/abset-admins") сопоставляются и целиком, и по
    последнему сегменту.

    Одиночная строка вместо списка тоже принимается: провайдеры, у которых
    группа одна, нередко отдают скаляр.
    """
    raw = claims.get(role_claim)
    if raw is None:
        return []
    values = [raw] if isinstance(raw, str) else list(raw)
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        trimmed = text.lstrip("/")
        if trimmed and trimmed not in out:
            out.append(trimmed)
        # Вложенная группа: сопоставляем и по последнему сегменту, чтобы
        # "/departments/analytics/abset-admins" попадала в "abset-admins".
        tail = trimmed.rsplit("/", 1)[-1]
        if tail and tail not in out:
            out.append(tail)
    return out


def resolve_role(groups: list[str], settings: OidcSettings) -> str | None:
    """Роль ABSet по группам пользователя, либо None — "отклонить вход".

    Правила (ТЗ п.2), в этом порядке:
    1. Побеждает САМАЯ ВЫСОКАЯ роль среди совпавших групп (admin > editor >
       viewer) — пользователь в abset-admins И abset-editors получает admin.
       Иначе результат зависел бы от порядка ключей в JSON, то есть был бы
       непредсказуемым.
    2. "*" в карте — общий fallback для аутентифицированных, но не попавших
       ни в одну явную группу. Участвует в том же выборе максимума, что и
       остальные: явная группа viewer + "*"->editor даст editor, потому что
       "*" — такое же правило, а не "последнее слово".
    3. Нет совпадений — ABKIT_OIDC_DEFAULT_ROLE; он пуст -> None (отклонить).
    """
    matched = [settings.role_map[g] for g in groups if g in settings.role_map]
    if _WILDCARD in settings.role_map:
        matched.append(settings.role_map[_WILDCARD])
    if matched:
        return max(matched, key=lambda role: _ROLE_PRECEDENCE[role])
    return settings.default_role


# --------------------------------------------------------------------------
# Discovery (.well-known) + JWKS
# --------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class _TtlCache:
    """Крошечный потокобезопасный TTL-кэш. Redis тут не нужен и не появится —
    проект принципиально однопроцессный (то же обоснование, что у
    abkit/db_connections/introspection.py)."""

    def __init__(self) -> None:
        self._data: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if entry.expires_at <= time.monotonic():
                del self._data[key]
                return None
            return entry.value

    def set(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._data[key] = _CacheEntry(value=value, expires_at=time.monotonic() + ttl)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_discovery_cache = _TtlCache()
_jwks_clients: dict[str, PyJWKClient] = {}
_jwks_lock = threading.Lock()


def clear_caches() -> None:
    """Для тестов и для смены конфигурации без рестарта."""
    _discovery_cache.clear()
    with _jwks_lock:
        _jwks_clients.clear()


def to_internal_url(settings: OidcSettings, url: str) -> str:
    """Переписывает origin публичного URL на ABKIT_OIDC_INTERNAL_BASE_URL.

    Применяется ТОЛЬКО к серверным вызовам (discovery, token, JWKS). К
    authorization_endpoint и end_session_endpoint — НИКОГДА: по ним ходит
    браузер пользователя, и внутренний адрес там был бы нерезолвимым.
    Пустая настройка (прод) — возврат как есть, ни одного отличия в поведении.
    """
    if not settings.internal_base_url:
        return url
    parsed = urlsplit(url)
    internal = urlsplit(settings.internal_base_url)
    return urlunsplit(
        (internal.scheme or parsed.scheme, internal.netloc, parsed.path, parsed.query, parsed.fragment)
    )


def discover(settings: OidcSettings) -> dict[str, Any]:
    """Метаданные провайдера из <issuer>/.well-known/openid-configuration.

    Пути эндпоинтов НИКОГДА не хардкодятся (ТЗ п.1): у Keycloak они зависят от
    версии и от того, включен ли legacy-префикс /auth — угаданный путь
    сломается на первом же апгрейде IdP.
    """
    cached = _discovery_cache.get(settings.issuer)
    if cached is not None:
        return cached
    url = to_internal_url(settings, f"{settings.issuer}/.well-known/openid-configuration")
    try:
        response = httpx.get(url, timeout=HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        document = response.json()
    except Exception as e:
        raise OidcError(
            f"Could not reach the identity provider's discovery document at {url}: {e}"
        ) from e
    for required in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not document.get(required):
            raise OidcError(f"Discovery document at {url} has no '{required}'")
    # Issuer из документа обязан совпадать с настроенным: несовпадение значит
    # либо опечатку в конфиге, либо что нас увели на чужой реалм.
    document_issuer = str(document.get("issuer", "")).rstrip("/")
    if document_issuer and document_issuer != settings.issuer:
        raise OidcError(
            f"Discovery issuer mismatch: configured '{settings.issuer}', "
            f"document says '{document_issuer}'"
        )
    _discovery_cache.set(settings.issuer, document, DISCOVERY_CACHE_SECONDS)
    return document


def _jwk_client(jwks_uri: str) -> PyJWKClient:
    """Один PyJWKClient на JWKS-URI, переиспользуется между запросами.

    Кэш ключей держит сам PyJWKClient (cache_keys=True) — ходить за JWKS на
    каждый логин не нужно, но и намертво прибивать ключи нельзя: реалм
    переживает ротацию ключей, и клиент должен уметь подтянуть новый kid.
    """
    with _jwks_lock:
        client = _jwks_clients.get(jwks_uri)
        if client is None:
            client = PyJWKClient(jwks_uri, cache_keys=True, lifespan=DISCOVERY_CACHE_SECONDS)
            _jwks_clients[jwks_uri] = client
        return client


# --------------------------------------------------------------------------
# PKCE + authorization request
# --------------------------------------------------------------------------


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str
    state: str
    nonce: str
    code_verifier: str


def generate_pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) по S256.

    PKCE нужен даже конфиденциальному клиенту: он привязывает обмен кода к
    ТОМУ ЖЕ браузеру, что начал вход, поэтому перехваченный код (логи прокси,
    Referer, история) без verifier'а бесполезен.
    """
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorization_request(settings: OidcSettings) -> AuthorizationRequest:
    document = discover(settings)
    state = _b64url(secrets.token_bytes(16))
    nonce = _b64url(secrets.token_bytes(16))
    verifier, challenge = generate_pkce_pair()
    params = {
        "response_type": "code",
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        # email нужен для сопоставления пользователя, profile — для имени;
        # группы Keycloak кладет в ID-токен маппером, отдельного scope для
        # этого в общем случае не требуется.
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = f"{document['authorization_endpoint']}?{urlencode(params)}"
    return AuthorizationRequest(url=url, state=state, nonce=nonce, code_verifier=verifier)


def build_logout_url(settings: OidcSettings, id_token: str | None = None) -> str | None:
    """RP-initiated logout (ТЗ п.1) — только при ABKIT_OIDC_LOGOUT_UPSTREAM=true.

    None, если провайдер не публикует end_session_endpoint: тогда выходим
    только из своей сессии, что и есть поведение по умолчанию.
    """
    if not settings.logout_upstream:
        return None
    try:
        document = discover(settings)
    except OidcError:
        # Выход не должен падать из-за недоступного IdP — свою сессию мы уже
        # погасили, а это лишь необязательный «выйти и там тоже».
        return None
    endpoint = document.get("end_session_endpoint")
    if not endpoint:
        return None
    params: dict[str, str] = {
        "post_logout_redirect_uri": settings.post_logout_redirect_uri,
        "client_id": settings.client_id,
    }
    if id_token:
        params["id_token_hint"] = id_token
    return f"{endpoint}?{urlencode(params)}"


# --------------------------------------------------------------------------
# Обмен кода и проверка ID-токена
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OidcIdentity:
    """Итог успешной проверки ID-токена — всё, что нужно провижинингу."""

    subject: str
    email: str
    email_verified: bool
    first_name: str
    last_name: str
    groups: list[str] = field(default_factory=list)
    id_token: str = ""


def exchange_code(settings: OidcSettings, *, code: str, code_verifier: str) -> dict[str, Any]:
    document = discover(settings)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.redirect_uri,
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "code_verifier": code_verifier,
    }
    try:
        response = httpx.post(
            to_internal_url(settings, document["token_endpoint"]),
            data=data,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except Exception as e:
        raise OidcError(f"Could not reach the identity provider's token endpoint: {e}") from e
    if response.status_code != 200:
        # Тело ответа НЕ показываем пользователю (в нем бывает client_secret в
        # эхе ошибки) — только в лог.
        log.error(
            "oidc.token_exchange_failed",
            status=response.status_code,
            body=response.text[:500],
        )
        raise OidcError("The identity provider rejected the login attempt (token exchange failed)")
    payload = response.json()
    if not payload.get("id_token"):
        raise OidcError("The identity provider returned no ID token")
    return payload


def verify_id_token(settings: OidcSettings, id_token: str, *, expected_nonce: str) -> OidcIdentity:
    """Проверяет подпись и содержимое ID-токена и возвращает личность.

    Подпись — по JWKS реалма (асимметрично, RS256 и родня); симметричные
    алгоритмы и alg=none не принимаются в принципе, потому что список
    algorithms задается нами, а не берется из заголовка токена.
    """
    document = discover(settings)
    try:
        jwks_uri = to_internal_url(settings, document["jwks_uri"])
        signing_key = _jwk_client(jwks_uri).get_signing_key_from_jwt(id_token)
    except Exception as e:
        raise OidcError(f"Could not verify the ID token signature: {e}") from e
    try:
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256"],
            audience=settings.client_id,
            issuer=settings.issuer,
            leeway=CLOCK_SKEW_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.InvalidTokenError as e:
        raise OidcError(f"The ID token is not valid: {e}") from e

    # nonce привязывает токен к ИМЕННО ЭТОМУ запросу авторизации: без него
    # валидный, но перехваченный/переигранный ID-токен прошел бы проверку.
    token_nonce = claims.get("nonce")
    if not token_nonce or not secrets.compare_digest(str(token_nonce), expected_nonce):
        raise OidcError("The ID token does not match this login attempt (nonce mismatch)")

    email = (claims.get("email") or "").strip()
    if not email:
        raise OidcError(
            "The identity provider did not return an email address — ABSet matches "
            "accounts by email, so the login cannot proceed"
        )
    # email_verified обязателен (ТЗ п.2, "match users by verified email"):
    # непроверенный адрес позволил бы завести чужой аккаунт, просто вписав
    # чужой email у себя в профиле IdP.
    email_verified = bool(claims.get("email_verified", False))
    if not email_verified:
        raise OidcError(
            f"The email address '{email}' is not marked as verified in the identity "
            "provider — ABSet only accepts verified emails"
        )

    return OidcIdentity(
        subject=str(claims.get("sub", "")),
        email=email,
        email_verified=True,
        first_name=(claims.get("given_name") or "").strip(),
        last_name=(claims.get("family_name") or "").strip(),
        groups=normalize_groups(claims, settings.role_claim),
        id_token=id_token,
    )
