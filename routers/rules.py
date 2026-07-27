# routers/rules.py
from typing import Any, Dict, Optional, Union
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Body, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.dependencies import get_pg
from whyhow_api.services.crud.rule_pg import (
    create_rule as pg_create_rule,
    get_workspace_rules as pg_get_workspace_rules,
    get_graph_rules as pg_get_graph_rules,
    delete_rule as pg_delete_rule,
)
from whyhow_api.services.crud.user_pg import get_user_by_api_key
from whyhow_api.schemas.rules import RuleWrapper

router = APIRouter(tags=["Rules"], prefix="/rules")

async def _require_user_id(session: AsyncSession, api_key: str) -> UUID:
    u = await get_user_by_api_key(session, api_key)
    if not u:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return u["id"]

def _xor(a: Optional[Any], b: Optional[Any]) -> bool:
    return (a is None) ^ (b is None)

@router.post("")
async def create_rule_endpoint(
    workspace_id: Optional[UUID] = Query(None, description="工作区ID（二选一）"),
    graph_id: Optional[UUID] = Query(None, description="图ID（二选一）"),
    payload: Union[Dict[str, Any], RuleWrapper] = Body(..., description="规则 JSON，可以是裸对象或 {'rule': {...}}"),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    if not _xor(workspace_id, graph_id):
        raise HTTPException(status_code=400, detail="workspace_id 与 graph_id 必须二选一，且只能提供一个。")

    user_id = await _require_user_id(session, api_key)

    rule_body: Dict[str, Any]
    if isinstance(payload, dict):
        rule_body = payload
    else:
        rule_body = payload.rule

    new_rule = await pg_create_rule(
        session=session,
        user_id=user_id,
        rule_body=rule_body,
        workspace_id=workspace_id,
        graph_id=graph_id,
    )
    return {"message": "Rule created successfully.", "status": "success", "count": 1, "rules": [new_rule]}

@router.get("")
async def read_rules_endpoint(
    workspace_id: Optional[UUID] = Query(None, description="工作区ID（二选一）"),
    graph_id: Optional[UUID] = Query(None, description="图ID（二选一）"),
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=-1, le=200),
    order: int = Query(-1),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    """查询规则（PG，使用 API Key 识别用户）"""
    if not _xor(workspace_id, graph_id):
        raise HTTPException(status_code=400, detail="workspace_id 与 graph_id 必须二选一，且只能提供一个。")

    user_id = await _require_user_id(session, api_key)

    if workspace_id:
        rules = await pg_get_workspace_rules(
            session, user_id=user_id, workspace_id=workspace_id, skip=skip, limit=limit, order=order
        )
    else:
        rules = await pg_get_graph_rules(
            session, graph_id=graph_id, user_id=user_id, skip=skip, limit=limit, order=order
        )

    return {"message": "Rules retrieved successfully.", "status": "success", "count": len(rules), "rules": rules}

@router.delete("/{rule_id}")
async def delete_rule_endpoint(
    rule_id: UUID,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    """删除规则（PG，使用 API Key 识别用户）"""
    user_id = await _require_user_id(session, api_key)
    ok = await pg_delete_rule(session, user_id=user_id, rule_id=rule_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found.")
    return {"message": "Rule deleted successfully.", "status": "success", "count": 1}
