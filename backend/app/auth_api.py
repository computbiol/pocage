from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi_users.password import PasswordHelper
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth_schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyRead,
    AuthResponse,
    AuthSessionState,
    ChangePasswordRequest,
    CsrfResponse,
    LoginRequest,
    ProfileUpdateRequest,
    UserCreate,
    UserRead,
)
from .auth_users import fastapi_users, get_password_helper
from .config import get_settings
from .db import get_async_session
from .db_models import APIKey, AuthSession, User
from .passwords import require_valid_password
from .security import (
    clear_auth_cookies,
    create_access_token,
    decode_access_token,
    ensure_utc_datetime,
    ensure_csrf_cookie,
    generate_csrf_token,
    generate_secret_token,
    hash_secret,
    set_auth_cookies,
    utcnow,
)


settings = get_settings()
router = APIRouter(prefix=settings.api_prefix)
USER_INACTIVE_CODE = "USER_INACTIVE"
USER_NOT_VERIFIED_CODE = "USER_NOT_VERIFIED"


def _auth_error(status_code: int, code: str, reason: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "reason": reason})


def _ensure_user_allowed(user: User) -> None:
    if not user.is_active:
        raise _auth_error(status.HTTP_403_FORBIDDEN, USER_INACTIVE_CODE, "User is inactive.")
    if not user.is_verified:
        raise _auth_error(
            status.HTTP_403_FORBIDDEN,
            USER_NOT_VERIFIED_CODE,
            "Email is not verified. Please check your inbox or request a new verification email.",
        )


