from fastapi import HTTPException, status
from fastapi_users import InvalidPasswordException


MIN_PASSWORD_LENGTH = 8


def validate_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise InvalidPasswordException(
            reason=f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
        )


def require_valid_password(password: str) -> None:
    try:
        validate_password_policy(password)
    except InvalidPasswordException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.reason) from exc
