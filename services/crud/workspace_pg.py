# services/crud/workspace_pg.py
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.services.crud.base_pg import list_rows, insert_returning, get_one as pg_get_one, delete_where
from whyhow_api.services.crud.graph_pg import delete_graphs

logger = logging.getLogger(__name__)

metadata = sa.MetaData()
UUIDT = sa.dialects.postgresql.UUID(as_uuid=True)

workspaces = sa.Table(
    "workspaces", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("created_by", UUIDT, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
)

graphs = sa.Table(
    "graphs", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("workspace_id", UUIDT, nullable=False),
    sa.Column("created_by", UUIDT, nullable=False),
)

schemas = sa.Table(
    "schemas", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("workspace_id", UUIDT, nullable=False),
    sa.Column("created_by", UUIDT, nullable=False),
)

rules = sa.Table(
    "rules", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("workspace_id", UUIDT),
    sa.Column("created_by", UUIDT, nullable=False),
)

documents = sa.Table(
    "documents", metadata,
    sa.Column("id", UUIDT, primary_key=True),
)

document_workspaces = sa.Table(
    "document_workspaces", metadata,
    sa.Column("document_id", UUIDT, primary_key=True),
    sa.Column("workspace_id", UUIDT, primary_key=True),
)

chunks = sa.Table(
    "chunks", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("workspaces", sa.ARRAY(UUIDT), nullable=False),
    sa.Column("tags", sa.JSON, nullable=False),
    sa.Column("user_metadata", sa.JSON, nullable=False),
)

async def create_workspace(
    session: AsyncSession,
    name: str,
    user_id: UUID,
) -> Dict[str, Any]:
    values = {
        "id": uuid4(),
        "name": name,
        "created_by": user_id,
        "created_at": sa.func.now(),
        "updated_at": sa.func.now(),
    }
    row = await insert_returning(session, workspaces, values)
    await session.commit()
    return row

async def get_workspaces(
    session: AsyncSession,
    user_id: UUID,
    skip: int = 0,
    limit: int = 100,
    order: int = -1,
) -> List[Dict[str, Any]]:
    stmt = (
        sa.select(workspaces)
        .where(workspaces.c.created_by == user_id)
        .order_by(workspaces.c.created_at.asc() if order == 1 else workspaces.c.created_at.desc(),
                  workspaces.c.id.asc() if order == 1 else workspaces.c.id.desc())
        .offset(skip)
    )
    if limit >= 0:
        stmt = stmt.limit(limit)
    return await list_rows(session, stmt)

async def get_workspace(session: AsyncSession, workspace_id: UUID, user_id: Optional[UUID] = None) -> Dict[str, Any] | None:  # type: ignore[override]
    where = [workspaces.c.id == workspace_id]
    if user_id:
        where.append(workspaces.c.created_by == user_id)
    stmt = sa.select(workspaces).where(*where).limit(1)
    row = (await session.execute(stmt)).mappings().first()
    return dict(row) if row else None

async def update_workspace(
    session: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    *,
    name: Optional[str] = None
) -> Dict[str, Any] | None:
    """按用户权限更新 workspace；目前只支持改 name（如需 description 同理加字段）"""
    values: Dict[str, Any] = {}
    if name is not None:
        values["name"] = name

    if not values:
        return await pg_get_one(session, workspaces, {"id": workspace_id, "created_by": user_id})

    values["updated_at"] = sa.func.now()

    stmt = (
        sa.update(workspaces)
        .where(workspaces.c.id == workspace_id, workspaces.c.created_by == user_id)
        .values(**values)
        .returning(*workspaces.c)
    )
    row = (await session.execute(stmt)).mappings().first()
    await session.commit()
    return dict(row) if row else None

async def delete_workspace(
    session: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
) -> bool:
    """
    事务性删除 Workspace 及其“引用痕迹”：
    1) 找到该 workspace 下的所有 graphs（属于该用户），调用 delete_graphs（会删 triples/nodes/queries）
    2) 删除该 workspace 下的 rules
    3) 删除该 workspace 下的 schemas（此时已无 graph 引用）
    4) 清理文档映射表 document_workspaces
    5) 清理 chunks：从 workspaces 数组移除该 ws_id，删除 tags/user_metadata 中该 ws_id 的键
    6) 删除 workspace 行本身
    """
    ws = await get_workspace(session, workspace_id, user_id=user_id)
    if not ws:
        return False

    try:
        g_ids = (await session.execute(
            sa.select(graphs.c.id).where(graphs.c.workspace_id == workspace_id,
                                         graphs.c.created_by == user_id)
        )).scalars().all()
        if g_ids:
            await delete_graphs(session, user_id, g_ids)
        await delete_where(session, rules, {"workspace_id": workspace_id, "created_by": user_id})

        await delete_where(session, schemas, {"workspace_id": workspace_id, "created_by": user_id})

        await session.execute(
            sa.delete(document_workspaces).where(document_workspaces.c.workspace_id == workspace_id)
        )

        await session.execute(
            sa.update(chunks)
            .values(workspaces=sa.func.array_remove(chunks.c.workspaces, workspace_id))
        )

        rows = (await session.execute(
            sa.select(chunks.c.id, chunks.c.tags, chunks.c.user_metadata)
        )).mappings().all()
        changed_ids: List[UUID] = []
        for r in rows:
            tags = dict(r["tags"] or {})
            um   = dict(r["user_metadata"] or {})
            key  = str(workspace_id)
            touched = False
            if key in tags:
                tags.pop(key, None)
                touched = True
            if key in um:
                um.pop(key, None)
                touched = True
            if touched:
                await session.execute(
                    sa.update(chunks)
                    .where(chunks.c.id == r["id"])
                    .values(tags=tags, user_metadata=um)
                )
                changed_ids.append(r["id"])
        logger.info("Chunks json cleaned for workspace %s, affected: %s", workspace_id, len(changed_ids))

        await session.execute(
            sa.delete(workspaces).where(workspaces.c.id == workspace_id, workspaces.c.created_by == user_id)
        )

        await session.commit()
        return True
    except Exception:
        await session.rollback()
        logger.exception("Error deleting workspace %s", workspace_id)
        raise
