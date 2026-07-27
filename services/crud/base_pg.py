from typing import Any, Mapping, Sequence, Iterable
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select
from sqlalchemy import text

def _where(table: sa.Table, where: Mapping[str, Any]) -> Iterable[Any]:
    return [getattr(table.c, k) == v for k, v in where.items()]

async def get_one(
    session: AsyncSession,
    table: sa.Table,
    where: Mapping[str, Any],
) -> dict | None:
    stmt = sa.select(table).where(*_where(table, where)).limit(1)
    res = await session.execute(stmt)
    row = res.mappings().first()
    return dict(row) if row else None


async def list_rows(session: AsyncSession, stmt: sa.Select) -> list[dict]:
    res = await session.execute(stmt)
    return [dict(r) for r in res.mappings().all()]


async def insert_returning(
    session: AsyncSession,
    table: sa.Table,
    values: Mapping[str, Any],
) -> dict:
    stmt = sa.insert(table).values(**values).returning(*table.c)
    row = (await session.execute(stmt)).mappings().one()
    return dict(row)


async def update_returning(
    session: AsyncSession,
    table: sa.Table,
    where: Mapping[str, Any],
    values: Mapping[str, Any],
    *, auto_commit: bool = True
) -> dict | None:
    stmt = (
        sa.update(table)
        .where(*_where(table, where))
        .values(**values)
        .returning(*table.c)
    )
    row = (await session.execute(stmt)).mappings().first()
    if auto_commit:
        await session.commit()
    return dict(row) if row else None


async def delete_where(
    session: AsyncSession,
    table: sa.Table,
    where: Mapping[str, Any],
) -> None:
    stmt = sa.delete(table).where(*_where(table, where))
    await session.execute(stmt)