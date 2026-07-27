"""Node CRUD routes (openGauss / SQLAlchemy via services.crud.node_pg)."""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.dependencies import get_pg
from whyhow_api.services.crud.user_pg import get_user_by_api_key
from whyhow_api.services.crud import node_pg
from whyhow_api.schemas.nodes import (
    NodeCreate,
    NodeUpdate,
    NodesResponse,
    NodeChunksResponse,
)

from whyhow_api.services.crud.graph_pg import chunks

import sqlalchemy as sa

router = APIRouter(tags=["Nodes"], prefix="/nodes")


async def _require_user_id(session: AsyncSession, api_key: str) -> UUID:
    """检查 x-api-key 并返回 user_id。"""
    user = await get_user_by_api_key(session, api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return user["id"]


def _normalize_label_from_payload(payload: Dict[str, Any]) -> None:
    """
    某些旧 Schema 里节点类型叫 `type`，PG 实现里用 `label`。
    为了兼容，若 body/type 存在则映射到 label。
    """
    if payload is None:
        return
    if "label" not in payload and "type" in payload and payload["type"] is not None:
        payload["label"] = payload["type"]


# ------------------------- Create -------------------------
@router.post("", response_model=NodesResponse)
async def create_node(
    body: NodeCreate,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
) -> NodesResponse:
    """
    创建节点（PG 版）。
    - NodeCreate 若为 { graph, name, type, properties?, chunks? }，将把 type 映射到 label。
    """
    user_id = await _require_user_id(session, api_key)
    payload = body.model_dump()
    _normalize_label_from_payload(payload)

    for k in ("graph", "name", "label"):
        if payload.get(k) in (None, ""):
            raise HTTPException(status_code=422, detail=f"Missing field: {k}")

    node = await node_pg.create_node(
        session=session,
        graph_id=UUID(str(payload["graph"])),
        created_by=user_id,
        name=str(payload["name"]),
        label=str(payload["label"]),
        properties=payload.get("properties") or {},
        chunks=payload.get("chunks") or [],
    )
    return NodesResponse(
        message="Node created successfully",
        status="success",
        count=1,
        nodes=[node],
    )


# ------------------------- List -------------------------
@router.get("", response_model=NodesResponse)
async def list_nodes_endpoint(
    graph_id: UUID = Query(..., description="图 ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=-1, le=50),
    order: int = Query(-1, description="排序：-1=按创建时间倒序，1=正序"),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
) -> NodesResponse:
    """
    按图分页列出节点（PG 版）。
    注：与 Mongo 版不同，这里最小实现先要求提供 graph_id。
    后续你若需要 name/type/workspace 等更多过滤，可在 node_pg.py 中扩展 SQL 条件。
    """
    user_id = await _require_user_id(session, api_key)

    nodes, total = await node_pg.list_nodes(
        session=session,
        graph_id=graph_id,
        user_id=user_id,
        skip=skip,
        limit=limit,
        order=order,
    )
    return NodesResponse(
        message="Nodes retrieved successfully",
        status="success",
        nodes=nodes,
        count=total,
    )


# ------------------------- Read One -------------------------
@router.get("/{node_id}", response_model=NodesResponse)
async def read_node_endpoint(
    node_id: UUID,
    graph_id: Optional[UUID] = Query(None, description="可选：校验该节点属于的图"),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
) -> NodesResponse:
    """获取单节点（可选校验 graph_id）。"""
    user_id = await _require_user_id(session, api_key)

    node = await node_pg.get_node(
        session=session,
        node_id=node_id,
        graph_id=graph_id,
        user_id=user_id,
    )
    if not node:
        raise HTTPException(status_code=404, detail="Node not found.")

    return NodesResponse(
        message="Node retrieved successfully",
        status="success",
        count=1,
        nodes=[node],
    )


# ------------------------- Update -------------------------
@router.put("/{node_id}", response_model=NodesResponse)
async def update_node_endpoint(
    node_id: UUID,
    body: NodeUpdate,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
) -> NodesResponse:
    """
    更新节点（PG 版）。
    - 若 NodeUpdate 使用 `type` 字段，这里会映射到 label。
    """
    user_id = await _require_user_id(session, api_key)
    payload = body.model_dump(exclude_unset=True)
    _normalize_label_from_payload(payload)

    node = await node_pg.update_node(
        session=session,
        node_id=node_id,
        user_id=user_id,
        name=payload.get("name"),
        label=payload.get("label"),
        properties=payload.get("properties"),
        chunks=payload.get("chunks"),
    )
    if not node:
        raise HTTPException(status_code=404, detail="Node not found or not owned by user.")

    return NodesResponse(
        message="Node updated successfully",
        status="success",
        count=1,
        nodes=[node],
    )


# ------------------------- Delete -------------------------
@router.delete(
    "/{node_id}",
    response_model=NodesResponse,
    description="删除节点；如需同时清理关联三元组，可在 services 层串联 triple 清理。",
)
async def delete_node_endpoint(
    node_id: UUID,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
) -> NodesResponse:
    """删除当前用户创建的节点。"""
    user_id = await _require_user_id(session, api_key)

    old = await node_pg.get_node(session=session, node_id=node_id, user_id=user_id)
    if not old:
        raise HTTPException(status_code=404, detail="Node not found or not owned by user.")

    ok = await node_pg.delete_node(session=session, node_id=node_id, user_id=user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Node not found or not owned by user.")

    return NodesResponse(
        message="Node deleted successfully",
        status="success",
        count=1,
        nodes=[old],
    )


# ------------------------- /{node_id}/chunks -------------------------
@router.get("/{node_id}/chunks", response_model=NodeChunksResponse)
async def read_node_with_chunks_endpoint(
    node_id: UUID,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
) -> NodeChunksResponse:
    """
    读取节点引用到的 chunks。
    - 先从 nodes 取 chunks 数组，再到 chunks 表做 IN 查询。
    """
    user_id = await _require_user_id(session, api_key)

    node = await node_pg.get_node(session=session, node_id=node_id, user_id=user_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found.")

    chunk_ids = list(node.chunks or [])
    if not chunk_ids:
        return NodeChunksResponse(message="No chunks found for the node.", status="success", count=0, chunks=[])

    stmt = sa.select(
        chunks.c.id,
        chunks.c.data_type,
        chunks.c.content,
        chunks.c.content_obj,
        chunks.c.metadata,
        chunks.c.created_at,
    ).where(chunks.c.id.in_(chunk_ids))
    rows = (await session.execute(stmt)).mappings().all()

    return NodeChunksResponse(
        message="Node with chunks retrieved successfully.",
        status="success",
        count=len(rows),
        chunks=[dict(r) for r in rows],
    )
