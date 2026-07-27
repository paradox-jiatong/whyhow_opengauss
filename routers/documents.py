"""Documents router (openGauss/PostgreSQL)."""

from uuid import UUID
from typing import Any, Dict, List, Optional
import logging

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Body, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.dependencies import get_pg
from whyhow_api.services.crud.document_pg import (
    list_documents,
    get_document,
    create_document,
    update_document_state_errors,
    assign_documents_to_workspace,
    unassign_documents_from_workspace,
)
from whyhow_api.services.crud.user_pg import get_user_by_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Documents"], prefix="/documents")


async def _require_user_id(session: AsyncSession, api_key: str) -> UUID:
    u = await get_user_by_api_key(session, api_key)
    if not u:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return u["id"]


@router.get("")
async def list_docs(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=-1, le=50),
    order: int = Query(1, description="1 升序, -1 降序"),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    docs = await list_documents(session, user_id, skip=skip, limit=limit, order=order)
    return {
        "message": "ok",
        "status": "success",
        "count": len(docs or []),
        "documents": docs or [],
    }


@router.post("")
async def create_doc(
    body: Dict[str, Any] = Body(
        ...,
        example={
            "workspace_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "title": "doc-1",
            "source": "inline",
            "meta": {},
        },
    ),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)

    title: Optional[str] = body.get("title")
    source: Optional[str] = body.get("source")
    meta: Dict[str, Any] = body.get("meta") or {}
    ws_id_raw: Optional[str] = body.get("workspace_id")

    if not title or not source:
        raise HTTPException(status_code=422, detail="title 和 source 不能为空")

    metadata = {"title": title, "source": source, **meta}

    doc = await create_document(
        session=session,
        user_id=user_id,
        status="uploaded",
        metadata=metadata,
        workspace_id=UUID(ws_id_raw) if ws_id_raw else None,
    )

    if ws_id_raw and doc:
        try:
            await assign_documents_to_workspace(
                session=session,
                document_ids=[doc["id"]],
                workspace_id=UUID(ws_id_raw),
                created_by=user_id,
            )
        except Exception:
            logger.exception("assign_documents_to_workspace failed")

    return {"message": "created", "status": "success", "document": doc}


@router.get("/{document_id}")
async def get_doc(
    document_id: UUID,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    doc = await get_document(session, user_id, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"message": "ok", "status": "success", "document": doc}


@router.post("/{document_id}/state")
async def update_state(
    document_id: UUID,
    status_value: Optional[str] = Body(None, embed=True),
    errors: Optional[List[Dict[str, Any]]] = Body(None, embed=True),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    doc = await update_document_state_errors(
        session, user_id, document_id, status_value, errors
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"message": "ok", "status": "success", "document": doc}


@router.post("/assign")
async def assign_docs(
    workspace_id: UUID = Query(...),
    document_ids: List[UUID] = Body(..., embed=True),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    res = await assign_documents_to_workspace(session, document_ids, workspace_id, user_id)
    return {"message": "ok", "status": "success", "documents": res}


@router.post("/unassign")
async def unassign_docs(
    workspace_id: UUID = Query(...),
    document_ids: List[UUID] = Body(..., embed=True),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    res = await unassign_documents_from_workspace(session, document_ids, workspace_id, user_id)
    return {"message": "ok", "status": "success", "documents": res}
