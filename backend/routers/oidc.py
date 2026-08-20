"""GET /auth/oidc/login и GET /auth/oidc/callback — SSO через Keycloak.

Оба эндпоинта — НАВИГАЦИЯ БРАУЗЕРА, а не XHR: пользователь уходит на Keycloak
и возвращается редиректом. Отсюда два следствия, которых нет у остального API:

- отвечаем редиректами и HTML, а не JSON. Ошибка на callback'е обязана быть
  человеческой страницей (ТЗ п.3) — пользователь смотрит на нее в адресной
  строке, «{"error":{"code":...}}» тут неприемлемо;
- транзакция входа (state/nonce/PKCE-verifier) должна пережить уход на чужой
  домен и возврат. Она кладется в ОТДЕЛЬНУЮ подписанную короткоживущую cookie,
  а не в память процесса: так она переживает рестарт backend'а посреди входа и
  не требует общего состояния. SameSite у нее — Lax, а не Strict как у
  сессионной: Strict-cookie браузер НЕ пришлет при переходе с домена Keycloak
  обратно к нам, и вход ломался бы ровно на возврате (response_mode=query,
  то есть верхнеуровневая GET-навигация — Lax этого достаточно).

Сессия после успеха выпускается наша собственная, ровно как при входе по
паролю; токены Keycloak дальше не живут (см. abkit/auth/oidc.py).
"""

from __future__ import annotations

import html
import time
from typing import Any

import jwt
from fastapi import APIRouter, Cookie, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from abkit.auth.guards import AuthError
from abkit.auth.oidc import (
    OidcError,
    build_authorization_request,
    exchange_code,
    load_settings,
    verify_id_token,
)
from abkit.auth.tokens import get_secret_key
from abkit.logging_config import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/auth/oidc", tags=["auth"])

# Cookie с транзакцией входа. Отдельная от сессионной: у нее другой срок
# жизни (минуты), другой SameSite (Lax) и она удаляется сразу после callback'а.
TX_COOKIE_NAME = "abkit_oidc_tx"

# Сколько у пользователя есть времени на экран логина Keycloak. 10 минут —
# с запасом на ввод пароля и второй фактор, но не настолько долго, чтобы
# оставленная в браузере вкладка годами носила валидный state.
TX_LIFETIME_SECONDS = 10 * 60

_TX_ALGORITHM = "HS256"


def _tx_encode(payload: dict[str, Any]) -> str:
    now = int(time.time())
    return jwt.encode(
        {**payload, "iat": now, "exp": now + TX_LIFETIME_SECONDS},
        get_secret_key(),
        algorithm=_TX_ALGORITHM,
    )


def _tx_decode(raw: str) -> dict[str, Any]:
    return jwt.decode(raw, get_secret_key(), algorithms=[_TX_ALGORITHM])


