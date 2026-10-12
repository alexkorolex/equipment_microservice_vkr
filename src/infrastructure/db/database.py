from __future__ import annotations

import json
import logging
from pathlib import Path
from types import TracebackType

import asyncpg
from asyncpg.exceptions import DuplicateDatabaseError, InvalidCatalogNameError

from src.etc.settings_microservice.base import Settings


class Database:
    def __init__(
        self,
        settings: Settings,
        logger: logging.Logger,
        schema_path: Path | None = None,
    ) -> None:
        self._settings = settings
        self._logger = logger
        self._schema_path = schema_path or Path(__file__).with_name("database.sql")
        self.pool: asyncpg.Pool | None = None

    async def __aenter__(self) -> "Database":
        self.pool = await self.create_pool()
        await self.apply_schema()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def create_pool(self) -> asyncpg.Pool:
        try:
            pool = await self._create_pool(self._settings.db_name)
        except InvalidCatalogNameError:
            await self.ensure_database_exists()
            pool = await self._create_pool(self._settings.db_name)
        self._logger.info(
            "PostgreSQL pool created",
            extra={"database": self._settings.db_name},
        )
        return pool

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None
            self._logger.info("PostgreSQL pool closed")

    async def apply_schema(self) -> None:
        schema_sql = self._load_schema_sql()
        async with self._required_pool().acquire() as connection:
            await connection.execute(schema_sql)
        self._logger.info(
            "Equipment database schema applied",
            extra={"schema_path": str(self._schema_path)},
        )

    async def drop_schemas(self) -> None:
        query = "DROP SCHEMA IF EXISTS equipment CASCADE"
        async with self._required_pool().acquire() as connection:
            await connection.execute(query)
        self._logger.warning("Equipment database schema dropped")

    async def ensure_database_exists(self) -> None:
        connection = await self._connect_admin_database()
        try:
            exists = await connection.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1",
                self._settings.db_name,
            )
            if not exists:
                await self._create_database(connection)
        finally:
            await connection.close()

    async def _create_database(self, connection: asyncpg.Connection) -> None:
        try:
            name = self._quote_identifier(self._settings.db_name)
            await connection.execute(f"CREATE DATABASE {name}")
        except DuplicateDatabaseError:
            return
        self._logger.info(
            "PostgreSQL database created",
            extra={"database": self._settings.db_name},
        )

    async def _connect_admin_database(self) -> asyncpg.Connection:
        last_error: Exception | None = None
        for database_name in ("postgres", "template1"):
            try:
                return await self._connect(database_name)
            except InvalidCatalogNameError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise ConnectionError("Unable to connect to an administrative database")

    async def _connect(self, database_name: str) -> asyncpg.Connection:
        return await asyncpg.connect(
            user=self._settings.db_user,
            password=self._settings.db_password,
            database=database_name,
            host=self._settings.db_host,
            port=self._settings.db_port,
        )

    async def _create_pool(self, database_name: str) -> asyncpg.Pool:
        return await asyncpg.create_pool(
            user=self._settings.db_user,
            password=self._settings.db_password,
            database=database_name,
            host=self._settings.db_host,
            port=self._settings.db_port,
            min_size=1,
            max_size=10,
            init=self._configure_connection,
        )

    def _load_schema_sql(self) -> str:
        schema_sql = self._schema_path.read_text(encoding="utf-8").strip()
        if not schema_sql:
            raise ValueError(f"Database schema file is empty: {self._schema_path}")
        return schema_sql

    def _required_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("Database pool is not initialized")
        return self.pool

    @staticmethod
    def _quote_identifier(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    @staticmethod
    async def _configure_connection(connection: asyncpg.Connection) -> None:
        for type_name in ("json", "jsonb"):
            await connection.set_type_codec(
                type_name,
                schema="pg_catalog",
                encoder=json.dumps,
                decoder=json.loads,
                format="text",
            )
