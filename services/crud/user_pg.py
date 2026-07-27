import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from uuid import UUID
import secrets
import string
import base64, datetime as dt
from .base_pg import (
    get_one as pg_get_one,
    insert_returning as pg_insert_one,
    update_returning,
    delete_where as pg_delete_one,
)

metadata = sa.MetaData()

users = sa.Table(
    "users", metadata,
    sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
    sa.Column("email", sa.String(255), nullable=False, unique=True),
    sa.Column("username", sa.String(255), nullable=False),
    sa.Column("firstname", sa.String(255), nullable=False),
    sa.Column("lastname", sa.String(255), nullable=False),
    sa.Column("api_key", sa.String(64), nullable=False, unique=True),
    sa.Column("providers", sa.JSON, nullable=True),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
)


def gen_api_key(n: int = 40) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))

async def get_user_by_api_key(session: AsyncSession, api_key: str) -> dict | None:
    return await pg_get_one(session, users, {"api_key": api_key})

async def get_user_by_id(session: AsyncSession, user_id: UUID) -> dict | None:
    return await pg_get_one(session, users, {"id": user_id})

async def rotate_api_key(session: AsyncSession, user_id: UUID) -> dict | None:
    new_key = gen_api_key()
    updated = await update_returning(
        session,
        users,
        {"id": user_id},
        {"api_key": new_key, "updated_at": func.now()},
        auto_commit=True
    )
    return updated

async def set_providers(session: AsyncSession, user_id: UUID, providers: list[dict]) -> dict | None:
    await update_returning(session, users, {"id": user_id}, {"providers": sa.cast(sa.literal(providers), sa.JSON)})
    return await get_user_by_id(session, user_id)

async def get_providers(session: AsyncSession, user_id: UUID) -> list[dict]:
    row = await get_user_by_id(session, user_id)
    return row["providers"] if row and row.get("providers") else []

async def get_active(session: AsyncSession, user_id: UUID) -> bool:
    row = await get_user_by_id(session, user_id)
    return bool(row and row.get("active", False))
