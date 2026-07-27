"""Rule models and schemas (Postgres/UUID)."""

from __future__ import annotations
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from whyhow_api.schemas.base import (
    AnnotatedObjectId,
    BaseDocument,
    BaseResponse,
)


class RuleDocumentModel(BaseDocument):
    """Rule document model（对应 PG rules 表）"""

    workspace: Optional[AnnotatedObjectId] = Field(default=None, alias="workspace_id", description="Workspace id")
    graph: Optional[AnnotatedObjectId] = Field(default=None, alias="graph_id", description="Graph id")
    rule: Dict[str, Any] = Field(..., description="Arbitrary rule JSON")


class RuleOut(RuleDocumentModel):
    """API Response model for a rule."""
    id: AnnotatedObjectId
    created_by: AnnotatedObjectId

    model_config = ConfigDict(use_enum_values=True, from_attributes=True, populate_by_name=True)


class RulesResponse(BaseResponse):
    """Schema for the response body of the rules endpoints."""
    rules: list[RuleOut] = []

class RuleWrapper(BaseModel):
    rule: Dict[str, Any]