def _error_page(message: str, *, status: int = 400) -> HTMLResponse:
    """Человеческая страница ошибки входа (ТЗ п.3): что случилось + два
    осмысленных выхода. Не пустой экран и не сырой JSON.

    Самодостаточный HTML со встроенными стилями — она может отрисоваться
    тогда, когда фронтенд-бандл еще ни разу не грузился (пользователь пришел
    по ссылке прямо на SSO), поэтому полагаться на ассеты приложения нельзя.
    """
    safe = html.escape(message)
    return HTMLResponse(
        status_code=status,
        content=f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Sign-in failed — ABSet</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; background: #F7F7F7;
         margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center; }}
  .card {{ background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.12);
           padding: 28px 32px; max-width: 460px; }}
  h1 {{ font-size: 20px; margin: 0 0 12px; color: #1a1a1a; }}
  p {{ color: #444; line-height: 1.5; margin: 0 0 20px; }}
  .actions {{ display: flex; gap: 10px; flex-wrap: wrap; }}
  a.btn {{ display: inline-block; padding: 8px 16px; border-radius: 6px; text-decoration: none;
           font-size: 14px; }}
  a.primary {{ background: #1677ff; color: #fff; }}
  a.secondary {{ background: #f0f0f0; color: #1a1a1a; }}
</style>
</head>
<body>
  <div class="card">
    <h1>Could not sign you in</h1>
    <p>{safe}</p>
    <div class="actions">
      <a class="btn primary" href="/api/v1/auth/oidc/login">Try again</a>
      <a class="btn secondary" href="/login?sso=failed">Use password instead</a>
    </div>
  </div>
</body>
</html>""",
    )


def _clear_tx(response: Response) -> None:
    response.delete_cookie(TX_COOKIE_NAME, path="/api/v1/auth/oidc")


@router.get("/login")
def oidc_login_start() -> Response:
    """Старт входа: редирект на Keycloak с state/nonce/PKCE."""
    try:
        settings = load_settings()
    except OidcError as e:
        log.error("oidc.settings_invalid", error=str(e))
        return _error_page(f"Single sign-on is misconfigured: {e}", status=500)

    if not settings.enabled:
        return _error_page(
            "Single sign-on is not enabled on this instance. Sign in with your password.",
            status=404,
        )

    try:
        auth_request = build_authorization_request(settings)
    except OidcError as e:
        log.error("oidc.authorize_build_failed", error=str(e))
        return _error_page(
            f"Could not start the single sign-on flow: {e}. The identity provider may be "
            "temporarily unavailable.",
            status=502,
        )

    response = RedirectResponse(auth_request.url, status_code=302)
    response.set_cookie(
        key=TX_COOKIE_NAME,
        value=_tx_encode(
            {
                "state": auth_request.state,
                "nonce": auth_request.nonce,
                "cv": auth_request.code_verifier,
            }
        ),
        max_age=TX_LIFETIME_SECONDS,
        httponly=True,
        secure=_cookie_secure(),
        # Lax, не Strict — см. модульный docstring: Strict не переживет
        # возврат с домена Keycloak, и вход ломался бы всегда.
        samesite="lax",
        # Узкий path: эта cookie не нужна ни одному другому эндпоинту и не
        # должна ездить с каждым запросом к API.
        path="/api/v1/auth/oidc",
    )
    return response


def _cookie_secure() -> bool:
    # Та же ручка, что у сессионной cookie (backend/routers/auth.py) — держим
    # поведение одинаковым, чтобы TLS включался одним переключателем.
    import os

    return os.environ.get("ABKIT_COOKIE_SECURE", "false").lower() == "true"


@router.get("/callback")
def oidc_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    abkit_oidc_tx: str | None = Cookie(default=None),
) -> Response:
    """Возврат от Keycloak: проверка state/nonce, обмен кода, наша сессия."""
    from abkit.auth.service import OidcRejected, oidc_login

    try:
        settings = load_settings()
    except OidcError as e:
        return _error_page(f"Single sign-on is misconfigured: {e}", status=500)
    if not settings.enabled:
        return _error_page("Single sign-on is not enabled on this instance.", status=404)

    # Keycloak сам отказал (пользователь отменил вход, аккаунт отключен,
    # доступ к клиенту запрещен) — сюда приезжает error, а не code.
    if error:
        log.warning("oidc.provider_error", error=error, description=error_description)
        _audit_rejected(reason="provider_error", details={"error": error})
        response = _error_page(
            "The identity provider refused the sign-in"
            + (f": {error_description}" if error_description else f" ({error}).")
            + " If your account was recently disabled, contact your administrator.",
            status=403,
        )
        _clear_tx(response)
        return response

    if not code or not state:
        response = _error_page(
            "The sign-in response from the identity provider was incomplete. Please try again.",
        )
        _clear_tx(response)
        return response

    if not abkit_oidc_tx:
        # Cookie нет: прошли мимо /login, вкладка провисела дольше
        # TX_LIFETIME_SECONDS, или браузер режет сторонние cookie.
        _audit_rejected(reason="missing_transaction")
        response = _error_page(
            "This sign-in link has expired or was opened outside the login flow. "
            "Please start again."
        )
        _clear_tx(response)
        return response

    try:
        tx = _tx_decode(abkit_oidc_tx)
    except jwt.InvalidTokenError:
        _audit_rejected(reason="invalid_transaction")
        response = _error_page("This sign-in attempt is no longer valid. Please start again.")
        _clear_tx(response)
        return response

    # state сравнивается с тем, что мы САМИ подписали в cookie. Подделать
    # обе половины разом нельзя, не зная ABKIT_SECRET_KEY, — это и есть
    # защита от CSRF на этапе логина.
    import secrets as _secrets

    if not _secrets.compare_digest(str(tx.get("state", "")), state):
        log.warning("oidc.state_mismatch")
        _audit_rejected(reason="state_mismatch")
        response = _error_page(
            "The sign-in request could not be verified (state mismatch). This can happen "
            "if the login was started in another tab. Please try again."
        )
        _clear_tx(response)
        return response

    try:
        tokens = exchange_code(settings, code=code, code_verifier=str(tx.get("cv", "")))
        identity = verify_id_token(
            settings, tokens["id_token"], expected_nonce=str(tx.get("nonce", ""))
        )
    except OidcError as e:
        log.error("oidc.callback_failed", error=str(e))
        _audit_rejected(reason="token_validation_failed", details={"error": str(e)})
        response = _error_page(str(e), status=502)
        _clear_tx(response)
        return response

    try:
        token = oidc_login(identity, settings)
    except OidcRejected as e:
        # Провижининг уже записал auth.oidc_login_rejected со своей причиной.
        response = _error_page(str(e), status=403)
        _clear_tx(response)
        return response
    except AuthError as e:
        _audit_rejected(reason="login_failed", details={"error": str(e)})
        response = _error_page(str(e), status=403)
        _clear_tx(response)
        return response

    from backend.routers.auth import _set_session_cookie

    # Обратно в приложение. Абсолютный путь, а не Referer/state — уводить
    # пользователя туда, куда попросил внешний параметр, значит открыть
    # open-redirect.
    response = RedirectResponse("/experiments", status_code=302)
    _set_session_cookie(response, token)
    _clear_tx(response)
    return response


def _audit_rejected(*, reason: str, details: dict[str, Any] | None = None) -> None:
    """auth.oidc_login_rejected для отказов ДО того, как известен пользователь
    (протокольные ошибки). Отказы с известным email пишет сам oidc_login."""
    from abkit.db.repositories import AuditRepo

    AuditRepo().log(
        action="auth.oidc_login_rejected",
        details={"reason": reason, **(details or {})},
    )
