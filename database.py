"""Database connection and session management (PG-only)."""

import logging
import re
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from whyhow_api.config import Settings

logger = logging.getLogger(__name__)

pg_engine: AsyncEngine | None = None
pg_sessionmaker: async_sessionmaker[AsyncSession] | None = None

# --- openGauss server_version ---
def _og_get_server_version_info(self, connection):
    v = connection.exec_driver_sql("select version()").scalar()
    if not isinstance(v, str):
        return (13, 0)
    m = re.search(r"openGauss\s+(\d+)\.(\d+)\.(\d+)", v, re.IGNORECASE)
    if m:
        return tuple(int(x) for x in m.groups())
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", v)
    if m:
        return tuple(int(x) for x in m.groups() if x is not None)
    return (13, 0)

PGDialect._get_server_version_info = _og_get_server_version_info

async def connect_to_pg(settings: Settings) -> None:
    """初始化 openGauss/Postgres 引擎与会话工厂。"""
    global pg_engine, pg_sessionmaker
    if pg_engine is None:
        pg_engine = create_async_engine(
            settings.opengauss.dsn,          # postgresql+asyncpg://user:pass@host:port/db
            echo=settings.opengauss.echo_sql,
            pool_pre_ping=True,
        )
        pg_sessionmaker = async_sessionmaker(pg_engine, expire_on_commit=False)
        logger.info("Connected to openGauss/Postgres.")

async def close_pg() -> None:
    """关闭引擎。"""
    global pg_engine, pg_sessionmaker
    if pg_engine is not None:
        await pg_engine.dispose()
        pg_engine = None
        pg_sessionmaker = None
        logger.info("openGauss/Postgres connection closed.")

@asynccontextmanager
async def get_pg_session() -> AsyncGenerator[AsyncSession, None]:
    """获取 AsyncSession（事务由调用方决定是否显式使用）。"""
    if pg_sessionmaker is None:
        raise RuntimeError("Postgres has not been initialised. Call connect_to_pg() first.")
    async with pg_sessionmaker() as session:
        yield session
