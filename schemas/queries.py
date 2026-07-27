"""Query model and schemas (Postgres/UUID)."""

from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field

from whyhow_api.schemas.base import (
    AnnotatedObjectId,
    BaseDocument,
    BaseResponse,
    Status,
)


class QueryDocumentModel(BaseDocument):
    """Query document model（与 PG 行字段对齐）"""

    name: Optional[str] = Field(default=None, description="Optional query name")
    graph: Optional[AnnotatedObjectId] = Field(default=None, alias="graph_id", description="Graph id if associated")
    status: Status = Field(..., description="Query status")
    payload: Dict[str, Any] | None = Field(default=None, description="Raw parameters or query content")


class QueryOut(QueryDocumentModel):
    """Query output model（Router 里直接用 QueryOut.model_validate(row)）"""

    id: AnnotatedObjectId
    created_by: AnnotatedObjectId = Field(..., alias="user_id")

    model_config = ConfigDict(
        use_enum_values=True,
        from_attributes=True,
        arbitrary_types_allowed=True,
        populate_by_name=True,
    )


class QueryResponse(BaseResponse):
    """Queries output response model."""
    queries: list[QueryOut] = []

    model_config = ConfigDict(
        use_enum_values=True,
        from_attributes=True,
        arbitrary_types_allowed=True,
    )
