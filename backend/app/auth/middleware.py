from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from app.auth.database import db_session
from app.auth.models import RefreshSession, utc_now
from app.auth.service import ROLE_LEVEL, get_user_by_id
from app.auth.tokens import AccessTokenError, decode_access_token


PUBLIC_PATHS = {
    "/",
    "/api/health",
    "/api/health/live",
    "/api/health/ready",
    "/api/auth/login",
    "/api/auth/refresh",
    "/docs",
    "/redoc",
    "/openapi.json",
}

PASSWORD_GATE_ALLOWED = {
    "/api/auth/me",
    "/api/auth/set-password",
    "/api/auth/logout",
}


def _required_role(path: str, method: str) -> str:
    method = method.upper()
    if path.startswith("/api/admin/"):
        return "admin"
    if path == "/api/detect-cycles" or path == "/api/build-benchmark":
        return "editor"
    if path.startswith("/api/benchmarks"):
        return "editor"
    if path.startswith("/api/rca-rules"):
        return "editor"
    if path == "/rag/reindex":
        return "editor"
    if path == "/api/daily-report/settings" and method != "GET":
        return "editor"
    return "viewer"


def _json_error(status: int, code: str, detail: str):
    return JSONResponse(status_code=status, content={"detail": detail, "code": code})


async def authentication_guard(request: Request, call_next):
    path = request.url.path
    method = request.method.upper()

    if method == "OPTIONS" or path in PUBLIC_PATHS or path.startswith("/docs/"):
        return await call_next(request)
    if not (path.startswith("/api/") or path.startswith("/rag/")):
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return _json_error(401, "missing_token", "Authentication is required.")
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return _json_error(401, "missing_token", "Authentication is required.")

    try:
        payload = decode_access_token(token)
    except AccessTokenError as exc:
        return _json_error(401, exc.code, exc.message)
    except Exception:
        return _json_error(401, "invalid_token", "Authentication token is invalid.")

    try:
        with db_session() as db:
            user = get_user_by_id(db, str(payload.get("sub")))
            if not user:
                return _json_error(401, "invalid_user", "Your account is no longer available.")
            if not user.is_active:
                return _json_error(403, "account_disabled", "This account is disabled. Contact an administrator.")
            if int(payload.get("ver", -1)) != int(user.token_version or 0):
                return _json_error(401, "credentials_changed", "Your credentials have changed. Please log in again.")

            session_id = str(payload.get("sid") or "")
            refresh_session = db.get(RefreshSession, session_id)
            if (
                not refresh_session
                or refresh_session.user_id != user.id
                or refresh_session.revoked_at is not None
                or refresh_session.expires_at <= utc_now()
            ):
                return _json_error(401, "session_revoked", "Your session is no longer valid. Please log in again.")

            request.state.auth_user_id = user.id
            request.state.auth_user = {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role,
                "is_active": bool(user.is_active),
                "must_change_password": bool(user.must_change_password),
            }
            request.state.auth_session_id = session_id

            if user.must_change_password and path not in PASSWORD_GATE_ALLOWED:
                return _json_error(
                    403,
                    "password_change_required",
                    "You must set a private password before continuing.",
                )

            required = _required_role(path, method)
            if ROLE_LEVEL.get(user.role, 0) < ROLE_LEVEL.get(required, 99):
                return _json_error(403, "forbidden", "You do not have permission to perform this action.")
    except Exception:
        return _json_error(503, "auth_database_unavailable", "Authentication service is temporarily unavailable.")

    return await call_next(request)
