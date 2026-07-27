"""Graph schemas (Postgres/UUID)."""

from typing import Annotated, Any
from annotated_types import Len
from pydantic import BaseModel, ConfigDict, Field, model_validator

from whyhow_api.models.common import Node, Triple
from whyhow_api.schemas.base import (
    AfterAnnotatedObjectId,
    AnnotatedObjectId,
    BaseDocument,
    BaseRequest,
    BaseResponse,
    Chunk_Data_Type,
    ErrorDetails,
    Graph_Status,
)
from whyhow_api.schemas.nodes import NodeWithId, NodeWithIdAndSimilarity
from whyhow_api.schemas.queries import QueryOut
from whyhow_api.schemas.schemas import SchemaOut as SchemaDetails
from whyhow_api.schemas.triples import TripleWithId
from whyhow_api.schemas.workspaces import WorkspaceDetails

class CreateGraphBody(BaseModel):
    """创建图的请求体：与依赖中使用的字段保持一致。"""
    name: str = Field(..., min_length=1, description="Graph name")
    workspace: AfterAnnotatedObjectId
    schema_: AfterAnnotatedObjectId | None = Field(default=None, alias="schema_id")
    
    model_config = ConfigDict(populate_by_name=True)

class CreateGraphFromTriplesBody(BaseRequest):
    """Schema for creating a graph from triples."""

    name: str = Field(..., description="The name of the graph.", min_length=1)
    workspace: AfterAnnotatedObjectId
    schema_: AfterAnnotatedObjectId | None = Field(
        default=None,
        alias="schema",
    )
    triples: Annotated[list[Triple], Len(min_length=1)] = Field(
        ...,
        description="The triples to create the graph from.",
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)
    
class GraphDocumentModel(BaseDocument):
    """Graph document model (UUID-based)."""

    name: str = Field(..., description="Name of the graph", min_length=1)
    workspace: AfterAnnotatedObjectId
    schema_: AfterAnnotatedObjectId | None = Field(None, alias="schema_id")
    status: Graph_Status = Field(..., description="Status of the graph")
    errors: list[ErrorDetails] = Field(
        default=[], description="Details about the error that occurred during graph creation."
    )
    public: bool = Field(False, description="Whether the graph is public or not")

    def __str__(self) -> str:
        return f"""{self.name}
        (id: {self.id},
        workspace: {self.workspace},
        schema: {self.schema_})"""


class DetailedGraphDocumentModel(BaseDocument):
    """Graph document model with resolved workspace/schema (for details)."""

    name: str = Field(..., description="Name of the graph", min_length=1)
    workspace: WorkspaceDetails
    schema_: SchemaDetails = Field(..., alias="schema")
    status: Graph_Status = Field(..., description="Status of the graph")
    public: bool = Field(..., description="Whether the graph is public or not")
    errors: list[ErrorDetails] = Field(
        default=[], description="Details about the error that occurred during graph creation."
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)


class GraphStateErrorsUpdate(BaseModel):
    """Model for updating the state and errors of a graph."""
    status: Graph_Status = Field(..., description="Status of the graph")
    errors: list[ErrorDetails] = Field(
        default=[], description="Details about the error that occurred during graph creation."
    )


class GraphUpdate(BaseModel):
    """Graph model for PUT body."""
    name: str | None = Field(default=None, description="Name of the graph", min_length=1)
    public: bool | None = Field(default=None, description="Whether the graph is public or not")


class GraphOut(GraphDocumentModel):
    """Graph response model (UUID-based)."""

    id: AnnotatedObjectId = Field(..., alias="_id")
    created_by: AnnotatedObjectId
    workspace: AnnotatedObjectId = Field(..., alias="workspace_id")
    schema_: AnnotatedObjectId | None = Field(default=None, alias="schema_id")

    model_config = ConfigDict(
        use_enum_values=True, from_attributes=True, populate_by_name=True
    )


class DetailedGraphOut(DetailedGraphDocumentModel):
    """Graph response model with details."""
    id: AnnotatedObjectId = Field(..., alias="_id")
    created_by: AnnotatedObjectId
    workspace: WorkspaceDetails
    schema_: SchemaDetails = Field(..., alias="schema")
    public: bool = Field(..., description="Whether the graph is public or not")


class GraphBuildRequest(BaseRequest):
    """Build graph request."""
    content: str | None = Field(default=None, description="Optional content for building graph")
    values: list[str] = Field(default=[])
    entities: list[str] = Field(default=[])
    relations: list[str] = Field(default=[])
    include_chunks: bool = Field(default=False)


class GraphChunksResponse(BaseResponse):
    """Graph + chunks response (用于 /graphs/{id}/chunks)."""
    graph: GraphOut
    chunks: list[Any] = []  # 你也可以引入更详细的 ChunkOut schema


class GraphNodesResponse(BaseResponse):
    """Graph + nodes response."""
    graph: GraphOut
    nodes: list[NodeWithId] = []


class GraphTriplesResponse(BaseResponse):
    """Graph + triples response."""
    graph: GraphOut
    triples: list[TripleWithId] = []


class SimilarNodesResponse(BaseResponse):
    """相似节点响应。"""
    nodes: list[NodeWithIdAndSimilarity] = []


class AskGraphRequest(BaseRequest):
    """问答请求体（RAG over graph/chunks）。"""
    question: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    include_chunks: bool = Field(default=False)


class AskGraphResponse(BaseResponse):
    """问答响应。"""
    answer: str | None = None
    nodes: list[NodeWithId] = []
    triples: list[TripleWithId] = []
    chunks: list[Any] = []
