"""Shared retrieval models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Evidence:
    source: str
    text: str
    score: float
    payload: dict[str, Any]


@dataclass(frozen=True)
class RetrievalCandidate:
    route: str
    source: str
    text: str
    score: float
    payload: dict[str, Any]
