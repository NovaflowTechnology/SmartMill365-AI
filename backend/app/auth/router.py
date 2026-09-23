from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from app.auth.config import (
    AUTH_COOKIE_DOMAIN,
    AUTH_COOKIE_SAMESITE,
    AUTH_COOKIE_SECURE,
    AUTH_REFRESH_COOKIE_NAME,
    AUTH_SESSION_HOURS,
)
from app.auth.database import db_session
from app.auth.models import User
from app.auth.schemas import CreateUserRequest, LoginRequest, SetPasswordRequest, UpdateUserRequest
from app.auth.service import (
    AuthServiceError,
    authenticate_login,
    create_user_account,
    disable_user,
    enable_user,
    get_user_by_id,
    list_auth_audit_logs,
    list_users,
    record_auth_audit,
    refresh_session,
    reset_user_password,
    revoke_session,
    serialize_user,
    set_private_password,
    unlock_user,
    update_user_account,
)

router = APIRouter(tags=["Authentication"])


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()[:64]
    return request.client.host[:64] if request.client and request.client.host else None


def _user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _set_refresh_cookie(response: Response, token: str, *, max_age_seconds: int | None = None) -> None:
    response.set_cookie(
        key=AUTH_REFRESH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        max_age=max_age_seconds or AUTH_SESSION_HOURS * 3600,
        path="/api/auth",
        domain=AUTH_COOKIE_DOMAIN,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=AUTH_REFRESH_COOKIE_NAME,
        path="/api/auth",
        domain=AUTH_COOKIE_DOMAIN,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
    )


def _public_session(payload: dict) -> dict:
    return {
        "access_token": payload["access_token"],
        "token_type": "bearer",
        "access_token_expires_at": payload["access_token_expires_at"],
        "session_expires_at": payload["session_expires_at"],
        "user": payload["user"],
    }


def _raise_service_error(exc: AuthServiceError):
    raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


def _current_user(db, request: Request) -> User:
    user_id = getattr(request.state, "auth_user_id", None)
    user = get_user_by_id(db, user_id) if user_id else None
    if not user:
        raise HTTPException(status_code=401, detail="Authentication is required.")
    return user


@router.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, response: Response):
    # Authentication failures intentionally update persistent security state
    # (failed_login_attempts, locked_until and the auth audit log).
    # Catch AuthServiceError *inside* db_session() so those intended updates are
    # committed. Let unexpected exceptions escape so db_session() still rolls
    # the transaction back.
    service_error: AuthServiceError | None = None
    session_payload: dict | None = None

    with db_session() as db:
        try:
            session_payload = authenticate_login(
                db,
                email=str(payload.email),
                password=payload.password,
                ip_address=_client_ip(request),
                user_agent=_user_agent(request),
            )
        except AuthServiceError as exc:
            service_error = exc

    if service_error is not None:
        _raise_service_error(service_error)

    # session_payload is guaranteed on the success path above.
    _set_refresh_cookie(response, session_payload["refresh_token"])
    return _public_session(session_payload)


@router.post("/api/auth/refresh")
def refresh(request: Request, response: Response):
    raw = request.cookies.get(AUTH_REFRESH_COOKIE_NAME, "")
    try:
        with db_session() as db:
            session_payload = refresh_session(
                db,
                raw_refresh_token=raw,
                ip_address=_client_ip(request),
                user_agent=_user_agent(request),
            )
            _set_refresh_cookie(response, session_payload["refresh_token"])
            return _public_session(session_payload)
    except AuthServiceError as exc:
        _clear_refresh_cookie(response)
        _raise_service_error(exc)


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    with db_session() as db:
        session_id = getattr(request.state, "auth_session_id", None)
        user = _current_user(db, request)
        if session_id:
            revoke_session(db, session_id)
        record_auth_audit(
            db,
            "LOGOUT",
            actor_user_id=user.id,
            target_user_id=user.id,
            email=user.email,
            ip_address=_client_ip(request),
        )
    _clear_refresh_cookie(response)
    return {"message": "Signed out successfully."}


@router.get("/api/auth/me")
def me(request: Request):
    with db_session() as db:
        user = _current_user(db, request)
        return {"user": serialize_user(user)}


