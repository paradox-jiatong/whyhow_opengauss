"""GraphRAG build and retrieval pipeline for the openGauss demo."""

from __future__ import annotations

import math
import json
import re
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationError
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.models.common import LLMClient, Triple
from whyhow_api.services.crud.graph_pg import graphs, nodes, triples
from whyhow_api.services.entity_resolver import EntityResolver
from whyhow_api.services.graph_service_pg import build_graph_pg
from whyhow_api.services.graph_path_retriever import retrieve_graph_paths
from whyhow_api.services.retrieval_models import Evidence, RetrievalCandidate
from whyhow_api.services.schema_extractor import SchemaGuidedExtractor
from whyhow_api.services.vector_store import ensure_vector_support, normalize_embedding, vector_search_chunks, vector_search_triples
from whyhow_api.utilities.common import embed_texts

_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")
_SUPPORTS_RE = re.compile(
    r"(?P<head>[A-Za-z][A-Za-z0-9_\-]*|[\u4e00-\u9fff]{2,})\s*(?:还)?(?:支持|具备|提供|supports?)\s*(?P<tail>[^。；;,.，]+)",
    re.IGNORECASE,
)


def _normalize_entity(text: str) -> str:
    return " ".join(_WORD_RE.findall(text.lower())).strip()


def _tokens(text: str) -> set[str]:
    return {_normalize_entity(token) for token in _WORD_RE.findall(text) if _normalize_entity(token)}


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else -1.0


def _chunk_text(row: dict[str, Any]) -> str:
    return row.get("content") or str(row.get("content_obj") or "")


def _candidate_key(item: RetrievalCandidate) -> tuple[str, str]:
    return item.source, str(item.payload.get("id") or item.text)


def fuse_rough_recall_candidates(candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
    best: dict[tuple[str, str], RetrievalCandidate] = {}
    routes_by_key: dict[tuple[str, str], set[str]] = {}
    for item in candidates:
        key = _candidate_key(item)
        routes_by_key.setdefault(key, set()).add(item.route)
        current = best.get(key)
        if current is None or item.score > current.score:
            best[key] = item
    route_weight = {"vector_chunk": 0.35, "keyword_chunk": 0.25, "graph_path": 0.45, "predicate_chunk": 0.05}
    merged = [
        RetrievalCandidate(
            route=item.route,
            source=item.source,
            text=item.text,
            score=item.score,
            payload={**item.payload, "routes": sorted(routes_by_key.get(key, {item.route}))},
        )
        for key, item in best.items()
    ]
    return sorted(merged, key=lambda item: item.score + route_weight.get(item.route, 0.0), reverse=True)


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


async def extract_schema_guided_triples(schema_body: dict[str, Any], chunks_in: list[dict[str, Any]]) -> list[Triple]:
    """Extract triples from chunks using the schema relation pattern.

    The local implementation is intentionally deterministic for demos. In
    production this boundary can call an LLM with JSON schema/function calling
    and keep the persistence/retrieval pipeline unchanged.
    """
    pattern = _first_pattern(schema_body)
    if pattern is None:
        return []

    head_type, relation, tail_type = pattern
    seen: set[tuple[str, str, str]] = set()
    out: list[Triple] = []

    for chunk in chunks_in:
        chunk_id = str(chunk["id"])
        content = str(chunk.get("content") or chunk.get("content_obj") or "")
        for match in _SUPPORTS_RE.finditer(content):
            head = _normalize_entity(match.group("head"))
            tails = re.split(r"[、,，和及/]", match.group("tail"))
            for tail_raw in tails:
                tail = _normalize_entity(tail_raw)
                if not head or not tail:
                    continue
                key = (head, relation, tail)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    Triple(
                        head=head,
                        head_type=head_type,
                        relation=relation,
                        tail=tail,
                        tail_type=tail_type,
                        head_properties={"chunks": [chunk_id]},
                        relation_properties={"chunks": [chunk_id]},
                        tail_properties={"chunks": [chunk_id]},
                    )
                )

    return out


