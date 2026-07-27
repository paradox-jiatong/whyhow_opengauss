# services/crud/schema_pg.py
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4
from fastapi import FastAPI, HTTPException

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB 

from whyhow_api.services.crud.base_pg import (
    insert_returning, list_rows, delete_where, get_one as pg_get_one
)
from whyhow_api.schemas.schemas import SchemaOutWithWorkspaceDetails

logger = logging.getLogger(__name__)

metadata = sa.MetaData()

schemas = sa.Table(
    "schemas", metadata,
    sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
    sa.Column("workspace_id", PGUUID(as_uuid=True), nullable=False),
    sa.Column("created_by", PGUUID(as_uuid=True), nullable=False),
    sa.Column("name", sa.Text, nullable=True),
    sa.Column("body", JSONB, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
)

graphs = sa.Table(
    "graphs", metadata,
    sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
    sa.Column("schema_id", PGUUID(as_uuid=True), nullable=True),
    sa.Column("created_by", PGUUID(as_uuid=True), nullable=False),
)

async def create_schema(
    session: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    name: Optional[str],
    body: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    values = {
        "id": uuid4(),
        "workspace_id": workspace_id,
        "created_by": user_id,
        "name": name,
        "body": body,
    }
    row = await insert_returning(session, schemas, values)
    await session.commit()
    return row

async def list_schemas(
    session: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    skip: int = 0,
    limit: int = 100,
    order: int = -1,
) -> List[Dict[str, Any]]:
    stmt = (
        sa.select(schemas)
        .where(schemas.c.workspace_id == workspace_id, schemas.c.created_by == user_id)
        .order_by(schemas.c.created_at.asc() if order == 1 else schemas.c.created_at.desc(),
                  schemas.c.id.asc() if order == 1 else schemas.c.id.desc())
        .offset(skip)
    )
    if limit >= 0:
        stmt = stmt.limit(limit)
    return await list_rows(session, stmt)

async def get_schema(
    session: AsyncSession,
    schema_id: UUID,
    user_id: Optional[UUID] = None,
) -> Dict[str, Any] | None:
    where = [schemas.c.id == schema_id]
    if user_id:
        where.append(schemas.c.created_by == user_id)
    stmt = sa.select(schemas).where(*where).limit(1)
    res = await session.execute(stmt)
    row = res.mappings().first()
    return dict(row) if row else None

async def delete_schema(
    session: AsyncSession,
    user_id: UUID,
    schema_id: UUID,
) -> bool:
    in_use = await session.scalar(
        sa.select(sa.func.count()).select_from(graphs).where(
            graphs.c.schema_id == schema_id, graphs.c.created_by == user_id
        )
    )
    if (in_use or 0) > 0:
        return False

    found = await pg_get_one(session, schemas, {"id": schema_id, "created_by": user_id})
    if not found:
        return False
    await delete_where(session, schemas, {"id": schema_id, "created_by": user_id})
    await session.commit()
    return True

async def get_schema_with_workspace(
    session: AsyncSession, 
    schema_id: UUID, 
    user_id: UUID
) -> SchemaOutWithWorkspaceDetails | None:
    """
    根据 schema_id 和 user_id 获取与工作区相关的 schema。
    
    - schema_id: 模式的 UUID。
    - user_id: 用户的 UUID。
    
    如果没有找到相应的 schema 或 schema 不属于该用户，则返回 None。
    """
    stmt = select(schemas).where(
        schemas.c.id == schema_id,
        schemas.c.created_by == user_id
    ).limit(1)
    
    result = await session.execute(stmt)
    row = result.mappings().first()
    
    if row:
        return SchemaOutWithWorkspaceDetails.model_validate(row)
    else:
        raise HTTPException(status_code=404, detail="Schema not found.")