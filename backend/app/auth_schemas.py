from __future__ import annotations

import uuid
from datetime import datetime

from fastapi_users import schemas
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRead(schemas.BaseUser[uuid.UUID]):
    model_config = ConfigDict(from_attributes=True)

    display_name: str | None = None
    avatar_url: str | None = None
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserCreate(schemas.BaseUserCreate):
    display_name: str | None = Field(default=None, max_length=255)


class UserUpdate(schemas.BaseUserUpdate):
    display_name: str | None = Field(default=None, max_length=255)
    avatar_url: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ProfileUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)


class AuthResponse(BaseModel):
    user: UserRead
    access_token_expires_in: int
    refresh_token_expires_in: int


class AuthSessionState(BaseModel):
    authenticated: bool
    user: UserRead | None = None


class CsrfResponse(BaseModel):
    csrf_token: str


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    expires_at: datetime | None = None


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    prefix: str
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    is_active: bool
    created_at: datetime | None


class ApiKeyCreateResponse(ApiKeyRead):
    secret: str
