"""Schema models and schemas (Postgres/UUID)."""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import Self

from whyhow_api.schemas.base import (
    AfterAnnotatedObjectId,
    AnnotatedObjectId,
    BaseDocument,
    BaseResponse,
    ErrorDetails,
)
from whyhow_api.schemas.workspaces import WorkspaceDetails

# --------- 定义 schema 的“业务体”结构（存放在 DB 的 body 字段）---------
class SchemaEntity(BaseModel):
    name: str
    description: str | None = None

class SchemaRelation(BaseModel):
    name: str
    head: str
    tail: str
    description: str | None = None

class TriplePattern(BaseModel):
    head: str
    relation: str
    tail: str
    description: str | None = None

class SchemaBody(BaseModel):
    entities: List[SchemaEntity]
    relations: List[SchemaRelation]
    patterns: List[TriplePattern]

    @model_validator(mode="after")
    def validate_patterns(self) -> Self:
        entity_names = {e.name for e in self.entities}
        relation_names = {r.name for r in self.relations}
        for p in self.patterns:
            if p.head not in entity_names:
                raise ValueError(f"Pattern head '{p.head}' not found in entities.")
            if p.tail not in entity_names:
                raise ValueError(f"Pattern tail '{p.tail}' not found in entities.")
            if p.relation not in relation_names:
                raise ValueError(f"Pattern relation '{p.relation}' not found in relations.")
        return self


# --------------------- DB 背后的文档模型（UUID） ---------------------
class SchemaDocumentModel(BaseDocument):
    """Schema document model（与 PG 的 schemas 表对应）.

    PG 表字段：id, workspace_id, created_by, name, body(JSON), created_at...
    """
    name: str = Field(..., description="Name of the schema", min_length=1)
    workspace: AfterAnnotatedObjectId = Field(..., description="Workspace id associated with the schema", alias="workspace_id")
    body: SchemaBody = Field(..., description="Schema JSON body (entities/relations/patterns)")
    errors: list[ErrorDetails] = Field(default=[], description="Processing errors, if any.")

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)


# --------------------- 创建/更新 请求体 ---------------------
class SchemaCreate(BaseModel):
    """API POST body model（路由也支持直接传 body）。"""
    name: str = Field(..., min_length=1)
    workspace: AfterAnnotatedObjectId = Field(..., description="Workspace id associated with the schema")
    entities: List[SchemaEntity]
    relations: List[SchemaRelation]
    patterns: List[TriplePattern]

    @model_validator(mode="after")
    def validate_sizes(self) -> Self:
        if len(self.entities) < 1:
            raise ValueError("At least one entity must be supplied.")
        if len(self.relations) < 1:
            raise ValueError("At least one relation must be supplied.")
        if len(self.patterns) < 1:
            raise ValueError("At least one pattern must be supplied.")
        return self

    def to_insert_payload(self, created_by: str) -> Dict[str, Any]:
        """统一转换为与 PG schemas 表一致的插入结构（给 services 层用）"""
        return {
            "name": self.name,
            "workspace_id": self.workspace,
            "created_by": created_by,
            "body": SchemaBody(entities=self.entities, relations=self.relations, patterns=self.patterns).model_dump(),
        }


class SchemaUpdate(BaseModel):
    """API PUT body model."""
    name: Optional[str] = Field(default=None, min_length=1)
    body: Optional[SchemaBody] = None


# --------------------- 输出模型 ---------------------
class SchemaOut(SchemaDocumentModel):
    """Schema 响应模型（与 PG 行对齐）"""
    id: AnnotatedObjectId = Field(..., alias="_id")
    workspace: AnnotatedObjectId = Field(..., alias="workspace_id")
    created_by: AnnotatedObjectId

    model_config = ConfigDict(use_enum_values=True, from_attributes=True, populate_by_name=True)


class SchemaOutWithWorkspaceDetails(SchemaOut):
    """带工作区详情的 Schema 输出模型"""
    workspace: WorkspaceDetails  # type: ignore[assignment]


class SchemasResponse(BaseResponse):
    """schemas 列表响应"""
    schemas: list[SchemaOut] = []


class SchemasResponseWithWorkspaceDetails(BaseResponse):
    """带工作区详情的 schemas 列表响应"""
    schemas: list[SchemaOutWithWorkspaceDetails] = Field(default=[], description="list of schemas")


# --------------------- 生成 Schema（LLM） ---------------------
class GeneratedSchema(BaseModel):
    """用于 /schemas/generate 响应的最小结构"""
    entities: List[SchemaEntity]
    relations: List[SchemaRelation]
    patterns: List[TriplePattern]

class GeneratedSchemaResponse(BaseResponse):
    """Response of the generated schema."""
    questions: list[str]
    generated_schema: GeneratedSchema
    errors: list[ErrorDetails] = Field(default=[])
