from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth.config import (
    AUTH_LOCK_MINUTES,
    AUTH_MAX_FAILED_ATTEMPTS,
    AUTH_SESSION_HOURS,
)
from app.auth.models import AuthAuditLog, RefreshSession, User, utc_now
from app.auth.passwords import (
    generate_temporary_password,
    hash_password,
    password_hash_needs_rehash,
    validate_private_password,
    verify_password,
)
from app.auth.tokens import create_access_token, generate_refresh_token, hash_refresh_token

VALID_ROLES = {"admin", "editor", "viewer"}
ROLE_LEVEL = {"viewer": 1, "editor": 2, "admin": 3}


class AuthServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat() + "Z"


def serialize_user(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "is_active": bool(user.is_active),
        "must_change_password": bool(user.must_change_password),
        "failed_login_attempts": int(user.failed_login_attempts or 0),
        "locked_until": _iso(user.locked_until),
        "created_at": _iso(user.created_at),
        "updated_at": _iso(user.updated_at),
    }


def record_auth_audit(
    db: Session,
    event_type: str,
    *,
    actor_user_id: str | None = None,
    target_user_id: str | None = None,
    email: str | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuthAuditLog:
    safe_details = details or None
    row = AuthAuditLog(
        event_type=event_type,
        actor_user_id=actor_user_id,
        target_user_id=target_user_id,
        email=normalize_email(email) if email else None,
        details_json=json.dumps(safe_details, ensure_ascii=False, default=str) if safe_details else None,
        ip_address=ip_address,
    )
    db.add(row)
    db.flush()
    return row


def active_admin_count(db: Session, *, excluding_user_id: str | None = None) -> int:
    stmt = select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))
    if excluding_user_id:
        stmt = stmt.where(User.id != excluding_user_id)
    return int(db.scalar(stmt) or 0)


def get_user_by_id(db: Session, user_id: str) -> User | None:
    return db.get(User, user_id)


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == normalize_email(email)))


def _revoke_all_sessions(db: Session, user_id: str) -> None:
    now = utc_now()
    db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def revoke_session(db: Session, session_id: str) -> None:
    session = db.get(RefreshSession, session_id)
    if session and session.revoked_at is None:
        session.revoked_at = utc_now()
        db.flush()


