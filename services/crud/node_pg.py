# services/crud/node_pg.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.schemas.nodes import NodeWithId

metadata = sa.MetaData()
UUIDT = sa.dialects.postgresql.UUID(as_uuid=True)

nodes = sa.Table(
    "nodes", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("graph_id", UUIDT, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("label", sa.Text, nullable=False),
    sa.Column("properties", sa.JSON, nullable=False),
    sa.Column("chunks", sa.ARRAY(UUIDT), nullable=False),
    sa.Column("created_by", UUIDT, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
)

# --------------------------
# 列表
# --------------------------
async def list_nodes(
    session: AsyncSession,
    *,
    graph_id: UUID,
    user_id: Optional[UUID] = None,
    skip: int = 0,
    limit: int = 100,
    order: int = -1,
) -> Tuple[List[NodeWithId], int]:
    """按图列出节点，支持按创建人过滤、分页和排序。"""
    where = [nodes.c.graph_id == graph_id]
    if user_id:
        where.append(nodes.c.created_by == user_id)

    total = await session.scalar(
        sa.select(sa.func.count()).select_from(nodes).where(*where)
    ) or 0

    order_cols = [
        nodes.c.created_at.asc() if order == 1 else nodes.c.created_at.desc(),
        nodes.c.id.asc() if order == 1 else nodes.c.id.desc(),
    ]
    stmt = (
        sa.select(nodes)
        .where(*where)
        .order_by(*order_cols)
        .offset(skip)
    )
    if limit >= 0:
        stmt = stmt.limit(limit)

    rows = (await session.execute(stmt)).mappings().all()
    out = [
        NodeWithId(
            _id=r["id"],
            name=r["name"],
            label=r["label"],
            properties=r["properties"],
            chunks=r["chunks"],
        )
        for r in rows
    ]
    return out, int(total)

# --------------------------
# 获取单个
# --------------------------
async def get_node(
    session: AsyncSession,
    *,
    node_id: UUID,
    graph_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
) -> Optional[NodeWithId]:
    """获取单节点；可选校验 graph_id / user_id。"""
    where = [nodes.c.id == node_id]
    if graph_id:
        where.append(nodes.c.graph_id == graph_id)
    if user_id:
        where.append(nodes.c.created_by == user_id)

    row = (await session.execute(sa.select(nodes).where(*where).limit(1))).mappings().first()
    if not row:
        return None

    return NodeWithId(
        _id=row["id"],
        name=row["name"],
        label=row["label"],
        properties=row["properties"],
        chunks=row["chunks"],
    )

# --------------------------
# 新增
# --------------------------
async def create_node(
    session: AsyncSession,
    *,
    graph_id: UUID,
    created_by: UUID,
    name: str,
    label: str,
    properties: Dict[str, Any] | None = None,
    chunks: Optional[List[UUID]] = None,
) -> NodeWithId:
    """创建节点；properties 默认空 dict，chunks 默认空数组。"""
    new_id = uuid4()
    stmt = (
        nodes.insert()
        .values(
            id=new_id,
            graph_id=graph_id,
            name=name,
            label=label,
            properties=properties or {},
            chunks=chunks or [],
            created_by=created_by,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
        .returning(*nodes.c)
    )
    row = (await session.execute(stmt)).mappings().one()
    await session.commit()

    return NodeWithId(
        _id=row["id"],
        name=row["name"],
        label=row["label"],
        properties=row["properties"],
        chunks=row["chunks"],
    )

# --------------------------
# 更新
# --------------------------
async def update_node(
    session: AsyncSession,
    *,
    node_id: UUID,
    user_id: UUID,
    name: Optional[str] = None,
    label: Optional[str] = None,
    properties: Optional[Dict[str, Any]] = None,
    chunks: Optional[List[UUID]] = None,
) -> Optional[NodeWithId]:
    """仅允许更新当前用户创建的节点。"""
    to_set: Dict[str, Any] = {"updated_at": sa.func.now()}
    if name is not None:
        to_set["name"] = name
    if label is not None:
        to_set["label"] = label
    if properties is not None:
        to_set["properties"] = properties
    if chunks is not None:
        to_set["chunks"] = chunks

    if len(to_set) == 1:
        return await get_node(session, node_id=node_id, user_id=user_id)

    stmt = (
        sa.update(nodes)
        .where(nodes.c.id == node_id, nodes.c.created_by == user_id)
        .values(**to_set)
        .returning(*nodes.c)
    )
    row = (await session.execute(stmt)).mappings().first()
    if not row:
        return None
    await session.commit()

    return NodeWithId(
        _id=row["id"],
        name=row["name"],
        label=row["label"],
        properties=row["properties"],
        chunks=row["chunks"],
    )

# --------------------------
# 删除
# --------------------------
async def delete_node(
    session: AsyncSession,
    *,
    node_id: UUID,
    user_id: UUID,
) -> bool:
    """删除当前用户创建的节点；返回是否删除成功。"""
    result = await session.execute(
        sa.delete(nodes).where(nodes.c.id == node_id, nodes.c.created_by == user_id)
    )
    await session.commit()
    return result.rowcount > 0

async def delete_nodes_by_graph(
    session: AsyncSession,
    *,
    graph_id: UUID,
    user_id: Optional[UUID] = None,
) -> int:
    where = [nodes.c.graph_id == graph_id]
    if user_id:
        where.append(nodes.c.created_by == user_id)
    result = await session.execute(sa.delete(nodes).where(*where))
    await session.commit()
    return int(result.rowcount or 0)
