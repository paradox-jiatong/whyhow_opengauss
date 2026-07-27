"""Users router (openGauss version for first-stage)."""
import logging
import re
from uuid import UUID
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.dependencies import get_pg, get_user_pg
from whyhow_api.schemas.users import (
    APIKeyOutModel,
    DeleteUserResponse,
    GetAPIKeyResponse,
    GetProvidersDetailsResponse,
    GetUserStatusResponse,
    Provider,
    ProviderConfig,
    SetProvidersDetailsResponse,
)
from whyhow_api.services.crud.user_pg import (
    get_user_by_api_key,
    rotate_api_key,
    set_providers,
    get_providers,
    get_active,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Users"], prefix="/users")

async def _require_user(
    session: AsyncSession,
    api_key: str
) -> Dict[str, Any]:
    user = await get_user_by_api_key(session, api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return user

@router.get("/api_key")
async def get_api_key(
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    """根据 x-api-key 返回当前用户的 API Key（用于与旧行为保持一致）"""
    user = await _require_user(session, api_key)
    print(f"API Key: {api_key}")
    print(f"User: {user}")
    return {
        "message": "API key retrieved successfully.",
        "status": "success",
        "count": 1,
        "whyhow_api_key": [{"api_key": user["api_key"], "created_at": user["created_at"], "updated_at": user["updated_at"]}],
    }


@router.post("/rotate_api_key")
async def rotate_api_key_endpoint(
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user = await _require_user(session, api_key)
    updated = await rotate_api_key(session, user_id=user["id"])
    return {
        "message": "New API key generated.",
        "status": "success",
        "count": 1,
        "whyhow_api_key": [{"api_key": updated["api_key"], "created_at": updated["created_at"], "updated_at": updated["updated_at"]}],
    }


@router.get("/providers")
async def get_user_providers(
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user = await _require_user(session, api_key)
    providers = await get_providers(session, user_id=user["id"])
    return {"message": "ok", "status": "success", "providers": providers}

@router.put("/providers")
async def put_user_providers(
    payload: List[Dict[str, Any]],
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user = await _require_user(session, api_key)
    updated = await set_providers(session, user_id=user["id"], providers=payload)
    return {"message": "updated", "status": "success", "user": updated}

@router.get("/active")
async def get_user_active(
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user = await _require_user(session, api_key)
    return {"message": "ok", "status": "success", "active": await get_active(session, user_id=user["id"])}