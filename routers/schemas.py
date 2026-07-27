"""Schemas router (openGauss/PostgreSQL)"""

import logging
from typing import Optional, Dict, Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Body, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.dependencies import get_pg, get_llm_client, LLMClient
from whyhow_api.services.crud.schema_pg import (
    list_schemas,
    get_schema,
    create_schema,
    delete_schema,
)
from whyhow_api.services.crud.user_pg import get_user_by_api_key
from whyhow_api.utilities.builders import OpenAIBuilder

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Schemas"], prefix="/schemas")


async def _require_user_id(session: AsyncSession, api_key: str) -> UUID:
    user = await get_user_by_api_key(session, api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return user["id"]


@router.get("")
async def read_schemas_endpoint(
    workspace_id: UUID = Query(..., description="工作区ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=-1, le=200),
    order: int = Query(-1),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    """列出当前用户在指定 workspace 下的 schemas（PG）"""
    user_id = await _require_user_id(session, api_key)
    schemas = await list_schemas(
        session, user_id=user_id, workspace_id=workspace_id,
        skip=skip, limit=limit, order=order
    )
    return {
        "message": "Schemas retrieved successfully.",
        "status": "success",
        "count": len(schemas),
        "schemas": schemas,
    }


@router.get("/{schema_id}")
async def read_schema_endpoint(
    schema_id: UUID,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    """获取单个 schema（PG）"""
    user_id = await _require_user_id(session, api_key)
    s = await get_schema(session, schema_id=schema_id, user_id=user_id)
    if not s:
        raise HTTPException(status_code=404, detail="Schema not found")
    return {
        "message": "Schema retrieved successfully.",
        "status": "success",
        "count": 1,
        "schemas": [s],
    }


@router.post("")
async def create_schema_endpoint(
    workspace_id: UUID = Query(...),
    name: Optional[str] = Query(None),
    body: Optional[Dict[str, Any]] = Body(None),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    """创建 schema（PG）"""
    user_id = await _require_user_id(session, api_key)
    s = await create_schema(
        session, user_id=user_id, workspace_id=workspace_id, name=name, body=body
    )
    return {
        "message": "Schema created successfully.",
        "status": "success",
        "count": 1,
        "schemas": [s],
    }


@router.put("/{schema_id}")
async def update_schema_endpoint(
    schema_id: UUID,
    name: Optional[str] = Query(None),
    body: Optional[Dict[str, Any]] = Body(None),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    """更新 schema（PG）"""
    from whyhow_api.services.crud.schema_pg import schemas as schemas_tbl

    user_id = await _require_user_id(session, api_key)

    values: Dict[str, Any] = {}
    if name is not None:
        values["name"] = name
    if body is not None:
        values["body"] = body
    if not values:
        raise HTTPException(status_code=400, detail="Nothing to update")

    stmt = (
        sa.update(schemas_tbl)
        .where(schemas_tbl.c.id == schema_id, schemas_tbl.c.created_by == user_id)
        .values(**values)
        .returning(*schemas_tbl.c)
    )
    row = (await session.execute(stmt)).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Schema not found")
    await session.commit()
    return {
        "message": "Schema updated successfully.",
        "status": "success",
        "count": 1,
        "schemas": [dict(row)],
    }


@router.delete("/{schema_id}")
async def delete_schema_endpoint(
    schema_id: UUID,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    """删除 schema（PG）"""
    user_id = await _require_user_id(session, api_key)
    ok = await pg_delete_schema(session, user_id=user_id, schema_id=schema_id)
    if not ok:
        # 被图引用时返回 409
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete schema with associated graphs.",
        )
    return {"message": "Schema deleted successfully.", "status": "success", "count": 1}


@router.post("/generate")
async def generate_schema_endpoint(
    questions: list[str] = Body(..., embed=True),
    llm_client: LLMClient = Depends(get_llm_client),
):
    """用 LLM 生成 schema（无 DB 依赖）"""
    generated_schema, errors = await OpenAIBuilder.generate_schema(
        llm_client=llm_client,
        questions=questions,
    )
    return {
        "message": "Schema generated successfully.",
        "status": "success",
        "questions": questions,
        "generated_schema": generated_schema,
        "errors": errors,
    }
