"""Chunks router (openGauss/PostgreSQL, API-key auth)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Body,
    Header,
    UploadFile,
    File,
)

from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.dependencies import get_pg, get_llm_client, LLMClient
from whyhow_api.services.crud.user_pg import get_user_by_api_key

# ---- schemas (Pydantic) ----
from whyhow_api.schemas.chunks import (
    AddChunkModel,
    UpdateChunkModel,
    ChunkOut,
    ChunksOutWithWorkspaceDetails,
    ChunkDocumentModel,
)
from whyhow_api.schemas.base import File_Extensions

# ---- services (you provided in services/crud/chunks_pg.py) ----
from whyhow_api.services.crud.chunks_pg import (
    get_chunks,
    get_chunk_basic,
    prepare_chunks,
    add_chunks,
    process_chunks,
    assign_chunks_to_workspace,
    unassign_chunks_from_workspace,
    update_chunk as svc_update_chunk,
    delete_chunk as svc_delete_chunk,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Chunks"], prefix="/chunks")


# ---- common auth ----
async def _require_user_id(session: AsyncSession, api_key: str) -> UUID:
    u = await get_user_by_api_key(session, api_key)
    if not u:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return u["id"]


# ==============================
#            READ
# ==============================

@router.get("")
async def list_chunks(
    workspace_id: Optional[UUID] = Query(None, description="按 workspace 过滤"),
    data_type: Optional[str] = Query(None, description="string|object 等"),
    document_id: Optional[UUID] = Query(None, description="按 document 过滤"),
    chunk_id: Optional[UUID] = Query(None, alias="_id", description="按 chunk id 过滤"),
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=-1, le=200),
    order: int = Query(1, description="1 升序, -1 降序"),
    populate: bool = Query(True, description="是否展开 workspace/document 信息"),
    include_embeddings: bool = Query(True, description="返回是否包含 embedding（通常 False）"),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)

    filters: Dict[str, Any] = {}
    if workspace_id:
        filters["workspaces"] = workspace_id
    if data_type:
        filters["data_type"] = data_type
    if document_id:
        filters["document_id"] = document_id
    if chunk_id:
        filters["_id"] = chunk_id

    rows = await get_chunks(
        session=session,
        user_id=user_id,
        llm_client=None,
        include_embeddings=include_embeddings,
        filters=filters,
        skip=skip,
        limit=limit,
        order=order,
        populate=populate,
    )
    return {
        "message": "ok",
        "status": "success",
        "count": len(rows or []),
        "chunks": rows or [],
    }


@router.get("/{chunk_id}")
async def get_chunk(
    chunk_id: UUID,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    row = await get_chunk_basic(session, chunk_id=chunk_id, user_id=user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return {"message": "ok", "status": "success", "chunk": row}


# ==============================
#            CREATE
# ==============================

@router.post("")
async def add_chunks_manual(
    workspace_id: UUID = Query(..., description="目标 workspace"),
    chunks_in: List[AddChunkModel] = Body(..., embed=True),
    session: AsyncSession = Depends(get_pg),
    llm_client: LLMClient = Depends(get_llm_client),
    api_key: str = Header(..., alias="x-api-key"),
):
    """
    手动添加（字符串/对象）Chunks：
    - 先用 prepare_chunks 组装为 ChunkDocumentModel（含 tags/user_metadata 以 workspace 维度存储）
    - 再调用 add_chunks 计算 embedding 并入库
    """
    user_id = await _require_user_id(session, api_key)
    prepared = prepare_chunks(chunks_in, workspace_id, user_id)
    inserted = await add_chunks(session, llm_client, prepared)
    return {"message": "created", "status": "success", "count": len(inserted), "chunks": inserted}


@router.post("/upload")
async def upload_file_to_chunks(
    workspace_id: UUID = Query(...),
    document_id: UUID = Query(...),
    extension: File_Extensions = Query(..., description="csv|json|pdf|txt"),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_pg),
    llm_client: LLMClient = Depends(get_llm_client),
    api_key: str = Header(..., alias="x-api-key"),
):
    """
    上传文件并自动切块（csv/json/pdf/txt）。
    内部会根据扩展名走结构化或非结构化处理，然后 add_chunks 写入。
    """
    _ = await _require_user_id(session, api_key)
    content = await file.read()
    await process_chunks(
        session=session,
        content=content,
        document_id=document_id,
        llm_client=llm_client,
        workspace_id=workspace_id,
        user_id=_,  # 当前用户
        extension=extension,
    )
    return {"message": "uploaded", "status": "success"}


# ==============================
#        ASSIGN / UNASSIGN
# ==============================

@router.post("/assign")
async def assign_chunks(
    workspace_id: UUID = Query(...),
    chunk_ids: List[UUID] = Body(..., embed=True),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    result = await assign_chunks_to_workspace(session, chunk_ids, workspace_id, user_id)
    return {"message": "ok", "status": "success", "result": result}


@router.post("/unassign")
async def unassign_chunks(
    workspace_id: UUID = Query(...),
    chunk_ids: List[UUID] = Body(..., embed=True),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    result = await unassign_chunks_from_workspace(session, chunk_ids, workspace_id, user_id)
    return {"message": "ok", "status": "success", "result": result}


# ==============================
#        UPDATE / DELETE
# ==============================

@router.patch("/{chunk_id}")
async def update_chunk(
    chunk_id: UUID,
    workspace_id: UUID = Query(..., description="要写入 tags/user_metadata 的 workspace"),
    body: UpdateChunkModel = Body(...),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    msg, rows = await svc_update_chunk(session, chunk_id, workspace_id, body, user_id)
    return {"message": msg, "status": "success", "count": len(rows or []), "chunks": rows or []}


@router.delete("/{chunk_id}")
async def delete_chunk(
    chunk_id: UUID,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    row = await svc_delete_chunk(session, chunk_id, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return {"message": "deleted", "status": "success", "chunk": row}

@router.post("/embeddings/rebuild")
async def rebuild_embeddings_endpoint(
    workspace_id: UUID | None = Query(None),
    chunk_ids: str | None = Query(None),
    force: bool = Query(False),
    session: AsyncSession = Depends(get_pg),
    llm_client: LLMClient = Depends(get_llm_client),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    ids = [UUID(s.strip()) for s in (chunk_ids or "").split(",") if s.strip()] or None

    from whyhow_api.services.crud.chunks_pg import rebuild_chunk_embeddings
    result = await rebuild_chunk_embeddings(
        session,
        user_id=user_id,
        llm_client=llm_client,
        workspace_id=workspace_id,
        chunk_ids=ids,
        force=force,
    )
    return {"message": "embeddings rebuilt", "status": "success", **result}

