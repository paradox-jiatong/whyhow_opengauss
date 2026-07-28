"""Schema-guided extraction with structured validation."""

from __future__ import annotations

import json
import asyncio
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from whyhow_api.models.common import LLMClient

logger = logging.getLogger(__name__)

_SUPPORTS_RE = re.compile(
    r"(?P<head>[A-Za-z][A-Za-z0-9_\-\s]*?|[\u4e00-\u9fff]{2,})\s*(?:还)?(?:支持|具备|提供|supports?)\s*(?P<tail>[^。；;,.，]+)",
    re.IGNORECASE,
)
_CAUSE_RE = re.compile(r"(?P<head>[A-Za-z][A-Za-z0-9_\-\s]*?|[\u4e00-\u9fff]{2,})\s*可能原因\s*(?P<tail>[^。；;,.，]+)")
_LEADS_RE = re.compile(r"(?P<head>[A-Za-z][A-Za-z0-9_\-\s]*?|[\u4e00-\u9fff]{2,})\s*导致\s*(?P<tail>[^。；;,.，]+)")


class ExtractedNode(BaseModel):
    name: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)
    aliases: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_chunk_id: str = Field(..., min_length=1)


class ExtractedTriple(BaseModel):
    head: str = Field(..., min_length=1)
    relation: str = Field(..., min_length=1)
    tail: str = Field(..., min_length=1)
    head_type: str = Field(..., min_length=1)
    tail_type: str = Field(..., min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_chunk_id: str = Field(..., min_length=1)


class SchemaExtractionResult(BaseModel):
    nodes: list[ExtractedNode] = Field(default_factory=list)
    triples: list[ExtractedTriple] = Field(default_factory=list)


def _first_pattern(schema_body: dict[str, Any]) -> tuple[str, str, str] | None:
    patterns = schema_body.get("patterns") or []
    if patterns:
        pattern = patterns[0]
        return (
            str(pattern.get("head") or "entity"),
            str(pattern.get("relation") or "related_to"),
            str(pattern.get("tail") or "entity"),
        )
    relations = schema_body.get("relations") or []
    if relations:
        relation = relations[0]
        return (
            str(relation.get("head") or "entity"),
            str(relation.get("name") or "related_to"),
            str(relation.get("tail") or "entity"),
        )
    return None


def _split_tails(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[、,，和及/]", text) if item.strip()]


def deterministic_extract(schema_body: dict[str, Any], chunk_id: str, chunk_text: str) -> SchemaExtractionResult:
    relation_defs = schema_body.get("relations") or []
    patterns = schema_body.get("patterns") or []
    relation_patterns: list[tuple[str, str, str, re.Pattern[str]]] = []

    for pattern in patterns:
        if str(pattern.get("relation")) == "supports":
            relation_patterns.append((str(pattern.get("head") or "entity"), "supports", str(pattern.get("tail") or "entity"), _SUPPORTS_RE))
    for relation_def in relation_defs:
        name = str(relation_def.get("name") or "")
        if name == "supports" and not any(item[1] == "supports" for item in relation_patterns):
            relation_patterns.append((str(relation_def.get("head") or "entity"), name, str(relation_def.get("tail") or "entity"), _SUPPORTS_RE))
        elif name == "可能原因":
            relation_patterns.append((str(relation_def.get("head") or "entity"), name, str(relation_def.get("tail") or "entity"), _CAUSE_RE))
        elif name == "导致":
            relation_patterns.append((str(relation_def.get("head") or "entity"), name, str(relation_def.get("tail") or "entity"), _LEADS_RE))

    if not relation_patterns:
        pattern = _first_pattern(schema_body)
        if pattern is not None:
            relation_patterns.append((*pattern, _SUPPORTS_RE))
    if not relation_patterns:
        return SchemaExtractionResult()

    nodes: dict[tuple[str, str], ExtractedNode] = {}
    triples: list[ExtractedTriple] = []
    seen_triples: set[tuple[str, str, str]] = set()

    for head_type, relation, tail_type, regex in relation_patterns:
        for match in regex.finditer(chunk_text):
            head = match.group("head").strip()
            nodes.setdefault(
                (head_type, head.lower()),
                ExtractedNode(name=head, type=head_type, source_chunk_id=chunk_id),
            )
            for tail in _split_tails(match.group("tail")):
                nodes.setdefault(
                    (tail_type, tail.lower()),
                    ExtractedNode(name=tail, type=tail_type, source_chunk_id=chunk_id),
                )
                key = (head.lower(), relation.lower(), tail.lower())
                if key in seen_triples:
                    continue
                seen_triples.add(key)
                triples.append(
                    ExtractedTriple(
                        head=head,
                        relation=relation,
                        tail=tail,
                        head_type=head_type,
                        tail_type=tail_type,
                        confidence=0.95,
                        source_chunk_id=chunk_id,
                    )
                )

    return SchemaExtractionResult(nodes=list(nodes.values()), triples=triples)


class SchemaGuidedExtractor:
    async def extract(
        self,
        llm_client: LLMClient,
        schema_body: dict[str, Any],
        chunk_id: str,
        chunk_text: str,
        *,
        min_confidence: float = 0.5,
        request_timeout: float = 30.0,
    ) -> SchemaExtractionResult:
        try:
            response = await asyncio.wait_for(
                llm_client.client.chat.completions.create(
                    model=llm_client.metadata.language_model_name or "local-demo",
                    messages=[
                        {
                            "role": "system",
                            "content": "Extract schema-constrained knowledge. Return only JSON matching {nodes, triples}.",
                        },
                        {
                            "role": "user",
                            "content": "SCHEMA_EXTRACT_JSON\n"
                            f"chunk_id={chunk_id}\n"
                            f"schema={json.dumps(schema_body, ensure_ascii=False)}\n"
                            f"chunk={chunk_text}",
                        },
                    ],
                    temperature=0,
                    max_tokens=1200,
                    response_format={"type": "json_object"},
                    timeout=request_timeout,
                ),
                timeout=request_timeout,
            )
            result = SchemaExtractionResult.model_validate_json(response.choices[0].message.content or "{}")
        except (AttributeError, ValidationError, json.JSONDecodeError, Exception) as exc:
            logger.warning("Schema-guided extraction fell back for chunk %s: %s", chunk_id, type(exc).__name__)
            result = deterministic_extract(schema_body, chunk_id, chunk_text)

        return SchemaExtractionResult(
            nodes=[node for node in result.nodes if node.confidence >= min_confidence],
            triples=[triple for triple in result.triples if triple.confidence >= min_confidence],
        )
