"""JWT authentication utilities for ChemFlow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db import get_db
from app.models.orm import User

_bearer = HTTPBearer()


def create_access_token(user_id: str, email: str) -> str:
    """Return a signed JWT valid for ``settings.jwt_expire_minutes``."""
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": user_id, "email": email, "exp": expires}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency — validate Bearer JWT and return the matching User.

    Raises HTTP 401 if the token is missing, malformed, expired, or the user
    does not exist in the database.
    """
    _401 = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        user_id: str | None = payload.get("sub")
        if not user_id:
            raise _401
    except JWTError:
        raise _401

    user = await db.get(User, user_id)
    if user is None:
        raise _401
    return user
