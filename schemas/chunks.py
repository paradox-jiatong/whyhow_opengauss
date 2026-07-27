"""Data models and schemas for chunks (UUID / PG)."""

from __future__ import annotations
import json
from uuid import UUID
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, AliasChoices
from typing_extensions import Self

from whyhow_api.config import Settings
from whyhow_api.schemas.base import (
    AllowedChunkContentTypes,
    AllowedUserMetadataTypes,
    BaseAssignmentModel,
    BaseDocument,
    BaseResponse,
    BaseUnassignmentModel,
    Chunk_Data_Type,
)
from whyhow_api.schemas.documents import DocumentDetail
from whyhow_api.schemas.workspaces import WorkspaceDetails

settings = Settings()


class ChunkMetadata(BaseModel):
    """Chunk metadata model."""
    language: str = Field(default="en", description="Language of the chunk")
    length: int | None = Field(default=None, description="Length of the chunk in characters/keys")
    size: int | None = Field(default=None, description="Size of the chunk in bytes")
    data_source_type: Literal["manual", "automatic", "external"] | None = Field(default=None)

    index: int | None = None
    page: int | None = None
    start: int | None = None
    end: int | None = None


class ChunkDocumentModel(BaseDocument):
    """Chunk document model (PG)."""

    workspaces: list[UUID] = Field(..., description="list of workspaces chunk is assigned to.")
    document: UUID | None = Field(default=None, description="Document id associated with the chunk")
    data_type: Chunk_Data_Type = Field(..., description="Type of the content in the chunk")
    content: str | dict[str, AllowedChunkContentTypes] = Field(..., description="Content of the chunk", min_length=1)
    embedding: list[float] | None = Field(default=None, description="Embedding of the chunk")
    metadata: ChunkMetadata
    tags: dict[str, list[str]] = Field(default_factory=dict)
    user_metadata: dict[str, dict[str, AllowedUserMetadataTypes | list[AllowedUserMetadataTypes]]] = Field(
        default_factory=dict, description="User defined metadata"
    )


class ChunkOut(ChunkDocumentModel):
    """API Response model."""
    id: UUID = Field(..., validation_alias=AliasChoices("_id", "id"), serialization_alias="id")
    created_by: UUID
    workspaces: list[UUID]  # type: ignore[assignment]
    document: UUID | None = Field(default=None, description="Document id associated with the chunk")
    tags: dict[str, list[str]] | list[str]  # type: ignore[assignment]
    user_metadata: dict[str, dict[str, AllowedUserMetadataTypes | list[AllowedUserMetadataTypes]]] | dict[
        str, AllowedUserMetadataTypes | list[AllowedUserMetadataTypes]
    ]  # type: ignore[assignment]

    model_config = ConfigDict(use_enum_values=True, from_attributes=True)


class ChunksOutWithWorkspaceDetails(ChunkOut):
    """API Response model with workspace details."""
    workspaces: list[WorkspaceDetails]  # type: ignore[assignment]
    document: DocumentDetail | None = None  # type: ignore[assignment]


class PublicChunksOutWithWorkspaceDetails(ChunksOutWithWorkspaceDetails):
    """Public API Response model."""

    @model_validator(mode="after")
    def obfuscate_names(self) -> Self:
        """Obfuscate workspace/document names for public API."""
        for w in self.workspaces:
            w.name = "hidden"
        if self.document:
            self.document.filename = "hidden"
        return self


class AddChunkModel(BaseModel):
    """API POST body model."""
    content: str | dict[str, AllowedChunkContentTypes] = Field(..., description="Content of the chunk", min_length=1)
    user_metadata: dict[str, AllowedUserMetadataTypes | list[AllowedUserMetadataTypes]] | None = None
    tags: list[str] | None = None

    @model_validator(mode="after")
    def check_content_length(self) -> Self:
        """Check if the content exceeds max length."""
        max_length = settings.api.max_chars_per_chunk
        content_to_check = self.content if isinstance(self.content, str) else json.dumps(self.content)
        if len(content_to_check) > max_length:
            raise ValueError(
                f"Content length of {len(content_to_check)} exceeds the maximum allowed length of {max_length}."
            )
        return self


class AddChunksModel(BaseModel):
    """API POST body model for multiple chunks."""
    chunks: list[AddChunkModel]


class UpdateChunkModel(BaseModel):
    """API PUT body model."""
    user_metadata: dict[str, AllowedUserMetadataTypes | list[AllowedUserMetadataTypes]] | None = None
    tags: list[str] | None = None


class ChunksResponse(BaseResponse):
    """Schema for chunks endpoints."""
    chunks: list[ChunkOut]


class ChunksResponseWithWorkspaceDetails(BaseResponse):
    """Schema for chunks endpoints with workspace details."""
    chunks: list[ChunksOutWithWorkspaceDetails] = Field(default_factory=list, description="list of chunks")


class PublicChunksResponseWithWorkspaceDetails(ChunksResponseWithWorkspaceDetails):
    """Schema for public chunks endpoints with workspace details."""
    @model_validator(mode="after")
    def obfuscate_names(self) -> Self:
        for chunk in self.chunks:
            for w in chunk.workspaces:
                w.name = "hidden"
            if chunk.document:
                chunk.document.filename = "hidden"
        return self


class ChunkUnassignments(BaseUnassignmentModel):
    """Chunk unassignments model."""
    pass


class ChunkAssignments(BaseAssignmentModel):
    """Chunk assignments model."""
    pass


class ChunkAssignmentResponse(BaseResponse):
    """Response for assign chunks endpoint."""
    chunks: ChunkAssignments


class ChunkUnassignmentResponse(BaseResponse):
    """Response for unassign chunks endpoint."""
    chunks: ChunkUnassignments


class AddChunksResponse(BaseResponse):
    """Response for add chunks endpoint."""
    chunks: list[ChunkOut]
