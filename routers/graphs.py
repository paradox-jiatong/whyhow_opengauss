# routers/graphs.py
import logging
from typing import List
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Body, Header, status
from sqlalchemy.ext.asyncio import AsyncSession
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from whyhow_api.dependencies import get_pg, get_llm_client
from whyhow_api.models.common import LLMClient
from whyhow_api.schemas.graphs import Triple
from whyhow_api.services.crud.graph_pg import (
    list_all_graphs, get_graph, list_nodes, list_relations, delete_graphs, graphs, workspaces
)
from whyhow_api.services.graph_service_pg import build_graph_pg
from whyhow_api.services.crud.user_pg import get_user_by_api_key
from whyhow_api.services.crud.base_pg import insert_returning
from whyhow_api.schemas.graphs import CreateGraphBody

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Graphs"], prefix="/graphs")

async def _require_user_id(session: AsyncSession, api_key: str) -> UUID:
    u = await get_user_by_api_key(session, api_key)
    if not u:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return u["id"]

@router.get("")
async def list_graphs_endpoint(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=-1, le=50),
    order: int = Query(-1),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    graphs = await list_all_graphs(session, user_id=user_id, filters=None, skip=skip, limit=limit, order=order)
    return {"message": "ok", "status": "success", "count": len(graphs or []), "graphs": graphs or []}

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_graph_endpoint(
    body: CreateGraphBody = Body(...),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)

    ws = (await session.execute(
        sa.select(workspaces.c.id).where(workspaces.c.id == body.workspace)
    )).scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    dup = (await session.execute(
        sa.select(graphs.c.id).where(
            graphs.c.workspace_id == body.workspace,
            graphs.c.created_by == user_id,
            graphs.c.name == body.name.strip(),
        ).limit(1)
    )).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail="Graph with the same name already exists in this workspace")

    values = {
        "id": uuid4(),
        "schema_id": body.schema_,
        "workspace_id": body.workspace,
        "created_by": user_id,
        "public": False,
        "name": body.name.strip(),
    }

    try:
        row = await insert_returning(session, graphs, values)
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to create graph: {e.orig}")

    return {"message": "created", "status": "success", "graph": row}

@router.get("/{graph_id}")
async def read_graph_endpoint(
    graph_id: UUID,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    g = await get_graph(session, graph_id=graph_id, user_id=user_id, public=False)
    if not g:
        raise HTTPException(status_code=404, detail="Graph not found.")
    return {"message": "ok", "status": "success", "count": 1, "graphs": [g]}

@router.get("/{graph_id}/relations")
async def relations_endpoint(
    graph_id: UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=-1, le=200),
    order: int = Query(-1),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    rels, total = await list_relations(session, user_id=user_id, graph_id=graph_id, skip=skip, limit=limit, order=order)
    return {"message": "ok", "status": "success", "count": total, "relations": rels}

@router.get("/{graph_id}/nodes")
async def nodes_endpoint(
    graph_id: UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=-1, le=200),
    order: int = Query(-1),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    nodes_, total = await list_nodes(session, user_id=user_id, graph_id=graph_id, skip=skip, limit=limit, order=order)
    return {"message": "ok", "status": "success", "count": total, "nodes": nodes_}

@router.post("/from_triples")
async def create_graph_from_triples_endpoint(
    graph_id: UUID = Query(..., description="已有图 ID"),
    triples: List[Triple] = Body(..., embed=True),
    session: AsyncSession = Depends(get_pg),
    llm_client: LLMClient = Depends(get_llm_client),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    await build_graph_pg(session=session, llm_client=llm_client, graph_id=graph_id, user_id=user_id, triples_in=triples)
    return {"message": "ok", "status": "success"}

@router.delete("/{graph_id}")
async def delete_graph_endpoint(
    graph_id: UUID,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    await delete_graphs(session, user_id=user_id, graph_ids=[graph_id])
    return {"message": "Graph deleted successfully.", "status": "success"}
