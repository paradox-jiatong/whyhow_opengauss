# services/crud/rule_pg.py
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.services.crud.base_pg import (
    insert_returning, list_rows, delete_where, get_one as pg_get_one
)

logger = logging.getLogger(__name__)

metadata = sa.MetaData()
UUIDT = sa.dialects.postgresql.UUID(as_uuid=True)

rules = sa.Table(
    "rules", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("workspace_id", UUIDT, nullable=True),
    sa.Column("graph_id", UUIDT, nullable=True),
    sa.Column("created_by", UUIDT, nullable=False),
    sa.Column("name", sa.Text, nullable=True),
    sa.Column("body", sa.JSON, key="rule", nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
)

async def create_rule(
    session: AsyncSession,
    user_id: UUID,
    rule_body: Dict[str, Any],
    workspace_id: Optional[UUID] = None,
    graph_id: Optional[UUID] = None,
) -> Dict[str, Any]:
    values = {
        "id": uuid4(),
        "workspace_id": workspace_id,
        "graph_id": graph_id,
        "created_by": user_id,
        "rule": rule_body,
    }
    row = await insert_returning(session, rules, values)
    await session.commit()
    return row

async def get_workspace_rules(
    session: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    skip: int = 0,
    limit: int = 100,
    order: int = -1,
) -> List[Dict[str, Any]]:
    stmt = (
        sa.select(rules)
        .where(rules.c.workspace_id == workspace_id, rules.c.created_by == user_id)
        .order_by(rules.c.created_at.asc() if order == 1 else rules.c.created_at.desc(),
                  rules.c.id.asc() if order == 1 else rules.c.id.desc())
        .offset(skip)
    )
    if limit >= 0:
        stmt = stmt.limit(limit)
    return await list_rows(session, stmt)

async def get_graph_rules(
    session: AsyncSession,
    graph_id: UUID,
    skip: int = 0,
    limit: int = 100,
    order: int = -1,
    user_id: Optional[UUID] = None,
) -> List[Dict[str, Any]]:
    where = [rules.c.graph_id == graph_id]
    if user_id:
        where.append(rules.c.created_by == user_id)
    stmt = (
        sa.select(rules)
        .where(*where)
        .order_by(rules.c.created_at.asc() if order == 1 else rules.c.created_at.desc(),
                  rules.c.id.asc() if order == 1 else rules.c.id.desc())
        .offset(skip)
    )
    if limit >= 0:
        stmt = stmt.limit(limit)
    return await list_rows(session, stmt)

async def delete_rule(
    session: AsyncSession,
    user_id: UUID,
    rule_id: UUID,
) -> bool:
    found = await pg_get_one(session, rules, {"id": rule_id, "created_by": user_id})
    if not found:
        return False
    await delete_where(session, rules, {"id": rule_id, "created_by": user_id})
    await session.commit()
    return True