def rerank_evidence(question: str, items: list[Evidence], top_k: int) -> list[Evidence]:
    question_terms = _tokens(question)

    def score(item: Evidence) -> float:
        overlap = len(question_terms & _tokens(item.text))
        source_weight = {"triple": 1.0, "node": 0.35, "chunk": 0.0}.get(item.source, 0.0)
        return overlap + source_weight + item.score

    return sorted(items, key=score, reverse=True)[:top_k]


class RerankResult(BaseModel):
    ranked_ids: list[str] = Field(default_factory=list)
    reasons: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


def _parse_rerank_result(content: str) -> RerankResult:
    parsed = json.loads(content or "{}")
    if isinstance(parsed, list):
        return RerankResult(ranked_ids=[str(item) for item in parsed], confidence=0.5)
    return RerankResult.model_validate(parsed)


async def llm_rerank_evidence(
    llm_client: LLMClient,
    *,
    question: str,
    items: list[Evidence],
    top_k: int,
) -> list[Evidence]:
    pre_ranked = rerank_evidence(question, items, top_k=min(len(items), max(top_k * 4, top_k)))
    if not pre_ranked:
        return []

    id_to_item = {str(item.payload.get("id") or idx): item for idx, item in enumerate(pre_ranked)}
    lines = [
        f"{idx}. id={item_id} source={item.source} route={item.payload.get('route')} text={item.text[:500]}"
        for idx, (item_id, item) in enumerate(id_to_item.items(), start=1)
    ]
    try:
        response = await llm_client.client.chat.completions.create(
            model=llm_client.metadata.language_model_name or "local-demo",
            messages=[
                {"role": "system", "content": "Rerank evidence for a RAG answer. Return only a JSON array of ids."},
                {
                    "role": "user",
                    "content": "RERANK_EVIDENCE_JSON\n"
                    f"Question: {question}\n"
                    "Prefer evidence that directly answers the question and is grounded in graph triples when relevant.\n"
                    + "\n".join(lines),
                },
            ],
        )
        result = _parse_rerank_result(response.choices[0].message.content or "{}")
        out: list[Evidence] = []
        for item_id in result.ranked_ids:
            item = id_to_item.get(str(item_id))
            if item and item not in out:
                payload = {**item.payload}
                if result.reasons.get(str(item_id)):
                    payload["rerank_reason"] = result.reasons[str(item_id)]
                payload["rerank_confidence"] = result.confidence
                out.append(Evidence(item.source, item.text, item.score, payload))
            if len(out) >= top_k:
                break
        for item in pre_ranked:
            if item not in out:
                out.append(item)
            if len(out) >= top_k:
                break
        return out[:top_k]
    except (json.JSONDecodeError, ValidationError, Exception):
        return pre_ranked[:top_k]