def create_refresh_session(
    db: Session,
    *,
    user: User,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[RefreshSession, str]:
    now = utc_now()
    raw_refresh = generate_refresh_token()
    session = RefreshSession(
        user_id=user.id,
        token_hash=hash_refresh_token(raw_refresh),
        created_at=now,
        expires_at=now + timedelta(hours=AUTH_SESSION_HOURS),
        ip_address=(ip_address or None),
        user_agent=(user_agent or None)[:512] if user_agent else None,
    )
    db.add(session)
    db.flush()
    return session, raw_refresh


def issue_session_payload(db: Session, *, user: User, ip_address: str | None, user_agent: str | None):
    session, raw_refresh = create_refresh_session(
        db, user=user, ip_address=ip_address, user_agent=user_agent
    )
    access, access_expires = create_access_token(
        user_id=user.id,
        session_id=session.id,
        token_version=user.token_version,
    )
    return {
        "access_token": access,
        "access_token_expires_at": access_expires.replace(microsecond=0).isoformat(),
        "session_expires_at": _iso(session.expires_at),
        "user": serialize_user(user),
        "refresh_token": raw_refresh,
        "session_id": session.id,
    }


def authenticate_login(
    db: Session,
    *,
    email: str,
    password: str,
    ip_address: str | None,
    user_agent: str | None,
):
    normalized = normalize_email(email)
    user = get_user_by_email(db, normalized)

    if user and not user.is_active:
        record_auth_audit(
            db,
            "LOGIN_FAILED_DISABLED",
            target_user_id=user.id,
            email=normalized,
            ip_address=ip_address,
        )
        raise AuthServiceError(403, "account_disabled", "This account is disabled. Contact an administrator.")

    now = utc_now()
    if user and user.locked_until and user.locked_until <= now:
        user.failed_login_attempts = 0
        user.locked_until = None
        db.flush()

    if user and user.locked_until and user.locked_until > now:
        record_auth_audit(
            db,
            "LOGIN_FAILED_LOCKED",
            target_user_id=user.id,
            email=normalized,
            ip_address=ip_address,
        )
        raise AuthServiceError(
            429,
            "account_temporarily_locked",
            f"Too many failed login attempts. This account is locked for {AUTH_LOCK_MINUTES} minutes. Please try again later.",
        )

    valid_password = bool(user and verify_password(user.password_hash, password))
    if not valid_password:
        if user:
            user.failed_login_attempts = int(user.failed_login_attempts or 0) + 1
            if user.failed_login_attempts >= AUTH_MAX_FAILED_ATTEMPTS:
                user.locked_until = now + timedelta(minutes=AUTH_LOCK_MINUTES)
            db.flush()
        record_auth_audit(
            db,
            "LOGIN_FAILED",
            target_user_id=user.id if user else None,
            email=normalized,
            details={"failed_attempts": int(user.failed_login_attempts or 0) if user else None},
            ip_address=ip_address,
        )
        if user and user.locked_until and user.locked_until > now:
            raise AuthServiceError(
                429,
                "account_temporarily_locked",
                f"Too many failed login attempts. This account is locked for {AUTH_LOCK_MINUTES} minutes. Please try again later.",
            )
        raise AuthServiceError(401, "invalid_credentials", "Invalid email or password.")

    user.failed_login_attempts = 0
    user.locked_until = None
    if password_hash_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    db.flush()

    payload = issue_session_payload(
        db,
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    record_auth_audit(
        db,
        "LOGIN_SUCCESS",
        actor_user_id=user.id,
        target_user_id=user.id,
        email=user.email,
        ip_address=ip_address,
    )
    return payload


def refresh_session(
    db: Session,
    *,
    raw_refresh_token: str,
    ip_address: str | None,
    user_agent: str | None,
):
    if not raw_refresh_token:
        raise AuthServiceError(401, "missing_refresh_token", "Your session has expired. Please log in again.")

    token_hash = hash_refresh_token(raw_refresh_token)
    session = db.scalar(select(RefreshSession).where(RefreshSession.token_hash == token_hash))
    now = utc_now()
    if not session or session.revoked_at is not None or session.expires_at <= now:
        if session and session.revoked_at is None:
            session.revoked_at = now
        raise AuthServiceError(401, "session_expired", "Your session has expired. Please log in again.")

    user = get_user_by_id(db, session.user_id)
    if not user:
        session.revoked_at = now
        raise AuthServiceError(401, "invalid_session", "Your session is no longer valid. Please log in again.")
    if not user.is_active:
        session.revoked_at = now
        raise AuthServiceError(403, "account_disabled", "This account is disabled. Contact an administrator.")

    # Rotate the opaque refresh token on every use while preserving the original 24-hour session expiry.
    new_refresh = generate_refresh_token()
    session.token_hash = hash_refresh_token(new_refresh)
    session.last_used_at = now
    if ip_address:
        session.ip_address = ip_address
    if user_agent:
        session.user_agent = user_agent[:512]
    db.flush()

    access, access_expires = create_access_token(
        user_id=user.id,
        session_id=session.id,
        token_version=user.token_version,
    )
    return {
        "access_token": access,
        "access_token_expires_at": access_expires.replace(microsecond=0).isoformat(),
        "session_expires_at": _iso(session.expires_at),
        "user": serialize_user(user),
        "refresh_token": new_refresh,
        "session_id": session.id,
    }


def set_private_password(
    db: Session,
    *,
    user: User,
    new_password: str,
    ip_address: str | None,
    user_agent: str | None,
):
    if not user.must_change_password:
        raise AuthServiceError(
            403,
            "password_change_not_allowed",
            "Password changes must be requested through an administrator.",
        )
    try:
        validate_private_password(new_password)
    except ValueError as exc:
        raise AuthServiceError(400, "invalid_password", str(exc)) from exc

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.failed_login_attempts = 0
    user.locked_until = None
    user.token_version = int(user.token_version or 1) + 1
    _revoke_all_sessions(db, user.id)
    db.flush()

    payload = issue_session_payload(db, user=user, ip_address=ip_address, user_agent=user_agent)
    record_auth_audit(
        db,
        "PASSWORD_SET_BY_USER",
        actor_user_id=user.id,
        target_user_id=user.id,
        email=user.email,
        ip_address=ip_address,
    )
    return payload


def create_user_account(
    db: Session,
    *,
    actor: User | None,
    email: str,
    full_name: str,
    role: str,
    ip_address: str | None = None,
) -> tuple[User, str]:
    normalized = normalize_email(email)
    clean_name = str(full_name or "").strip()
    if not clean_name:
        raise AuthServiceError(400, "invalid_full_name", "Full name is required.")
    if role not in VALID_ROLES:
        raise AuthServiceError(400, "invalid_role", "Role must be Admin, Editor, or Viewer.")
    if get_user_by_email(db, normalized):
        raise AuthServiceError(409, "duplicate_email", "An account with this email already exists.")

    temporary_password = generate_temporary_password()
    user = User(
        email=normalized,
        full_name=clean_name,
        role=role,
        is_active=True,
        must_change_password=True,
        password_hash=hash_password(temporary_password),
        failed_login_attempts=0,
        token_version=1,
    )
    db.add(user)
    db.flush()
    record_auth_audit(
        db,
        "USER_CREATED",
        actor_user_id=actor.id if actor else user.id,
        target_user_id=user.id,
        email=user.email,
        details={"role": role, "full_name": clean_name},
        ip_address=ip_address,
    )
    return user, temporary_password


def update_user_account(
    db: Session,
    *,
    actor: User,
    target: User,
    full_name: str | None,
    role: str | None,
    ip_address: str | None,
) -> User:
    changes: dict[str, Any] = {}
    if full_name is not None:
        clean_name = full_name.strip()
        if not clean_name:
            raise AuthServiceError(400, "invalid_full_name", "Full name is required.")
        if clean_name != target.full_name:
            changes["full_name"] = {"from": target.full_name, "to": clean_name}
            target.full_name = clean_name

    if role is not None and role != target.role:
        if role not in VALID_ROLES:
            raise AuthServiceError(400, "invalid_role", "Role must be Admin, Editor, or Viewer.")
        if actor.id == target.id:
            raise AuthServiceError(403, "self_role_change_blocked", "You cannot change your own role.")
        if target.role == "admin" and target.is_active and active_admin_count(db) <= 1:
            raise AuthServiceError(409, "last_admin_protected", "The system must always retain at least one active Admin.")
        changes["role"] = {"from": target.role, "to": role}
        target.role = role

    if changes:
        db.flush()
        record_auth_audit(
            db,
            "USER_UPDATED",
            actor_user_id=actor.id,
            target_user_id=target.id,
            email=target.email,
            details=changes,
            ip_address=ip_address,
        )
    return target


def disable_user(db: Session, *, actor: User, target: User, ip_address: str | None) -> User:
    if actor.id == target.id:
        raise AuthServiceError(403, "self_disable_blocked", "You cannot disable your own account.")
    if not target.is_active:
        return target
    if target.role == "admin" and active_admin_count(db) <= 1:
        raise AuthServiceError(409, "last_admin_protected", "The system must always retain at least one active Admin.")
    target.is_active = False
    target.token_version = int(target.token_version or 1) + 1
    _revoke_all_sessions(db, target.id)
    db.flush()
    record_auth_audit(
        db,
        "USER_DISABLED",
        actor_user_id=actor.id,
        target_user_id=target.id,
        email=target.email,
        ip_address=ip_address,
    )
    return target


def enable_user(db: Session, *, actor: User, target: User, ip_address: str | None) -> User:
    if target.is_active:
        return target
    target.is_active = True
    db.flush()
    record_auth_audit(
        db,
        "USER_ENABLED",
        actor_user_id=actor.id,
        target_user_id=target.id,
        email=target.email,
        ip_address=ip_address,
    )
    return target


def unlock_user(db: Session, *, actor: User, target: User, ip_address: str | None) -> User:
    target.failed_login_attempts = 0
    target.locked_until = None
    db.flush()
    record_auth_audit(
        db,
        "USER_UNLOCKED",
        actor_user_id=actor.id,
        target_user_id=target.id,
        email=target.email,
        ip_address=ip_address,
    )
    return target


def reset_user_password(db: Session, *, actor: User, target: User, ip_address: str | None) -> str:
    temporary_password = generate_temporary_password()
    target.password_hash = hash_password(temporary_password)
    target.must_change_password = True
    target.failed_login_attempts = 0
    target.locked_until = None
    target.token_version = int(target.token_version or 1) + 1
    _revoke_all_sessions(db, target.id)
    db.flush()
    record_auth_audit(
        db,
        "USER_PASSWORD_RESET",
        actor_user_id=actor.id,
        target_user_id=target.id,
        email=target.email,
        ip_address=ip_address,
    )
    return temporary_password


def list_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.full_name.asc(), User.email.asc())).all())


def list_auth_audit_logs(db: Session, limit: int = 100) -> list[dict[str, Any]]:
    rows = list(db.scalars(select(AuthAuditLog).order_by(AuthAuditLog.id.desc()).limit(max(1, min(limit, 500)))).all())
    output = []
    for row in rows:
        try:
            details = json.loads(row.details_json) if row.details_json else None
        except Exception:
            details = None
        output.append(
            {
                "id": row.id,
                "event_type": row.event_type,
                "actor_user_id": row.actor_user_id,
                "target_user_id": row.target_user_id,
                "email": row.email,
                "details": details,
                "ip_address": row.ip_address,
                "created_at": _iso(row.created_at),
            }
        )
    return output
