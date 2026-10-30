"""PostgreSQL-backed delegated access storage."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from urllib.parse import unquote, urlsplit

from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from .models import AllowedCommand, Base, IgnoreChat, ShareUser, Sudoer


def normalize_async_url(database_url: str) -> str:
    if database_url.startswith("postgres://"):
        return "postgresql+asyncpg://" + database_url[len("postgres://") :]
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


def database_name_from_url(database_url: str) -> str:
    return unquote(urlsplit(database_url).path.lstrip("/").split("/", 1)[0])


def quote_database_identifier(identifier: str) -> str:
    if not identifier:
        raise ValueError("Database name must not be empty")
    return '"' + identifier.replace('"', '""') + '"'


def _maintenance_url(database_url: str) -> str:
    from sqlalchemy.engine import make_url

    return make_url(normalize_async_url(database_url)).set(
        database="postgres"
    ).render_as_string(hide_password=False)


class AccessStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = normalize_async_url(database_url)
        self.engine: AsyncEngine | None = None
        self._sessions: async_sessionmaker | None = None

    @staticmethod
    def _is_duplicate_database_error(error: Exception) -> bool:
        original = getattr(error, "orig", error)
        return getattr(original, "sqlstate", None) == "42P04" or getattr(
            original, "pgcode", None
        ) == "42P04"

    async def initialize(self) -> None:
        if not self.database_url:
            raise ValueError("DATABASE_URL must be configured for delegated access")
        await self._ensure_database_exists()
        self.engine = create_async_engine(self.database_url, pool_pre_ping=True)
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        try:
            from alembic import command
            from alembic.config import Config

            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", self.database_url.replace("%", "%%"))
            await asyncio.to_thread(command.upgrade, config, "head")
        except Exception:
            await self.close()
            raise

    async def _ensure_database_exists(self) -> None:
        from sqlalchemy import text

        maintenance_engine = create_async_engine(_maintenance_url(self.database_url), isolation_level="AUTOCOMMIT")
        name = database_name_from_url(self.database_url)
        try:
            async with maintenance_engine.connect() as connection:
                exists = await connection.scalar(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": name},
                )
                if not exists:
                    try:
                        await connection.exec_driver_sql(
                            f"CREATE DATABASE {quote_database_identifier(name)}"
                        )
                    except DBAPIError as error:
                        if not self._is_duplicate_database_error(error):
                            raise
        finally:
            await maintenance_engine.dispose()

    @asynccontextmanager
    async def _session(self):
        if self._sessions is None:
            raise RuntimeError("AccessStore.initialize() must be called first")
        async with self._sessions() as session:
            yield session

    async def is_sudoer(self, user_id: int) -> bool:
        async with self._session() as session:
            return await session.get(Sudoer, user_id) is not None

    async def is_share_user(self, user_id: int) -> bool:
        async with self._session() as session:
            return await session.get(ShareUser, user_id) is not None

    async def has_grant(self, user_id: int, command: str) -> bool:
        async with self._session() as session:
            result = await session.execute(
                select(AllowedCommand.user_id).where(
                    AllowedCommand.user_id == user_id,
                    AllowedCommand.command == command.casefold(),
                )
            )
            return result.scalar_one_or_none() is not None

    async def is_ignored_chat(self, chat_id: int) -> bool:
        async with self._session() as session:
            return await session.get(IgnoreChat, chat_id) is not None

    async def add_ignored_chat(self, chat_id: int, title: str | None = None) -> bool:
        async with self._session() as session, session.begin():
            existing = await session.get(IgnoreChat, chat_id)
            if existing is not None:
                existing.title = title
                return False
            session.add(IgnoreChat(chat_id=chat_id, title=title))
            return True

    async def remove_ignored_chat(self, chat_id: int) -> bool:
        async with self._session() as session, session.begin():
            result = await session.execute(
                delete(IgnoreChat).where(IgnoreChat.chat_id == chat_id)
            )
            return bool(result.rowcount)

    async def list_ignored_chats(self) -> list[IgnoreChat]:
        async with self._session() as session:
            return list((await session.scalars(select(IgnoreChat))).all())

    async def list_sudoers(self) -> list[Sudoer]:
        async with self._session() as session:
            return list((await session.scalars(select(Sudoer))).all())

    async def list_share_users(self) -> list[ShareUser]:
        async with self._session() as session:
            return list((await session.scalars(select(ShareUser))).all())

    async def list_grants(self, user_id: int) -> list[str]:
        async with self._session() as session:
            return list((await session.scalars(select(AllowedCommand.command).where(AllowedCommand.user_id == user_id))).all())

    async def list_grants_by_user(self) -> list[tuple[int, str | None, str | None, list[str]]]:
        async with self._session() as session:
            result = await session.execute(
                select(
                    AllowedCommand.user_id,
                    ShareUser.first_name,
                    ShareUser.last_name,
                    AllowedCommand.command,
                )
                .outerjoin(ShareUser, ShareUser.user_id == AllowedCommand.user_id)
                .order_by(AllowedCommand.user_id, AllowedCommand.command)
            )
            grants: dict[int, tuple[str | None, str | None, list[str]]] = {}
            for user_id, first_name, last_name, command in result:
                if user_id not in grants:
                    grants[user_id] = (first_name, last_name, [])
                grants[user_id][2].append(command)
            return [
                (user_id, first_name, last_name, commands)
                for user_id, (first_name, last_name, commands) in grants.items()
            ]

    async def add_sudoer(
        self, user_id: int, first_name: str | None = None, last_name: str | None = None
    ) -> bool:
        return await self._add_role(Sudoer, user_id, first_name, last_name)

    async def remove_sudoer(self, user_id: int) -> bool:
        return await self._remove_role(Sudoer, user_id)

    async def add_share_user(
        self, user_id: int, first_name: str | None = None, last_name: str | None = None
    ) -> bool:
        return await self._add_role(ShareUser, user_id, first_name, last_name)

    async def remove_share_user(self, user_id: int) -> bool:
        return await self._remove_role(ShareUser, user_id)

    async def _add_role(
        self,
        model,
        user_id: int,
        first_name: str | None,
        last_name: str | None,
    ) -> bool:
        async with self._session() as session, session.begin():
            existing = await session.get(model, user_id)
            if existing is not None:
                if first_name:
                    existing.first_name = first_name
                if last_name:
                    existing.last_name = last_name
                return False
            session.add(
                model(user_id=user_id, first_name=first_name, last_name=last_name)
            )
            return True

    async def _remove_role(self, model, user_id: int) -> bool:
        async with self._session() as session, session.begin():
            result = await session.execute(delete(model).where(model.user_id == user_id))
            return bool(result.rowcount)

    async def grant(self, user_id: int, command: str) -> bool:
        command = command.casefold().lstrip(".#?")
        if not command:
            raise ValueError("Command name must not be empty")
        async with self._session() as session, session.begin():
            key = (user_id, command)
            if await session.get(AllowedCommand, key) is not None:
                return False
            session.add(AllowedCommand(user_id=user_id, command=command))
            return True

    async def revoke(self, user_id: int, command: str) -> bool:
        command = command.casefold().lstrip(".#?")
        async with self._session() as session, session.begin():
            result = await session.execute(
                delete(AllowedCommand).where(
                    AllowedCommand.user_id == user_id,
                    AllowedCommand.command == command,
                )
            )
            return bool(result.rowcount)

    async def reset_grants(self, user_id: int) -> int:
        async with self._session() as session, session.begin():
            result = await session.execute(
                delete(AllowedCommand).where(AllowedCommand.user_id == user_id)
            )
            return result.rowcount or 0

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()
            self.engine = None
            self._sessions = None
