from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Depends
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings


settings = get_settings()
engine = create_async_engine(settings.database_url, future=True, echo=False)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


async def get_user_db(session: AsyncSession = Depends(get_async_session)) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    from .db_models import User

    yield SQLAlchemyUserDatabase(session, User)
