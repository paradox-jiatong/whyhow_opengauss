# whyhow_api/routers/triples.py
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.dependencies import get_pg
from whyhow_api.services.crud.user_pg import get_user_by_api_key
from whyhow_api.services.crud.triple_pg import (
    list_triples, create_triple, update_triple, delete_triple,
)

router = APIRouter(prefix="/triples", tags=["triples"])

async def _require_user_id(session: AsyncSession, api_key: str) -> UUID:
    user = await get_user_by_api_key(session, api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return user["id"]

@router.get("")
async def list_triples_api(
    graph_id: UUID = Query(..., description="图谱ID"),
    skip: int = 0,
    limit: int = 100,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    rows = await list_triples(session, graph_id=graph_id, created_by=user_id, skip=skip, limit=limit)
    return {"message": "ok", "status": "success", "count": len(rows), "triples": rows}

@router.post("")
async def create_triple_api(
    payload: Dict[str, Any] = Body(..., example={
        "graph_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "subject": "Alice",
        "predicate": "knows",
        "object": "Bob",
    }),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)

    required = ["graph_id", "subject", "predicate", "object"]
    if any(k not in payload for k in required):
        raise HTTPException(status_code=422, detail=f"Missing fields: {required}")

    triple = await create_triple(
        session=session,
        graph_id=UUID(payload["graph_id"]),
        subject=str(payload["subject"]),
        predicate=str(payload["predicate"]),
        object_=str(payload["object"]),
        created_by=user_id,
    )
    return {"message": "created", "status": "success", "triple": triple}

@router.put("/{triple_id}")
async def update_triple_api(
    triple_id: UUID,
    payload: Dict[str, Any] = Body(
        ...,
        examples={
            "change_predicate": {
                "summary": "仅改谓词",
                "value": {"predicate": "reports_to"},
            },
            "merge_properties": {
                "summary": "合并 properties 并修改 subject",
                "value": {"properties": {"reason": "org change"}, "subject": "Alice"},
            },
            "replace_props": {
                "summary": "覆盖 properties（不合并）",
                "value": {"properties": {"source": "sync"}, "merge_properties": False},
            },
        },
    ),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    """更新 triple。支持字段：
       - predicate -> relation_name
       - subject / object -> 写入 properties.subject / properties.object
       - properties (默认合并，可通过 merge_properties=false 覆盖)
       - head_node_id / tail_node_id / chunks
    """
    user_id = await get_user_by_api_key(session, api_key)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid API key")
    user_id = user_id["id"]

    if not any(k in payload for k in ("predicate", "subject", "object", "properties", "head_node_id", "tail_node_id", "chunks")):
        raise HTTPException(status_code=422, detail="No updatable fields provided.")

    predicate: Optional[str] = payload.get("predicate")
    subject: Optional[str] = payload.get("subject")
    object_: Optional[str] = payload.get("object")
    properties: Optional[Dict[str, Any]] = payload.get("properties")
    head_node_id = payload.get("head_node_id")
    tail_node_id = payload.get("tail_node_id")
    chunks = payload.get("chunks")
    merge_properties = payload.get("merge_properties", True)

    def _to_uuid(v):
        return UUID(v) if isinstance(v, str) else v

    head_node_id = _to_uuid(head_node_id) if head_node_id else None
    tail_node_id = _to_uuid(tail_node_id) if tail_node_id else None
    if isinstance(chunks, list):
        chunks = [ _to_uuid(x) for x in chunks ]

    row = await update_triple(
        session=session,
        triple_id=triple_id,
        created_by=user_id,
        predicate=predicate,
        subject=subject,
        object_=object_,
        properties=properties,
        head_node_id=head_node_id,
        tail_node_id=tail_node_id,
        chunks=chunks,
        merge_properties=bool(merge_properties),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Triple not found or not owned by user")

    return {"message": "updated", "status": "success", "triple": row}

@router.delete("/{triple_id}")
async def delete_triple_api(
    triple_id: UUID,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    ok = await delete_triple(session, user_id=user_id, triple_id=triple_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Triple not found")
    return {"message": "deleted", "status": "success"}
