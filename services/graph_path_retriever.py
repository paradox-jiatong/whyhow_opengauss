"""Graph path recall for one-hop and two-hop evidence."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.services.retrieval_models import RetrievalCandidate


def build_path_candidates(rows: list[dict[str, Any]]) -> list[RetrievalCandidate]:
    candidates: list[RetrievalCandidate] = []
    seen: set[str] = set()
    for row in rows:
        if row.get("second_id"):
            text = (
                f'{row["first_head"]} -> {row["first_relation"]} -> {row["middle"]} '
                f'-> {row["second_relation"]} -> {row["second_tail"]}'
            )
            hop = 2
            item_id = f'{row["first_id"]}:{row["second_id"]}'
        else:
            text = f'{row["first_head"]} -> {row["first_relation"]} -> {row["middle"]}'
            hop = 1
            item_id = str(row["first_id"])
        if item_id in seen:
            continue
        seen.add(item_id)
        candidates.append(
            RetrievalCandidate(
                route="graph_path",
                source="path",
                text=text,
                score=1.0 / hop,
                payload={"id": item_id, "hop": hop, "chunks": [str(c) for c in row.get("chunks") or []]},
            )
        )
    return candidates


async def retrieve_graph_paths(
    session: AsyncSession,
    *,
    user_id: UUID,
    graph_id: UUID,
    question: str,
    top_k: int,
    max_hops: int = 2,
) -> list[RetrievalCandidate]:
    tokens = [token for token in question.lower().replace("?", " ").replace("？", " ").split() if token]
    like_terms = [f"%{token}%" for token in tokens[:5]]
    if not like_terms:
        return []

    rows: list[dict[str, Any]] = []
    for term in like_terms:
        one_hop = await session.execute(
            sa.text(
                """
                SELECT t.id AS first_id,
                       hn.name AS first_head,
                       t.relation_name AS first_relation,
                       tn.name AS middle,
                       NULL AS second_id,
                       NULL AS second_relation,
                       NULL AS second_tail,
                       t.chunks AS chunks
                FROM triples t
                JOIN nodes hn ON t.head_node_id = hn.id
                JOIN nodes tn ON t.tail_node_id = tn.id
                WHERE t.graph_id = :graph_id
                  AND t.created_by = :user_id
                  AND (LOWER(hn.name) LIKE :term OR LOWER(tn.name) LIKE :term)
                LIMIT :limit
                """
            ),
            {"graph_id": graph_id, "user_id": user_id, "term": term, "limit": top_k},
        )
        rows.extend(dict(row) for row in one_hop.mappings().all())

        if max_hops < 2:
            continue
        two_hop = await session.execute(
            sa.text(
                """
                SELECT t1.id AS first_id,
                       h1.name AS first_head,
                       t1.relation_name AS first_relation,
                       m.name AS middle,
                       t2.id AS second_id,
                       t2.relation_name AS second_relation,
                       tail2.name AS second_tail,
                       t1.chunks || t2.chunks AS chunks
                FROM triples t1
                JOIN nodes h1 ON t1.head_node_id = h1.id
                JOIN nodes m ON t1.tail_node_id = m.id
                JOIN triples t2 ON t2.head_node_id = m.id
                JOIN nodes tail2 ON t2.tail_node_id = tail2.id
                WHERE t1.graph_id = :graph_id
                  AND t2.graph_id = :graph_id
                  AND t1.created_by = :user_id
                  AND t2.created_by = :user_id
                  AND LOWER(h1.name) LIKE :term
                LIMIT :limit
                """
            ),
            {"graph_id": graph_id, "user_id": user_id, "term": term, "limit": top_k},
        )
        rows.extend(dict(row) for row in two_hop.mappings().all())

    return build_path_candidates(rows)[:top_k]
