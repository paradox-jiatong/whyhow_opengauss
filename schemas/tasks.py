"""Task schemas (Postgres/UUID)."""

from datetime import datetime
from pydantic import ConfigDict, Field

from whyhow_api.schemas.base import (
    AnnotatedObjectId,
    BaseDocument,
    BaseResponse,
    TaskStatus,
    get_utc_now,
)


class TaskDocumentModel(BaseDocument):
    """Task schema (DB-facing)."""
    title: str = Field(..., min_length=1, description="Task title")
    description: str | None = Field(default=None, description="Task description")

    start_time: datetime = Field(default_factory=get_utc_now)
    end_time: datetime | None = None
    status: TaskStatus = Field(..., description="Status of task")
    result: str | None = None


class TaskOut(TaskDocumentModel):
    """Task schema for response."""
    created_by: AnnotatedObjectId = Field(..., alias="user_id")

    model_config = ConfigDict(use_enum_values=True, from_attributes=True, populate_by_name=True)


class TaskResponse(BaseResponse):
    """Task response schema."""
    task: TaskOut
