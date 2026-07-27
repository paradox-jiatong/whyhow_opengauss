"""Lightweight semantic chunking helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SemanticChunk:
    text: str
    metadata: dict[str, str | int] = field(default_factory=dict)


def _split_markdown_blocks(text: str) -> list[tuple[str, str]]:
    current_section = "root"
    current: list[str] = []
    blocks: list[tuple[str, str]] = []

    for line in text.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if heading:
            if current:
                blocks.append((current_section, "\n".join(current).strip()))
                current = []
            current_section = heading.group(2).strip()
            current.append(line.strip())
            continue
        if not line.strip() and current:
            blocks.append((current_section, "\n".join(current).strip()))
            current = []
            continue
        if line.strip():
            current.append(line.strip())

    if current:
        blocks.append((current_section, "\n".join(current).strip()))
    return [(section, block) for section, block in blocks if block]


def _window_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    out: list[str] = []
    start = 0
    step = max(1, max_chars - overlap_chars)
    while start < len(text):
        out.append(text[start:start + max_chars].strip())
        start += step
    return [item for item in out if item]


def semantic_chunk_text(text: str, *, max_chars: int = 800, overlap_chars: int = 120) -> list[SemanticChunk]:
    chunks: list[SemanticChunk] = []
    for section, block in _split_markdown_blocks(text):
        for idx, window in enumerate(_window_text(block, max_chars=max_chars, overlap_chars=overlap_chars)):
            chunks.append(SemanticChunk(text=window, metadata={"section": section, "window": idx}))
    return chunks
