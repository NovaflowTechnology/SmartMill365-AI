from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.auth.config import AUTH_ACCESS_TOKEN_MINUTES, AUTH_JWT_ALGORITHM, require_jwt_secret


class AccessTokenError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def create_access_token(*, user_id: str, session_id: str, token_version: int) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=AUTH_ACCESS_TOKEN_MINUTES)
    payload: dict[str, Any] = {
        "sub": user_id,
        "sid": session_id,
        "ver": int(token_version),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, require_jwt_secret(), algorithm=AUTH_JWT_ALGORITHM), expires_at


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, require_jwt_secret(), algorithms=[AUTH_JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise AccessTokenError("token_expired", "Your session token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise AccessTokenError("invalid_token", "Authentication token is invalid.") from exc

    if payload.get("type") != "access" or not payload.get("sub") or not payload.get("sid"):
        raise AccessTokenError("invalid_token", "Authentication token is invalid.")
    return payload


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(64)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