async def build_graph_from_workspace_chunks(
    session: AsyncSession,
    llm_client: LLMClient,
    *,
    user_id: UUID,
    workspace_id: UUID,
    schema_id: UUID,
    graph_name: str,
    chunk_limit: int = 200,
) -> dict[str, Any]:
    await ensure_vector_support(session)
    schema_row = (await session.execute(
        sa.text("SELECT body FROM schemas WHERE id = :schema_id AND created_by = :user_id"),
        {"schema_id": schema_id, "user_id": user_id},
    )).mappings().first()
    if not schema_row:
        raise ValueError("Schema not found")

    graph_row = (await session.execute(
        graphs.insert()
        .values(
            id=uuid4(),
            schema_id=schema_id,
            workspace_id=workspace_id,
            created_by=user_id,
            public=False,
            name=graph_name,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
        .returning(*graphs.c)
    )).mappings().one()
    await session.commit()

    ws_bind = sa.bindparam("ws_id", workspace_id)
    rows = (await session.execute(
        sa.select(sa.text("id"), sa.text("content"), sa.text("content_obj"))
        .select_from(sa.text("chunks"))
        .where(sa.text("created_by = :user_id"))
        .where(sa.cast(ws_bind, sa.dialects.postgresql.UUID(as_uuid=True)) == sa.any_(sa.column("workspaces")))
        .limit(chunk_limit),
        {"user_id": user_id, "ws_id": workspace_id},
    )).mappings().all()

    extraction_results = []
    extractor = SchemaGuidedExtractor()
    schema_body = schema_row["body"] or {}
    for row in rows:
        extraction_results.append(
            await extractor.extract(
                llm_client=llm_client,
                schema_body=schema_body,
                chunk_id=str(row["id"]),
                chunk_text=row["content"] or str(row["content_obj"] or ""),
            )
        )
    extracted = EntityResolver(schema_body).to_triples(extraction_results)
    if extracted:
        await build_graph_pg(session=session, llm_client=llm_client, graph_id=graph_row["id"], user_id=user_id, triples_in=extracted)

    node_count = await session.scalar(sa.select(sa.func.count()).select_from(nodes).where(nodes.c.graph_id == graph_row["id"])) or 0
    triple_count = await session.scalar(sa.select(sa.func.count()).select_from(triples).where(triples.c.graph_id == graph_row["id"])) or 0

    return {
        "graph": dict(graph_row),
        "chunks_scanned": len(rows),
        "triples_extracted": len(extracted),
        "nodes_written": int(node_count),
        "triples_written": int(triple_count),
    }


async def query_graph(
    session: AsyncSession,
    llm_client: LLMClient,
    *,
    user_id: UUID,
    graph_id: UUID,
    question: str,
    top_k: int,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    await ensure_vector_support(session)
    graph_row = (await session.execute(
        sa.select(graphs).where(graphs.c.id == graph_id, graphs.c.created_by == user_id)
    )).mappings().first()
    if not graph_row:
        raise ValueError("Graph not found")

    qv = (await embed_texts(llm_client=llm_client, texts=[question]))[0]
    filters = filters or {}
    workspace_id = graph_row["workspace_id"]
    ws_bind = sa.bindparam("ws_id", workspace_id)
    candidates: list[RetrievalCandidate] = []

    for row in await vector_search_chunks(
        session,
        user_id=user_id,
        workspace_id=workspace_id,
        query_vector=normalize_embedding(qv),
        top_k=top_k * 2,
        filters=filters,
    ):
        candidates.append(
            RetrievalCandidate(
                route="vector_chunk",
                source="chunk",
                text=_chunk_text(row),
                score=float(row.get("score") or 0.0),
                payload={"id": str(row["id"])},
            )
        )

    chunk_rows = (await session.execute(
        sa.select(sa.text("id"), sa.text("content"), sa.text("content_obj"), sa.text("embedding"))
        .select_from(sa.text("chunks"))
        .where(sa.text("created_by = :user_id"))
        .where(sa.cast(ws_bind, sa.dialects.postgresql.UUID(as_uuid=True)) == sa.any_(sa.column("workspaces")))
        .where(sa.text("document_id = CAST(:document_id AS UUID)") if filters.get("document_id") else sa.true())
        .where(sa.text("CAST(tags AS TEXT) LIKE :tag_filter") if filters.get("tags") else sa.true())
        .limit(200),
        {
            "user_id": user_id,
            "ws_id": workspace_id,
            "document_id": str(filters.get("document_id") or ""),
            "tag_filter": f"%{(filters.get('tags') or [''])[0]}%",
        },
    )).mappings().all()

    question_terms = _tokens(question)
    for row in chunk_rows:
        chunk_text = row["content"] or str(row["content_obj"] or "")
        keyword_score = len(question_terms & _tokens(chunk_text))
        if keyword_score:
            candidates.append(
                RetrievalCandidate(
                    route="keyword_chunk",
                    source="chunk",
                    text=chunk_text,
                    score=float(keyword_score),
                    payload={"id": str(row["id"])},
                )
            )
        candidates.append(
            RetrievalCandidate(
                route="predicate_chunk",
                source="chunk",
                text=chunk_text,
                score=0.1,
                payload={"id": str(row["id"])},
            )
        )

    if not any(item.route == "vector_chunk" for item in candidates):
        for row in chunk_rows:
            chunk_text = row["content"] or str(row["content_obj"] or "")
            emb = row["embedding"]
            if isinstance(emb, dict):
                emb = emb.get("vector")
            if isinstance(emb, list):
                candidates.append(
                    RetrievalCandidate(
                        route="vector_chunk",
                        source="chunk",
                        text=chunk_text,
                        score=_cosine(qv, emb),
                        payload={"id": str(row["id"]), "fallback": "json_cosine"},
                    )
                )

    for row in await vector_search_triples(
        session,
        user_id=user_id,
        graph_id=graph_id,
        query_vector=normalize_embedding(qv),
        top_k=top_k * 2,
    ):
        candidates.append(
            RetrievalCandidate(
                route="graph_path",
                source="triple",
                text=f'{row["head"]} {row["relation_name"]} {row["tail"]}',
                score=float(row.get("score") or 0.0),
                payload={"id": str(row["id"]), "chunks": [str(c) for c in row["chunks"] or []]},
            )
        )

    candidates.extend(
        await retrieve_graph_paths(
            session,
            user_id=user_id,
            graph_id=graph_id,
            question=question,
            top_k=top_k * 2,
            max_hops=2,
        )
    )

    hn = nodes.alias("hn")
    tn = nodes.alias("tn")
    triple_rows = (await session.execute(
        sa.select(
            triples.c.id,
            triples.c.relation_name,
            triples.c.chunks,
            hn.c.name.label("head"),
            hn.c.label.label("head_type"),
            tn.c.name.label("tail"),
            tn.c.label.label("tail_type"),
        )
        .select_from(triples.join(hn, triples.c.head_node_id == hn.c.id).join(tn, triples.c.tail_node_id == tn.c.id))
        .where(triples.c.graph_id == graph_id, triples.c.created_by == user_id)
    )).mappings().all()

    for row in triple_rows:
        text = f'{row["head"]} {row["relation_name"]} {row["tail"]}'
        candidates.append(
            RetrievalCandidate(
                route="graph_path",
                source="triple",
                text=text,
                score=float(len(question_terms & _tokens(text))),
                payload={"id": str(row["id"]), "chunks": [str(c) for c in row["chunks"] or []]},
            )
        )

    node_rows = (await session.execute(
        sa.select(nodes.c.id, nodes.c.name, nodes.c.label, nodes.c.chunks)
        .where(nodes.c.graph_id == graph_id, nodes.c.created_by == user_id)
    )).mappings().all()
    for row in node_rows:
        text = f'{row["name"]} ({row["label"]})'
        if question_terms & _tokens(text):
            candidates.append(
                RetrievalCandidate(
                    route="graph_path",
                    source="node",
                    text=text,
                    score=float(len(question_terms & _tokens(text))),
                    payload={"id": str(row["id"]), "chunks": [str(c) for c in row["chunks"] or []]},
                )
            )

    evidences = [
        Evidence(item.source, item.text, item.score, {"route": item.route, **item.payload})
        for item in fuse_rough_recall_candidates(candidates)
    ]
    ranked = await llm_rerank_evidence(llm_client, question=question, items=evidences, top_k=top_k)
    context = "\n".join(f"- [{item.source}] {item.text}" for item in ranked)
    response = await llm_client.client.chat.completions.create(
        model=llm_client.metadata.language_model_name or "local-demo",
        messages=[
            {"role": "system", "content": "Only answer using the supplied context."},
            {"role": "user", "content": f"问题：{question}\n\n可用上下文：\n{context}"},
        ],
    )

    return {
        "answer": response.choices[0].message.content,
        "evidence": [
            {"source": item.source, "text": item.text, "score": item.score, **item.payload}
            for item in ranked
        ],
    }
