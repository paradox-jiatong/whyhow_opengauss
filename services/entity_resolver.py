"""Entity normalization and provenance-aware triple deduplication."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from whyhow_api.models.common import Triple
from whyhow_api.services.schema_extractor import SchemaExtractionResult

_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")


def normalize_entity_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return " ".join(_WORD_RE.findall(normalized)).strip()


def _append_unique(values: list[str], incoming: list[str]) -> list[str]:
    out = list(values)
    for item in incoming:
        if item not in out:
            out.append(item)
    return out


class EntityResolver:
    def __init__(self, schema_body: dict[str, Any] | None = None) -> None:
        self.schema_body = schema_body or {}
        self.alias_to_canonical = self._load_aliases(self.schema_body)

    def _load_aliases(self, schema_body: dict[str, Any]) -> dict[tuple[str, str], str]:
        aliases: dict[tuple[str, str], str] = {}
        for entity in schema_body.get("entities") or []:
            entity_type = str(entity.get("name") or "entity")
            raw_aliases = entity.get("aliases") or {}
            if isinstance(raw_aliases, dict):
                for canonical, alias_values in raw_aliases.items():
                    canonical_name = normalize_entity_name(str(canonical))
                    aliases[(entity_type, canonical_name)] = canonical_name
                    for alias in alias_values or []:
                        aliases[(entity_type, normalize_entity_name(str(alias)))] = canonical_name
            elif isinstance(raw_aliases, list):
                for alias in raw_aliases:
                    aliases[(entity_type, normalize_entity_name(str(alias)))] = normalize_entity_name(str(alias))
        return aliases

    def canonicalize(self, name: str, entity_type: str) -> str:
        normalized = normalize_entity_name(name)
        return self.alias_to_canonical.get((entity_type, normalized), normalized)

    def to_triples(self, results: list[SchemaExtractionResult]) -> list[Triple]:
        merged: dict[tuple[str, str, str, str, str], Triple] = {}

        for result in results:
            for extracted in result.triples:
                head = self.canonicalize(extracted.head, extracted.head_type)
                tail = self.canonicalize(extracted.tail, extracted.tail_type)
                relation = normalize_entity_name(extracted.relation) or extracted.relation
                key = (extracted.head_type, head, relation, extracted.tail_type, tail)
                chunks = [extracted.source_chunk_id]

                existing = merged.get(key)
                if existing is None:
                    merged[key] = Triple(
                        head=head,
                        head_type=extracted.head_type,
                        relation=relation,
                        tail=tail,
                        tail_type=extracted.tail_type,
                        head_properties={"chunks": chunks, "confidence": extracted.confidence},
                        relation_properties={"chunks": chunks, "confidence": extracted.confidence},
                        tail_properties={"chunks": chunks, "confidence": extracted.confidence},
                    )
                    continue

                existing.head_properties["chunks"] = _append_unique(existing.head_properties.get("chunks", []), chunks)
                existing.relation_properties["chunks"] = _append_unique(existing.relation_properties.get("chunks", []), chunks)
                existing.tail_properties["chunks"] = _append_unique(existing.tail_properties.get("chunks", []), chunks)
                existing.head_properties["confidence"] = max(existing.head_properties.get("confidence", 0), extracted.confidence)
                existing.relation_properties["confidence"] = max(existing.relation_properties.get("confidence", 0), extracted.confidence)
                existing.tail_properties["confidence"] = max(existing.tail_properties.get("confidence", 0), extracted.confidence)

        return list(merged.values())
