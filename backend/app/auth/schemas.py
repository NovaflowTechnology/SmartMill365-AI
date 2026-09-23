from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field

RoleName = Literal["admin", "editor", "viewer"]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=6)


class UserPublic(BaseModel):
    id: str
    email: str
    full_name: str
    role: RoleName
    is_active: bool
    must_change_password: bool
    failed_login_attempts: int = 0
    locked_until: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    access_token_expires_at: str
    session_expires_at: str
    user: UserPublic


class CreateUserRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    role: RoleName = "viewer"


class UpdateUserRequest(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    role: Optional[RoleName] = None


class AdminUserResponse(BaseModel):
    user: UserPublic
    temporary_password: Optional[str] = None


class AuditLogPublic(BaseModel):
    id: int
    event_type: str
    actor_user_id: Optional[str] = None
    target_user_id: Optional[str] = None
    email: Optional[str] = None
    details: Optional[dict] = None
    ip_address: Optional[str] = None
    created_at: str
