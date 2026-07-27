"""Base classes for request, response, and return schemas (Postgres/UUID)."""

from __future__ import annotations
from abc import ABC
from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
)

# --------------------------
# Custom scalar / enums
# --------------------------
def _ensure_str(v: Any) -> str:
    return str(v)

def _validate_uuid(v: str) -> str:
    try:
        UUID(str(v))
        return str(v)
    except Exception as e:
        raise ValueError(f"{v} is not a valid UUID") from e

AnnotatedObjectId = Annotated[str, BeforeValidator(_ensure_str), AfterValidator(_validate_uuid)]
AfterAnnotatedObjectId = Annotated[str, BeforeValidator(_ensure_str), AfterValidator(_validate_uuid)]

AllowedUserMetadataTypes = str | int | bool | float
AllowedChunkContentTypes = str | int | bool | float | None
AllowedPropertyTypes = str | int | bool | float | None

Status = Literal["success", "pending", "failed"]
Graph_Status = Literal["creating", "updating", "ready", "failed"]
Document_Status = Literal["uploaded", "processing", "processed", "failed"]
Chunk_Data_Type = Literal["string", "object"]
Default_Entity_Type = "entity"
Default_Relation_Type = "related_to"
File_Extensions = Literal["csv", "json", "pdf", "txt"]
Rule_Type = Literal["merge_nodes"]
TaskStatus = Literal["pending", "success", "failed"]


def get_utc_now() -> datetime:
    """Get the current time in UTC."""
    return datetime.now(timezone.utc)


class ErrorDetails(BaseModel):
    """Model for holding details about an error or other message types."""

    message: str = Field(
        ...,
        description="The error message detailing what went wrong.",
        min_length=10,
    )
    created_at: datetime = Field(
        default_factory=get_utc_now,
        description="The UTC timestamp when the error was logged.",
    )
    level: Literal["error", "critical"]


class FilterBody(BaseModel):
    """Filter body for query operations."""
    filters: dict[str, Any] | None = None


class DeleteResponseModel(BaseModel):
    """Response model for delete operations."""
    message: str
    status: Status


class BaseDocument(BaseModel):
    """
    Base class for all DB documents (UUID-based).

    说明：
    - 仍保留 _id / created_by 的命名和别名以兼容旧路由/响应模型；
    - 但底层类型和校验已切到 UUID。
    """
    id: AfterAnnotatedObjectId | None = Field(default=None, alias="_id")
    created_at: datetime = Field(default_factory=get_utc_now)
    updated_at: datetime = Field(default_factory=get_utc_now)
    created_by: AfterAnnotatedObjectId

    model_config = ConfigDict(
        populate_by_name=True,
        use_enum_values=True,
        from_attributes=True,
        arbitrary_types_allowed=True,
    )

    @field_validator("id", "created_by", mode="before")
    def _validate_uuid_like(cls, v) -> str | None:  # type: ignore[no-untyped-def]
        if v is None:
            return v
        UUID(str(v))  # will raise if invalid
        return str(v)


class BaseRequest(BaseModel, ABC):
    """Base class for all request schemas."""
    model_config = ConfigDict(extra="forbid")


class BaseResponse(BaseModel, ABC):
    """
    Base class for all response schemas.

    FastAPI 的响应会先过模型解析，所以我们尽量宽松（ignore 额外字段），
    以便向后兼容。
    """
    message: str
    status: Status
    count: int = 0
    model_config = ConfigDict(extra="ignore")


class BaseUnassignmentModel(BaseModel):
    """Base unassignments model."""
    unassigned: list[str]
    not_found: list[str]
    not_found_in_workspace: list[str]


class BaseAssignmentModel(BaseModel):
    """Base assignments model."""
    assigned: list[str]
    not_found: list[str]
    already_assigned: list[str]
