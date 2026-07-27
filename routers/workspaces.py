# whyhow_api/routers/workspaces.py
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.dependencies import get_pg
from whyhow_api.services.crud.user_pg import get_user_by_api_key
from whyhow_api.services.crud.workspace_pg import (
    get_workspaces, get_workspace, delete_workspace, create_workspace, update_workspace
)
from whyhow_api.schemas.workspaces import CreateWorkspaceBody, UpdateWorkspaceBody

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

async def _require_user_id(session: AsyncSession, api_key: str) -> UUID:
    user = await get_user_by_api_key(session, api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return user["id"]

@router.get("")
async def list_workspaces(
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
    skip: int = 0,
    limit: int = 100,
    order: int = -1,
):
    user_id = await _require_user_id(session, api_key)
    rows = await get_workspaces(session, user_id=user_id, skip=skip, limit=limit, order=order)
    return {"message": "ok", "status": "success", "count": len(rows), "workspaces": rows}

@router.post("")
async def create_workspace_api(
    body: CreateWorkspaceBody,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    ws = await create_workspace(session, name=body.name, user_id=user_id)
    return {"message": "created", "status": "success", "workspace": ws}

@router.get("/{workspace_id}")
async def get_workspace_by_id(
    workspace_id: UUID,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    ws = await get_workspace(session, workspace_id=workspace_id, user_id=user_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return {"message": "ok", "status": "success", "workspace": ws}

@router.patch("/{workspace_id}")
async def patch_workspace(
    workspace_id: UUID,
    body: UpdateWorkspaceBody,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)

    ws = await update_workspace(
        session=session,
        workspace_id=workspace_id,
        user_id=user_id,
        name=body.name
    )
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    return {"message": "updated", "status": "success", "workspace": ws}

@router.delete("/{workspace_id}")
async def delete_workspace_by_id(
    workspace_id: UUID,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    ok = await delete_workspace(session, user_id=user_id, workspace_id=workspace_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Workspace not found or already deleted")
    return {"message": "deleted", "status": "success"}
