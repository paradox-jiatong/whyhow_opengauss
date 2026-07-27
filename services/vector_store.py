"""openGauss-backed vector retrieval helpers.

The demo image does not ship with a pgvector-compatible extension, so this
module stores embeddings as ``FLOAT8[]`` and performs cosine distance inside
openGauss. If the vector columns/function are unavailable, callers can fall
back to the legacy JSON embeddings without changing API behavior.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


VECTOR_COLUMN = "embedding_vector"


def normalize_embedding(value: Any) -> list[float]:
    if isinstance(value, dict):
        value = value.get("vector")
    if not isinstance(value, list):
        return []
    out: list[float] = []
    for item in value:
        try:
            out.append(float(item))
        except (TypeError, ValueError):
            return []
    return out


async def ensure_vector_support(session: AsyncSession) -> None:
    """Create vector columns and the SQL distance function when possible."""
    for table_name in ("chunks", "triples"):
        exists = await session.scalar(
            sa.text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = :table_name AND column_name = :column_name
                LIMIT 1
                """
            ),
            {"table_name": table_name, "column_name": VECTOR_COLUMN},
        )
        if not exists:
            await session.execute(sa.text(f"ALTER TABLE {table_name} ADD COLUMN {VECTOR_COLUMN} FLOAT8[]"))

    await session.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION whyhow_cosine_distance(a FLOAT8[], b FLOAT8[])
            RETURNS DOUBLE PRECISION AS $$
            DECLARE
              dot DOUBLE PRECISION := 0;
              norm_a DOUBLE PRECISION := 0;
              norm_b DOUBLE PRECISION := 0;
              i INTEGER;
              upper_bound INTEGER;
            BEGIN
              IF a IS NULL OR b IS NULL THEN
                RETURN 1.0;
              END IF;
              upper_bound := LEAST(array_length(a, 1), array_length(b, 1));
              IF upper_bound IS NULL OR upper_bound = 0 THEN
                RETURN 1.0;
              END IF;
              FOR i IN 1..upper_bound LOOP
                dot := dot + a[i] * b[i];
                norm_a := norm_a + a[i] * a[i];
                norm_b := norm_b + b[i] * b[i];
              END LOOP;
              IF norm_a = 0 OR norm_b = 0 THEN
                RETURN 1.0;
              END IF;
              RETURN 1.0 - (dot / (sqrt(norm_a) * sqrt(norm_b)));
            END;
            $$ LANGUAGE plpgsql IMMUTABLE;
            """
        )
    )
    await session.commit()


async def has_vector_support(session: AsyncSession, table_name: str) -> bool:
    col_exists = await session.scalar(
        sa.text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = :table_name AND column_name = :column_name
            LIMIT 1
            """
        ),
        {"table_name": table_name, "column_name": VECTOR_COLUMN},
    )
    fn_exists = await session.scalar(
        sa.text(
            """
            SELECT 1
            FROM pg_proc
            WHERE proname = 'whyhow_cosine_distance'
            LIMIT 1
            """
        )
    )
    return bool(col_exists and fn_exists)


async def vector_search_chunks(
    session: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    query_vector: list[float],
    top_k: int,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filters = filters or {}
    if not query_vector or not await has_vector_support(session, "chunks"):
        return []

    predicates = [
        "created_by = :user_id",
        f"{VECTOR_COLUMN} IS NOT NULL",
        "CAST(:workspace_id AS UUID) = ANY(workspaces)",
    ]
    params: dict[str, Any] = {
        "user_id": user_id,
        "workspace_id": workspace_id,
        "query_vector": query_vector,
        "top_k": top_k,
    }

    if filters.get("document_id"):
        predicates.append("document_id = CAST(:document_id AS UUID)")
        params["document_id"] = str(filters["document_id"])
    for idx, tag in enumerate(filters.get("tags") or []):
        predicates.append(f"CAST(tags AS TEXT) LIKE :tag_{idx}")
        params[f"tag_{idx}"] = f"%{tag}%"

    stmt = sa.text(
        f"""
        SELECT id, data_type, content, content_obj, document_id,
               1.0 - whyhow_cosine_distance({VECTOR_COLUMN}, CAST(:query_vector AS FLOAT8[])) AS score
        FROM chunks
        WHERE {' AND '.join(predicates)}
        ORDER BY whyhow_cosine_distance({VECTOR_COLUMN}, CAST(:query_vector AS FLOAT8[])) ASC,
                 created_at DESC
        LIMIT :top_k
        """
    )
    return [dict(row) for row in (await session.execute(stmt, params)).mappings().all()]


async def vector_search_triples(
    session: AsyncSession,
    *,
    user_id: UUID,
    graph_id: UUID,
    query_vector: list[float],
    top_k: int,
) -> list[dict[str, Any]]:
    if not query_vector or not await has_vector_support(session, "triples"):
        return []

    stmt = sa.text(
        f"""
        SELECT t.id,
               t.relation_name,
               t.chunks,
               hn.name AS head,
               hn.label AS head_type,
               tn.name AS tail,
               tn.label AS tail_type,
               1.0 - whyhow_cosine_distance(t.{VECTOR_COLUMN}, CAST(:query_vector AS FLOAT8[])) AS score
        FROM triples t
        JOIN nodes hn ON t.head_node_id = hn.id
        JOIN nodes tn ON t.tail_node_id = tn.id
        WHERE t.graph_id = :graph_id
          AND t.created_by = :user_id
          AND t.{VECTOR_COLUMN} IS NOT NULL
        ORDER BY whyhow_cosine_distance(t.{VECTOR_COLUMN}, CAST(:query_vector AS FLOAT8[])) ASC,
                 t.created_at DESC
        LIMIT :top_k
        """
    )
    params = {"user_id": user_id, "graph_id": graph_id, "query_vector": query_vector, "top_k": top_k}
    return [dict(row) for row in (await session.execute(stmt, params)).mappings().all()]