async def require_csrf(request: Request) -> None:
    header_value = request.headers.get("X-CSRF-Token")
    cookie_value = request.cookies.get(settings.csrf_cookie_name)
    if not header_value or not cookie_value or header_value != cookie_value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed.")


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> User:
    token = request.cookies.get(settings.access_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
    payload = decode_access_token(token)
    session_id = payload.get("sid")
    user_id = payload.get("sub")
    if not isinstance(session_id, str) or not isinstance(user_id, str):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
    auth_session = await session.get(AuthSession, uuid.UUID(session_id))
    if (
        auth_session is None
        or auth_session.revoked_at is not None
        or ensure_utc_datetime(auth_session.expires_at) <= utcnow()
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session is invalid.")
    user = await session.get(User, uuid.UUID(user_id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
    _ensure_user_allowed(user)
    return user


async def get_optional_current_user(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> User | None:
    try:
        return await get_current_user(request, session)
    except HTTPException as exc:
        if exc.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}:
            return None
        raise


async def create_session_for_user(
    request: Request,
    response: Response,
    session: AsyncSession,
    user: User,
) -> None:
    now = utcnow()
    refresh_token = generate_secret_token("rt_")
    auth_session = AuthSession(
        user_id=user.id,
        refresh_token_hash=hash_secret(refresh_token),
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
        expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
        last_refreshed_at=now,
    )
    session.add(auth_session)
    await session.flush()
    access_token = create_access_token(user_id=user.id, session_id=auth_session.id)
    user.last_login_at = now
    set_auth_cookies(response, access_token, refresh_token)


auth_router = APIRouter(prefix="/auth", tags=["auth"])
user_router = APIRouter(prefix="/users", tags=["users"])
api_key_router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@auth_router.get("/csrf", response_model=CsrfResponse)
async def issue_csrf_token(request: Request) -> Response:
    csrf_token = request.cookies.get(settings.csrf_cookie_name) or generate_csrf_token()
    response = JSONResponse(CsrfResponse(csrf_token=csrf_token).model_dump(mode="json"))
    ensure_csrf_cookie(response, request, csrf_token)
    return response


@auth_router.get("/session", response_model=AuthSessionState)
async def read_auth_session(current_user: User | None = Depends(get_optional_current_user)) -> AuthSessionState:
    if current_user is None:
        return AuthSessionState(authenticated=False, user=None)
    return AuthSessionState(authenticated=True, user=UserRead.model_validate(current_user))


@auth_router.post("/login", response_model=AuthResponse, dependencies=[Depends(require_csrf)])
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    password_helper: PasswordHelper = Depends(get_password_helper),
) -> Response:
    user = await session.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")

    verified, new_hash = password_helper.verify_and_update(payload.password, user.hashed_password)
    if not verified:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")
    if new_hash:
        user.hashed_password = new_hash
    _ensure_user_allowed(user)

    response = JSONResponse(
        AuthResponse(
            user=UserRead.model_validate(user),
            access_token_expires_in=settings.access_token_ttl_minutes * 60,
            refresh_token_expires_in=settings.refresh_token_ttl_days * 24 * 60 * 60,
        ).model_dump(mode="json")
    )
    ensure_csrf_cookie(response, request)
    await create_session_for_user(request, response, session, user)
    await session.commit()
    return response


@auth_router.post("/refresh", response_model=AuthResponse, dependencies=[Depends(require_csrf)])
async def refresh_session(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token.")

    auth_session = await session.scalar(
        select(AuthSession).where(AuthSession.refresh_token_hash == hash_secret(refresh_token))
    )
    now = utcnow()
    if auth_session is None or auth_session.revoked_at or ensure_utc_datetime(auth_session.expires_at) <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token is invalid.")
    user = await session.get(User, auth_session.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
    _ensure_user_allowed(user)

    new_refresh_token = generate_secret_token("rt_")
    auth_session.refresh_token_hash = hash_secret(new_refresh_token)
    auth_session.last_refreshed_at = now
    auth_session.expires_at = now + timedelta(days=settings.refresh_token_ttl_days)
    response = JSONResponse(
        AuthResponse(
            user=UserRead.model_validate(user),
            access_token_expires_in=settings.access_token_ttl_minutes * 60,
            refresh_token_expires_in=settings.refresh_token_ttl_days * 24 * 60 * 60,
        ).model_dump(mode="json")
    )
    ensure_csrf_cookie(response, request)
    access_token = create_access_token(user_id=user.id, session_id=auth_session.id)
    set_auth_cookies(response, access_token, new_refresh_token)
    await session.commit()
    return response


@auth_router.post("/logout", dependencies=[Depends(require_csrf)])
async def logout(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if refresh_token:
        auth_session = await session.scalar(
            select(AuthSession).where(AuthSession.refresh_token_hash == hash_secret(refresh_token))
        )
        if auth_session is not None and auth_session.revoked_at is None:
            auth_session.revoked_at = utcnow()
            await session.commit()
    response = JSONResponse({"detail": "Logged out."})
    clear_auth_cookies(response)
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    return response


@auth_router.post("/change-password", dependencies=[Depends(require_csrf)])
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
    password_helper: PasswordHelper = Depends(get_password_helper),
) -> Response:
    verified, _ = password_helper.verify_and_update(payload.current_password, current_user.hashed_password)
    if not verified:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is invalid.")
    require_valid_password(payload.new_password)
    current_user.hashed_password = password_helper.hash(payload.new_password)
    result = await session.scalars(
        select(AuthSession).where(AuthSession.user_id == current_user.id, AuthSession.revoked_at.is_(None))
    )
    now = utcnow()
    for auth_session in result:
        auth_session.revoked_at = now
    await session.commit()
    response = JSONResponse({"detail": "Password updated. Please log in again."})
    ensure_csrf_cookie(response, request)
    clear_auth_cookies(response)
    return response


@user_router.get("/me", response_model=UserRead)
async def read_me(current_user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(current_user)


@user_router.patch("/me", response_model=UserRead, dependencies=[Depends(require_csrf)])
async def update_me(
    payload: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> UserRead:
    normalized_display_name = payload.display_name.strip() if payload.display_name else ""
    current_user.display_name = normalized_display_name or None
    await session.commit()
    await session.refresh(current_user)
    return UserRead.model_validate(current_user)


@api_key_router.get("", response_model=list[ApiKeyRead])
async def list_api_keys(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> list[ApiKeyRead]:
    result = await session.scalars(
        select(APIKey).where(APIKey.user_id == current_user.id).order_by(APIKey.created_at.desc())
    )
    return [ApiKeyRead.model_validate(item) for item in result]


@api_key_router.post("", response_model=ApiKeyCreateResponse, dependencies=[Depends(require_csrf)])
async def create_api_key(
    payload: ApiKeyCreateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> ApiKeyCreateResponse:
    plain_secret = generate_secret_token("pat_")
    api_key = APIKey(
        user_id=current_user.id,
        name=payload.name,
        prefix=plain_secret[:12],
        secret_hash=hash_secret(plain_secret),
        expires_at=payload.expires_at,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)
    return ApiKeyCreateResponse(secret=plain_secret, **ApiKeyRead.model_validate(api_key).model_dump())


@api_key_router.delete("/{api_key_id}", dependencies=[Depends(require_csrf)])
async def revoke_api_key(
    api_key_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    api_key = await session.scalar(
        select(APIKey).where(APIKey.id == api_key_id, APIKey.user_id == current_user.id)
    )
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found.")
    api_key.is_active = False
    api_key.revoked_at = utcnow()
    await session.commit()
    return {"detail": "API key revoked."}


router.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
    dependencies=[Depends(require_csrf)],
)
router.include_router(
    fastapi_users.get_reset_password_router(),
    prefix="/auth",
    tags=["auth"],
    dependencies=[Depends(require_csrf)],
)
router.include_router(
    fastapi_users.get_verify_router(UserRead),
    prefix="/auth",
    tags=["auth"],
    dependencies=[Depends(require_csrf)],
)
router.include_router(auth_router)
router.include_router(user_router)
router.include_router(api_key_router)