@router.post("/api/auth/set-password")
def set_password(payload: SetPasswordRequest, request: Request, response: Response):
    try:
        with db_session() as db:
            user = _current_user(db, request)
            session_payload = set_private_password(
                db,
                user=user,
                new_password=payload.new_password,
                ip_address=_client_ip(request),
                user_agent=_user_agent(request),
            )
            _set_refresh_cookie(response, session_payload["refresh_token"])
            return _public_session(session_payload)
    except AuthServiceError as exc:
        _raise_service_error(exc)


@router.get("/api/admin/users")
def admin_list_users(request: Request):
    with db_session() as db:
        _current_user(db, request)
        return {"users": [serialize_user(user) for user in list_users(db)]}


@router.post("/api/admin/users")
def admin_create_user(payload: CreateUserRequest, request: Request):
    try:
        with db_session() as db:
            actor = _current_user(db, request)
            user, temp = create_user_account(
                db,
                actor=actor,
                email=str(payload.email),
                full_name=payload.full_name,
                role=payload.role,
                ip_address=_client_ip(request),
            )
            return {"user": serialize_user(user), "temporary_password": temp}
    except AuthServiceError as exc:
        _raise_service_error(exc)


@router.patch("/api/admin/users/{user_id}")
def admin_update_user(user_id: str, payload: UpdateUserRequest, request: Request):
    try:
        with db_session() as db:
            actor = _current_user(db, request)
            target = get_user_by_id(db, user_id)
            if not target:
                raise HTTPException(status_code=404, detail="User account was not found.")
            updated = update_user_account(
                db,
                actor=actor,
                target=target,
                full_name=payload.full_name,
                role=payload.role,
                ip_address=_client_ip(request),
            )
            return {"user": serialize_user(updated)}
    except AuthServiceError as exc:
        _raise_service_error(exc)


@router.post("/api/admin/users/{user_id}/disable")
def admin_disable_user(user_id: str, request: Request):
    try:
        with db_session() as db:
            actor = _current_user(db, request)
            target = get_user_by_id(db, user_id)
            if not target:
                raise HTTPException(status_code=404, detail="User account was not found.")
            user = disable_user(db, actor=actor, target=target, ip_address=_client_ip(request))
            return {"user": serialize_user(user), "message": "Account disabled successfully."}
    except AuthServiceError as exc:
        _raise_service_error(exc)


@router.post("/api/admin/users/{user_id}/enable")
def admin_enable_user(user_id: str, request: Request):
    try:
        with db_session() as db:
            actor = _current_user(db, request)
            target = get_user_by_id(db, user_id)
            if not target:
                raise HTTPException(status_code=404, detail="User account was not found.")
            user = enable_user(db, actor=actor, target=target, ip_address=_client_ip(request))
            return {"user": serialize_user(user), "message": "Account enabled successfully."}
    except AuthServiceError as exc:
        _raise_service_error(exc)


@router.post("/api/admin/users/{user_id}/unlock")
def admin_unlock_user(user_id: str, request: Request):
    try:
        with db_session() as db:
            actor = _current_user(db, request)
            target = get_user_by_id(db, user_id)
            if not target:
                raise HTTPException(status_code=404, detail="User account was not found.")
            user = unlock_user(db, actor=actor, target=target, ip_address=_client_ip(request))
            return {"user": serialize_user(user), "message": "Temporary login restriction cleared."}
    except AuthServiceError as exc:
        _raise_service_error(exc)


@router.post("/api/admin/users/{user_id}/reset-password")
def admin_reset_password(user_id: str, request: Request):
    try:
        with db_session() as db:
            actor = _current_user(db, request)
            target = get_user_by_id(db, user_id)
            if not target:
                raise HTTPException(status_code=404, detail="User account was not found.")
            temp = reset_user_password(db, actor=actor, target=target, ip_address=_client_ip(request))
            return {
                "user": serialize_user(target),
                "temporary_password": temp,
                "message": "Temporary password generated successfully.",
            }
    except AuthServiceError as exc:
        _raise_service_error(exc)


@router.get("/api/admin/audit-logs")
def admin_audit_logs(request: Request, limit: int = 100):
    with db_session() as db:
        _current_user(db, request)
        return {"logs": list_auth_audit_logs(db, limit=limit)}
