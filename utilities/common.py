# whyhow_api/utilities/common.py
"""Common utilities (Postgres/UUID)."""

from __future__ import annotations
import logging
import string
from collections import defaultdict
from typing import Any, DefaultDict, Dict, List, Mapping, Sequence, Set, Tuple
from uuid import UUID

import logfire
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.exceptions import NotFoundException
from whyhow_api.models.common import LLMClient
from whyhow_api.schemas.graphs import Triple

logger = logging.getLogger(__name__)


async def embed_texts(
    llm_client: LLMClient, texts: list[str], batch_size: int = 2048
) -> List[Any]:
    """Embed a list of texts using the OpenAI API (no DB dependency)."""
    logfire.instrument_openai(llm_client.client)

    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        logger.info(f"Processing batch from {i} to {i + batch_size}")

        if len(batch) > 2048:
            raise RuntimeError("Texts must be 2048 items or less.")

        response = await llm_client.client.embeddings.create(
            input=batch,
            model=(llm_client.metadata.embedding_name or "text-embedding-3-small"),
            dimensions=1536,
        )
        all_embeddings.extend([d.embedding for d in response.data])

    logger.info(f"Finished processing {len(texts)} texts.")
    return all_embeddings


def compress_triples(triples: List[Tuple[str, str, str]]) -> str:
    """Compress triples into compact lines."""
    structured: DefaultDict[Any, DefaultDict[Any, Set[str]]] = defaultdict(lambda: defaultdict(set))
    for head, relation, tail in triples:
        relation = relation.replace("_", " ").lower()
        structured[head][relation].add(tail)

    out: list[str] = []
    for head, relations in sorted(structured.items()):
        for rel, tails in sorted(relations.items()):
            out.append(f"{head} {rel} {', '.join(sorted(tails))}")
    return "\n".join(out)


def create_chunk_triples(triples: List[Triple], chunk_map: Dict[str, str]) -> List[Triple]:
    """Create linked triples per chunk id."""
    if not triples:
        return []
    processed: dict[tuple[str, str], Dict[str, int]] = {}

    identifiers = set((t.head, t.head_type) for t in triples) | set((t.tail, t.tail_type) for t in triples)
    for identifier, type_name in identifiers:
        processed[(identifier, type_name)] = count_frequency(search_str=identifier, data_dict=chunk_map)

    linked: List[Triple] = []
    for identifier, type_name in identifiers:
        for chunk_id, freq in processed[(identifier, type_name)].items():
            linked.append(
                Triple(
                    head=chunk_id,
                    head_type="Chunk",
                    tail=identifier,
                    tail_type=type_name,
                    relation="Contains",
                    head_properties={"id": chunk_id, "text": chunk_map[chunk_id]},
                    relation_properties={"count": freq},
                )
            )
    return linked


def remove_punctuation(text: str) -> str:
    translator = str.maketrans("", "", string.punctuation)
    return text.translate(translator)


def count_frequency(search_str: str, data_dict: Dict[str, str]) -> Dict[str, int]:
    """Count mentions of search_str across texts (case-insensitive & punctuation-agnostic)."""
    freq: Dict[str, int] = {}
    normalized = remove_punctuation(search_str.lower())
    for _id, text in data_dict.items():
        t = remove_punctuation(str(text).lower())
        occ = t.count(normalized)
        if occ > 0:
            freq[_id] = freq.get(_id, 0) + occ
    return freq


def clean_text(text: str) -> str:
    allowed = {",", ";", "."}
    return (
        "".join((ch if ch.isalnum() or ch in allowed or ch == " " else " ") for ch in text)
        .replace("_", " ")
        .strip()
    )


# ----------------------
# PG/SQL helpers
# ----------------------
async def check_existing(
    session: AsyncSession,
    table: sa.Table,
    ids: Sequence[UUID] | Sequence[str],
    additional_eq: Mapping[str, Any] | None = None,
) -> list[UUID]:
    """
    检查一组主键是否存在；返回存在的 UUID 列表。

    Parameters
    ----------
    session : AsyncSession
    table   : sa.Table (含 'id' UUID 列)
    ids     : 序列（UUID 或可被 UUID() 解析的字符串）
    additional_eq : 额外等值过滤，如 {"created_by": some_uuid}

    Returns
    -------
    list[UUID] : 已存在的 id
    """
    # 统一成 UUID
    uuid_ids: list[UUID] = [i if isinstance(i, UUID) else UUID(str(i)) for i in ids]
    conds: list[sa.ColumnElement[bool]] = [table.c.id.in_(uuid_ids)]
    if additional_eq:
        for k, v in additional_eq.items():
            col = getattr(table.c, k, None)
            if col is not None:
                conds.append(col == v)

    stmt = sa.select(table.c.id).where(sa.and_(*conds))
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


def require_found(found_count: int, expected_count: int, what: str = "records") -> None:
    """若未全部找到则抛 NotFoundException。"""
    if found_count != expected_count:
        raise NotFoundException(f"{what} not found: expected {expected_count}, found {found_count}")
