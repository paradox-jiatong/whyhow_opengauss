"""Deterministic local LLM adapter for demos and tests.

This adapter intentionally implements the tiny subset of the OpenAI async client
surface used by the demo: ``embeddings.create`` and ``chat.completions.create``.
It keeps local development independent from external API keys while preserving
the production call shape.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


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
