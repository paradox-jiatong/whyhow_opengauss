# whyhow_api/models/common.py
"""Shared models."""

from typing import Any, Dict, List, Union

from openai import AsyncAzureOpenAI, AsyncOpenAI
from pydantic import BaseModel, Field, ConfigDict

from whyhow_api.schemas.users import (
    BYOAzureOpenAIMetadata,
    BYOOpenAIMetadata,
    WhyHowOpenAIMetadata,
)
from whyhow_api.config import Settings

settings = Settings()

class LLMClient:
    """LLM client."""

    def __init__(
        self,
        client: AsyncOpenAI | AsyncAzureOpenAI,
        metadata: Union[
            BYOOpenAIMetadata, BYOAzureOpenAIMetadata, WhyHowOpenAIMetadata
        ],
    ) -> None:
        """Initialize the LLM client."""
        self.client = client
        self.metadata = metadata


class Node(BaseModel):
    """Schema for a single node."""
    name: str = Field(..., description="The name of the node.", examples=["Python"])
    label: str | None = Field(
        None,
        description="The label (e.g., person, organization, location).",
        examples=["Programming Language"],
    )
    properties: dict[str, Any] = Field(default_factory=dict, description="Properties of the node.")


class Relation(BaseModel):
    """Schema for a single relationship."""
    label: str
    start_node: Node
    end_node: Node
    properties: dict[str, Any] = Field(default_factory=dict)


class Entity(BaseModel):
    """Schema for a single entity (text required)."""
    text: str = Field(..., examples=["Python"])
    label: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class Triple(BaseModel):
    """Schema for a single triple."""
    head: str = Field(..., min_length=1)
    head_type: str = Field(default="Entity", min_length=1)
    relation: str = Field(..., min_length=1)
    tail: str = Field(..., min_length=1)
    tail_type: str = Field(default="Entity", min_length=1)
    head_properties: Dict[Any, Any] = Field(default={})
    relation_properties: Dict[Any, Any] = Field(default={})
    tail_properties: Dict[Any, Any] = Field(default={})

    def __str__(self) -> str:
        return self.model_dump_json(indent=2)


class EntityField(BaseModel):
    name: str
    properties: List[str] = []


class SchemaEntity(BaseModel):
    name: str
    description: str
    fields: List[EntityField] = Field(default=[])


class SchemaRelation(BaseModel):
    name: str
    description: str


class TriplePattern(BaseModel):
    head: str
    relation: str
    tail: str
    description: str

class GeneratedSchema(BaseModel):
    entities: List[SchemaEntity] = []
    relations: List[SchemaRelation] = []
    patterns: List[TriplePattern] = []
    model_config = ConfigDict(extra="ignore")

class SchemaTriplePattern(BaseModel):
    head: SchemaEntity
    relation: SchemaRelation
    tail: SchemaEntity
    description: str


class StructuredSchemaEntity(BaseModel):
    name: str
    field: EntityField
    properties: List[str] = Field(default_factory=list)


class StructuredSchemaTriplePattern(BaseModel):
    head: StructuredSchemaEntity
    relation: str
    tail: StructuredSchemaEntity


class Schema(BaseModel):
    entities: List[SchemaEntity] = Field(default_factory=list)
    relations: List[SchemaRelation] = Field(default_factory=list)
    patterns: List[Union[SchemaTriplePattern, TriplePattern]] = Field(default_factory=list)

    def get_entity(self, name: str) -> SchemaEntity | None:
        return next((e for e in self.entities if e.name == name), None)

    def get_relation(self, name: str) -> SchemaRelation | None:
        return next((r for r in self.relations if r.name == name), None)

class OpenAICompletionsConfig(BaseModel):
    """OpenAI completions configuration."""

    model: str = Field(default="gpt-4o")
    temperature: float = Field(default=0.1)
    max_tokens: int = Field(default=2000)

class OpenAIDirectivesConfig(BaseModel):
    """OpenAI directives configuration."""

    entity_questions: str = ""
    triple_questions: str = ""
    entity_concepts: str = ""
    triple_concepts: str = ""
    merge_graph: str = ""
    specific_query: str = ""
    improve_matched_relations: str = ""
    improve_matched_entities: str = ""

class MasterOpenAICompletionsConfig(BaseModel):
    """Master OpenAI completions configuration."""

    default: OpenAICompletionsConfig
    entity: OpenAICompletionsConfig
    triple: OpenAICompletionsConfig
    entity_questions: OpenAICompletionsConfig
    triple_questions: OpenAICompletionsConfig
    entity_concepts: OpenAICompletionsConfig
    triple_concepts: OpenAICompletionsConfig
    merge_graph: OpenAICompletionsConfig

class DatasetModel(BaseModel):
    """Dataset model."""

    dataset: Union[Dict[str, List[str]], List[str]]


class PDFProcessorConfig(BaseModel):
    """PDF processor configuration."""

    file_path: str = Field(..., description="File path to PDF document")
    chunk_size: int = Field(default=settings.api.max_chars_per_chunk)
    chunk_overlap: int = Field(0)

class TextWithEntities(BaseModel):
    """Text with extracted entities."""

    text: str = Field(
        ...,
        description=(
            "The original text that was analyzed to identify entities. This"
            " text contains one or more entities that have been classified and"
            " extracted."
        ),
    )
    entities: List[Entity] = Field(
        ...,
        description=(
            "A list of entities extracted from the original text. Each entity"
            " is represented as a combination of the entity's surface form and"
            " its classification label."
        ),
    )