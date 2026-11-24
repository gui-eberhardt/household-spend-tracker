"""Very small household login: one shared password, signed session cookie."""

import hmac

from fastapi import Cookie, HTTPException, status
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import get_settings

COOKIE_NAME = "hst_session"
MAX_AGE = 60 * 60 * 24 * 90  # 90 days, so the phone stays logged in


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="household-session")


def check_password(password: str) -> bool:
    return hmac.compare_digest(password.encode(), get_settings().household_password.encode())


def make_token() -> str:
    return _serializer().dumps({"ok": True})


def require_session(hst_session: str | None = Cookie(default=None)) -> None:
    if not hst_session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in")
    try:
        _serializer().loads(hst_session, max_age=MAX_AGE)
    except BadSignature:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired") from None
