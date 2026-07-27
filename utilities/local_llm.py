"""Deterministic local LLM adapter for demos and tests.

This adapter intentionally implements the tiny subset of the OpenAI async client
surface used by the demo: ``embeddings.create`` and ``chat.completions.create``.
It keeps local development independent from external API keys while preserving
the production call shape.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_SUPPORTS_RE = re.compile(
    r"(?P<head>[A-Za-z][A-Za-z0-9_\-\s]*?|[\u4e00-\u9fff]{2,})\s*(?:还)?(?:支持|具备|提供|supports?)\s*(?P<tail>[^。；;,.，]+)",
    re.IGNORECASE,
)


@dataclass
class _EmbeddingItem:
    embedding: list[float]


@dataclass
class _EmbeddingResponse:
    data: list[_EmbeddingItem]


@dataclass
class _Message:
    content: str


@dataclass
class _Choice:
    message: _Message


@dataclass
class _ChatResponse:
    choices: list[_Choice]


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(text)]


def _embed(text: str, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    for token in _tokens(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


class _LocalEmbeddings:
    async def create(self, *, input: list[str], model: str, dimensions: int = 1536, **_: Any) -> _EmbeddingResponse:
        return _EmbeddingResponse(data=[_EmbeddingItem(_embed(text, dimensions)) for text in input])


class _LocalCompletions:
    async def create(self, *, messages: list[dict[str, str]], **_: Any) -> _ChatResponse:
        user_message = next((message.get("content", "") for message in reversed(messages) if message.get("role") == "user"), "")
        if "SCHEMA_EXTRACT_JSON" in user_message:
            chunk_id = re.search(r"chunk_id=([^\n]+)", user_message)
            schema = re.search(r"schema=(.+)\nchunk=", user_message, flags=re.DOTALL)
            chunk = user_message.split("\nchunk=", 1)[-1]
            schema_body = json.loads(schema.group(1)) if schema else {}
            pattern = (schema_body.get("patterns") or [None])[0]
            relation_def = (schema_body.get("relations") or [None])[0]
            source = pattern or relation_def or {}
            head_type = source.get("head") or "entity"
            relation = source.get("relation") or source.get("name") or "related_to"
            tail_type = source.get("tail") or "entity"
            nodes = []
            triples = []
            seen_nodes = set()
            for match in _SUPPORTS_RE.finditer(chunk):
                head = match.group("head").strip()
                tails = [item.strip() for item in re.split(r"[、,，和及/]", match.group("tail")) if item.strip()]
                if (head_type, head.lower()) not in seen_nodes:
                    seen_nodes.add((head_type, head.lower()))
                    nodes.append({"name": head, "type": head_type, "aliases": [], "confidence": 0.95, "source_chunk_id": chunk_id.group(1) if chunk_id else "chunk"})
                for tail in tails:
                    if (tail_type, tail.lower()) not in seen_nodes:
                        seen_nodes.add((tail_type, tail.lower()))
                        nodes.append({"name": tail, "type": tail_type, "aliases": [], "confidence": 0.95, "source_chunk_id": chunk_id.group(1) if chunk_id else "chunk"})
                    triples.append({
                        "head": head,
                        "relation": relation,
                        "tail": tail,
                        "head_type": head_type,
                        "tail_type": tail_type,
                        "confidence": 0.95,
                        "source_chunk_id": chunk_id.group(1) if chunk_id else "chunk",
                    })
            return _ChatResponse(choices=[_Choice(message=_Message(content=json.dumps({"nodes": nodes, "triples": triples}, ensure_ascii=False)))])
        if "RERANK_EVIDENCE_JSON" in user_message:
            rows = re.findall(r"^\s*\d+\.\s+id=([^\s]+)\s+source=([^\s]+).*?text=(.+)$", user_message, flags=re.MULTILINE)
            question_match = re.search(r"Question:\s*(.+)", user_message)
            question_terms = set(_tokens(question_match.group(1) if question_match else ""))

            def score(row: tuple[str, str, str]) -> tuple[int, int]:
                source_weight = {"triple": 5, "path": 5, "node": 2, "chunk": 0}.get(row[1], 0)
                overlap = len(question_terms & set(_tokens(row[2])))
                return overlap + source_weight, source_weight

            ids = [row[0] for row in sorted(rows, key=score, reverse=True)]
            payload = {"ranked_ids": ids, "reasons": {item_id: "local deterministic rerank" for item_id in ids}, "confidence": 0.8}
            return _ChatResponse(choices=[_Choice(message=_Message(content=json.dumps(payload, ensure_ascii=False)))])
        context = user_message.split("可用上下文：", 1)[-1].strip() if "可用上下文：" in user_message else user_message
        first_context_line = next((line.strip("- ").strip() for line in context.splitlines() if line.strip()), "")
        answer = first_context_line or "无法确定。"
        return _ChatResponse(choices=[_Choice(message=_Message(content=answer))])


class _LocalChat:
    def __init__(self) -> None:
        self.completions = _LocalCompletions()


class LocalLLMClient:
    def __init__(self) -> None:
        self.embeddings = _LocalEmbeddings()
        self.chat = _LocalChat()
