"""Document models and schemas (UUID / PG)."""

from __future__ import annotations
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, AliasChoices

from whyhow_api.schemas.base import (
    AllowedUserMetadataTypes,
    BaseAssignmentModel,
    BaseDocument,
    BaseResponse,
    BaseUnassignmentModel,
    Document_Status,
    ErrorDetails,
    File_Extensions,
)
from whyhow_api.schemas.workspaces import WorkspaceDetails


class DocumentInfo(BaseModel):
    """Document info model for uploads."""
    content: bytes = Field(..., description="Content of the document in bytes")
    filename: str = Field(..., description="Filename of the document")
    content_type: str
    size: int = Field(..., description="Size of the document", examples=[1234])
    tags: list[str] | None = None
    user_metadata: dict[str, dict[str, AllowedUserMetadataTypes | list[AllowedUserMetadataTypes]]] | None = None


class DocumentMetadata(BaseModel):
    """Document metadata model."""
    size: int = Field(..., description="Size of the document", examples=[1234])
    format: File_Extensions = Field(..., description="Format of the document")
    filename: str = Field(..., description="Filename of the document", min_length=1)


class DocumentDocumentModel(BaseDocument):
    """Document document model (PG)."""
    workspaces: list[UUID] = Field(default_factory=list, description="Workspace IDs document is assigned to.")
    status: Document_Status
    errors: list[ErrorDetails] = Field(default=[], description="Errors during processing.")
    metadata: DocumentMetadata
    tags: dict[str, list[str]] = Field(default_factory=dict, description="List of tags")
    user_metadata: dict[str, dict[str, AllowedUserMetadataTypes | list[AllowedUserMetadataTypes]]] = Field(
        default_factory=dict, description="User defined metadata"
    )

class DocumentOut(DocumentDocumentModel):
    """Document response model."""
    id: UUID = Field(..., validation_alias=AliasChoices("_id", "id"), serialization_alias="id")
    created_by: UUID
    workspaces: list[UUID]

    model_config = ConfigDict(use_enum_values=True, from_attributes=True, populate_by_name=True)


class DocumentOutWithWorkspaceDetails(DocumentOut):
    """Document response model with workspace details."""
    workspaces: list[WorkspaceDetails]


class DocumentUpdate(BaseModel):
    """Document model for PUT body."""
    user_metadata: dict[str, AllowedUserMetadataTypes | list[AllowedUserMetadataTypes]] | None = None
    tags: list[str] | None = None


class DocumentDetail(BaseModel):
    """Schema for document details."""
    id: UUID = Field(..., validation_alias=AliasChoices("_id", "id"), serialization_alias="id")
    filename: str = Field(..., description="Filename of the document", min_length=1)


class DocumentsResponse(BaseResponse):
    """Schema for the response body of the documents endpoints."""
    documents: list[DocumentOut] = Field(default_factory=list, description="list of documents")


class DocumentsResponseWithWorkspaceDetails(BaseResponse):
    """Schema for the response body of the documents endpoints with workspace details."""
    documents: list[DocumentOutWithWorkspaceDetails] = Field(default_factory=list, description="list of documents")


class DocumentUnassignments(BaseUnassignmentModel):
    """Document unassignments model."""
    pass


class DocumentAssignments(BaseAssignmentModel):
    """Document assignments model."""
    pass


class DocumentAssignmentResponse(BaseResponse):
    """Response model for the assign documents endpoint."""
    documents: DocumentAssignments


class DocumentUnassignmentResponse(BaseResponse):
    """Response model for the unassign documents endpoint."""
    documents: DocumentUnassignments


class GeneratePresignedRequest(BaseModel):
    """Request model for generating a presigned post."""
    filename: str = Field(..., description="Filename of the document", min_length=1)
    workspace_id: UUID


class GeneratePresignedResponse(BaseModel):
    """Response model for generating a presigned post."""
    url: str
    fields: dict[str, str]


class GeneratePresignedDownloadRequest(BaseModel):
    """Request model for generating a presigned download url."""
    filename: str = Field(..., description="Filename of the document", min_length=1)


class GeneratePresignedDownloadResponse(BaseModel):
    """Response model for generating a presigned download url."""
    url: str = Field(..., description="Download URL of the document", min_length=1)


class DocumentStateErrorsUpdate(BaseModel):
    """Model for updating the state and errors of a document."""
    status: Document_Status
    errors: list[ErrorDetails] = Field(default_factory=list, description="Details about processing errors.")
