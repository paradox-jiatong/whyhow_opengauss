"""Task CRUD router (openGauss)"""
from uuid import UUID
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.dependencies import get_pg
from whyhow_api.services.crud.task_pg import get_task, create_task

router = APIRouter(prefix="/tasks", tags=["tasks"])

@router.get("/{task_id}")
async def get_task_by_id(
    task_id: UUID,
    created_by: UUID = Query(..., description="任务创建者（用户）ID"),
    session: AsyncSession = Depends(get_pg),
):
    """查询单个任务"""
    data = await get_task(session, task_id=task_id, created_by=created_by)
    if not data:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"message": "ok", "status": "success", "task": data}

@router.post("")
async def create_task_api(
    created_by: UUID = Query(..., description="任务创建者（用户）ID"),
    title: str = Query(..., description="任务标题"),
    description: Optional[str] = Query(None, description="任务描述"),
    session: AsyncSession = Depends(get_pg),
):
    """创建任务"""
    task = await create_task(
        session,
        created_by=created_by,
        title=title,
        description=description,
    )
    return {"message": "created", "status": "success", "task": task}
