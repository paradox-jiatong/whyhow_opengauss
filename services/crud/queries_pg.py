# whyhow_api/services/crud/queries_pg.py
from __future__ import annotations

import sqlalchemy as sa
from typing import Sequence, Optional, Tuple
from uuid import UUID

from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import select, func, literal_column

metadata = sa.MetaData()

queries = sa.Table(
    "queries",
    metadata,
    sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
    sa.Column("user_id", PGUUID(as_uuid=True), nullable=False),
    sa.Column("graph_id", PGUUID(as_uuid=True), nullable=True),
    sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
    sa.Column("name", sa.Text, nullable=True),
    sa.Column("payload", sa.JSON, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
)

graphs = sa.Table(
    "graphs",
    metadata,
    sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
    sa.Column("name", sa.Text, nullable=False, unique=False),
)

def _order_clause(order: int):
    return queries.c.created_at.desc() if order == -1 else queries.c.created_at.asc()

async def list_queries(
    session: AsyncSession,
    *,
    created_by: UUID,
    skip: int,
    limit: int,
    order: int,
    status: Optional[str],
    graph_id: Optional[UUID],
    graph_name: Optional[str],
) -> Sequence[dict]:
    conds = [queries.c.user_id == created_by]
    if status:
        conds.append(queries.c.status == status)
    if graph_id:
        conds.append(queries.c.graph_id == graph_id)

    if graph_name:
        stmt = (
            select(queries)
            .join(graphs, queries.c.graph_id == graphs.c.id, isouter=True)
            .where(*(conds + [graphs.c.name == graph_name]))
            .order_by(_order_clause(order))
            .offset(skip)
        )
    else:
        stmt = (
            select(queries)
            .where(*conds)
            .order_by(_order_clause(order))
            .offset(skip)
        )

    if limit != -1:
        stmt = stmt.limit(limit)

    result = await session.execute(stmt)
    return [dict(r._mapping) for r in result]

async def count_queries(
    session: AsyncSession,
    *,
    created_by: UUID,
    status: Optional[str],
    graph_id: Optional[UUID],
    graph_name: Optional[str],
) -> int:
    conds = [queries.c.user_id == created_by]
    if status:
        conds.append(queries.c.status == status)
    if graph_id:
        conds.append(queries.c.graph_id == graph_id)

    if graph_name:
        stmt = (
            select(func.count(literal_column("*")))
            .select_from(queries.join(graphs, queries.c.graph_id == graphs.c.id, isouter=True))
            .where(*(conds + [graphs.c.name == graph_name]))
        )
    else:
        stmt = select(func.count(literal_column("*"))).select_from(queries).where(*conds)

    total = await session.scalar(stmt)
    return int(total or 0)

async def get_query(
    session: AsyncSession,
    *,
    query_id: UUID,
    created_by: UUID,
) -> Optional[dict]:
    stmt = select(queries).where(queries.c.id == query_id, queries.c.user_id == created_by).limit(1)
    row = await session.execute(stmt)
    r = row.first()
    return dict(r._mapping) if r else None

async def delete_query(
    session: AsyncSession,
    *,
    query_id: UUID,
    created_by: UUID,
) -> Optional[dict]:
    row = await get_query(session, query_id=query_id, created_by=created_by)
    if not row:
        return None
    await session.execute(sa.delete(queries).where(queries.c.id == query_id, queries.c.user_id == created_by))
    await session.commit()
    return row
