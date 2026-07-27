# services/crud/graph_pg.py
from __future__ import annotations
import itertools
import logging
from typing import Any, Dict, List, Tuple
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.schemas.chunks import ChunksOutWithWorkspaceDetails
from whyhow_api.schemas.graphs import DetailedGraphDocumentModel
from whyhow_api.schemas.nodes import NodeWithId
from whyhow_api.schemas.triples import TripleWithId

logger = logging.getLogger(__name__)

metadata = sa.MetaData()
UUIDT = sa.dialects.postgresql.UUID(as_uuid=True)
EMPTY_SCHEMA_BODY = {"entities": [], "relations": [], "patterns": []}
NULL_UUID = UUID("00000000-0000-0000-0000-000000000000")

graphs = sa.Table(
    "graphs", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("schema_id", UUIDT, nullable=True),
    sa.Column("workspace_id", UUIDT, nullable=False),
    sa.Column("created_by", UUIDT, nullable=False),
    sa.Column("public", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("name", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
)

nodes = sa.Table(
    "nodes", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("graph_id", UUIDT, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("label", sa.Text, nullable=False),
    sa.Column("properties", sa.JSON, nullable=False),
    sa.Column("chunks", sa.ARRAY(UUIDT), nullable=False),
    sa.Column("created_by", UUIDT, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
)

triples = sa.Table(
    "triples", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("graph_id", UUIDT, nullable=False),
    sa.Column("head_node_id", UUIDT, nullable=True),
    sa.Column("tail_node_id", UUIDT, nullable=True),
    sa.Column("relation_name", sa.Text, nullable=False),
    sa.Column("properties", sa.JSON, nullable=False),
    sa.Column("chunks", sa.ARRAY(UUIDT), nullable=False),
    sa.Column("created_by", UUIDT, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
)

workspaces = sa.Table(
    "workspaces", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
)

documents = sa.Table(
    "documents", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("metadata", sa.JSON),
)

chunks = sa.Table(
    "chunks", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("document_id", UUIDT),
    sa.Column("workspaces", sa.ARRAY(UUIDT)),
    sa.Column("data_type", sa.Text),
    sa.Column("content", sa.Text),
    sa.Column("content_obj", sa.JSON),
    sa.Column("tags", sa.JSON),
    sa.Column("user_metadata", sa.JSON),
    sa.Column("metadata", sa.JSON),
    sa.Column("created_by", UUIDT),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)

queries = sa.Table(
    "queries", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("graph_id", UUIDT),
    sa.Column("created_by", UUIDT),
)

# ----------------- 删除图（及其关联） -----------------
async def delete_graphs(
    session: AsyncSession,
    user_id: UUID,
    graph_ids: list[UUID],
) -> None:
    await session.execute(sa.delete(triples).where(triples.c.graph_id.in_(graph_ids), triples.c.created_by == user_id))
    await session.execute(sa.delete(nodes).where(nodes.c.graph_id.in_(graph_ids), nodes.c.created_by == user_id))
    await session.execute(sa.delete(queries).where(queries.c.graph_id.in_(graph_ids), queries.c.created_by == user_id))
    await session.execute(sa.delete(graphs).where(graphs.c.id.in_(graph_ids), graphs.c.created_by == user_id))
    await session.commit()
    logger.info(f"All related items for graphs {graph_ids} were successfully deleted.")

# ----------------- 关系列表（distinct） -----------------
async def list_relations(
    session: AsyncSession,
    user_id: UUID | None,
    graph_id: UUID,
    skip: int = 0,
    limit: int = 100,
    order: int = -1,
) -> Tuple[List[str], int]:
    base = (
        sa.select(sa.distinct(triples.c.relation_name))
        .select_from(triples.join(graphs, triples.c.graph_id == graphs.c.id))
        .where(triples.c.graph_id == graph_id)
    )
    if user_id:
        base = base.where(graphs.c.created_by == user_id)

    total = len((await session.execute(base)).scalars().all())

    order_by = triples.c.relation_name.asc() if order == 1 else triples.c.relation_name.desc()
    stmt = base.order_by(order_by).offset(skip)
    if limit >= 0:
        stmt = stmt.limit(limit)

    relations = (await session.execute(stmt)).scalars().all()
    return relations, total

# ----------------- 节点列表 -----------------
async def list_nodes(
    session: AsyncSession,
    user_id: UUID | None,
    graph_id: UUID,
    skip: int = 0,
    limit: int = 100,
    order: int = -1,
) -> tuple[List[NodeWithId], int]:
    base_from = nodes.join(graphs, nodes.c.graph_id == graphs.c.id)
    where = [nodes.c.graph_id == graph_id]
    if user_id:
        where.append(graphs.c.created_by == user_id)

    total_stmt = sa.select(sa.func.count()).select_from(base_from).where(*where)
    total = (await session.execute(total_stmt)).scalar() or 0

    order_cols = [
        nodes.c.created_at.asc() if order == 1 else nodes.c.created_at.desc(),
        nodes.c.id.asc()         if order == 1 else nodes.c.id.desc(),
    ]
    stmt = (
        sa.select(nodes)
        .select_from(base_from)
        .where(*where)
        .order_by(*order_cols)
        .offset(skip)
    )
    if limit >= 0:
        stmt = stmt.limit(limit)

    rows = (await session.execute(stmt)).mappings().all()

    out = [
        NodeWithId(
            **{
                "_id": r["id"],
                "name": r["name"],
                "label": r["label"],
                "properties": r.get("properties") or {},
                "chunks": r.get("chunks") or [],
            }
        )
        for r in rows
    ]
    return out, int(total)



# ----------------- 取单个图（含 schema/workspace） -----------------
async def get_graph(
    session: AsyncSession,
    graph_id: UUID,
    user_id: UUID | None,
    public: bool = False,
) -> DetailedGraphDocumentModel | None:
    where = [graphs.c.id == graph_id]
    if user_id:
        where.append(graphs.c.created_by == user_id)
    if public:
        where.append(graphs.c.public == sa.true())

    ws = workspaces.alias("ws")
    stmt = (
        sa.select(
            graphs.c.id,
            graphs.c.schema_id,
            graphs.c.workspace_id,
            graphs.c.created_by,
            graphs.c.public,
            graphs.c.name,
            graphs.c.created_at,
            graphs.c.updated_at,
            sa.literal("ready").label("status"),
            ws.c.name.label("workspace_name"),
        )
        .select_from(graphs.join(ws, graphs.c.workspace_id == ws.c.id))
        .where(*where)
        .limit(1)
    )
    row = (await session.execute(stmt)).mappings().first()
    if not row:
        return None

    payload = dict(row)

    schema_id = payload.pop("schema_id", None)
    workspace_id = payload.pop("workspace_id")
    workspace_name = payload.pop("workspace_name")

    payload["workspace"] = {"_id": workspace_id, "name": workspace_name}
    payload["schema"] = {
        "_id": (schema_id if schema_id is not None else NULL_UUID),
        "name": "(default)",
        "body": EMPTY_SCHEMA_BODY,
        "created_by": payload["created_by"],
        "workspace_id": workspace_id,
    }
    payload.setdefault("status", "ready")

    return DetailedGraphDocumentModel(**payload)


# ----------------- 图列表（含 schema/workspace） -----------------
async def list_all_graphs(
    session: AsyncSession,
    user_id: UUID,
    filters: Dict[str, Any] | None = None,
    skip: int = 0,
    limit: int = 100,
    order: int = -1,
) -> List[DetailedGraphDocumentModel] | None:
    where = [graphs.c.created_by == user_id]
    if filters and "public" in filters:
        where.append(graphs.c.public == bool(filters["public"]))

    ws = workspaces.alias("ws")
    stmt = (
        sa.select(
            graphs.c.id,
            graphs.c.schema_id,
            graphs.c.workspace_id,
            graphs.c.created_by,
            graphs.c.public,
            graphs.c.name,
            graphs.c.created_at,
            graphs.c.updated_at,
            sa.literal("ready").label("status"),
            ws.c.name.label("workspace_name"),
        )
        .select_from(graphs.join(ws, graphs.c.workspace_id == ws.c.id))
        .where(*where)
        .order_by(
            graphs.c.created_at.asc() if order == 1 else graphs.c.created_at.desc(),
            graphs.c.id.asc() if order == 1 else graphs.c.id.desc(),
        )
        .offset(skip)
    )
    if limit >= 0:
        stmt = stmt.limit(limit)

    rows = (await session.execute(stmt)).mappings().all()
    if not rows:
        return None

    out: List[DetailedGraphDocumentModel] = []
    for r in rows:
        payload = dict(r)

        schema_id = payload.pop("schema_id", None)
        workspace_id = payload.pop("workspace_id")
        workspace_name = payload.pop("workspace_name")

        payload["workspace"] = {"_id": workspace_id, "name": workspace_name}
        payload["schema"] = {
            "_id": (schema_id if schema_id is not None else NULL_UUID),
            "name": "(default)",
            "body": EMPTY_SCHEMA_BODY,
            "created_by": payload["created_by"],
            "workspace_id": workspace_id,
        }
        payload.setdefault("status", "ready")

        out.append(DetailedGraphDocumentModel(**payload))
    return out



# ----------------- 三元组列表（含 head/tail node 展开） -----------------
async def list_triples(
    session: AsyncSession,
    user_id: UUID | None,
    graph_id: UUID,
    skip: int = 0,
    limit: int = 100,
    order: int = -1,
) -> Tuple[List[TripleWithId], int]:
    where = [triples.c.graph_id == graph_id, triples.c.relation_name != "Contains"]
    if user_id:
        where.append(triples.c.created_by == user_id)

    total = await session.scalar(sa.select(sa.func.count()).select_from(triples).where(*where)) or 0

    hn = nodes.alias("hn"); tn = nodes.alias("tn")
    stmt = (
        sa.select(
            triples.c.id,
            sa.json_build_object(
                "_id", hn.c.id, "name", hn.c.name, "label", hn.c.label,
                "properties", hn.c.properties, "chunks", hn.c.chunks
            ).label("head_node"),
            sa.json_build_object(
                "name", triples.c.relation_name, "properties", triples.c.properties
            ).label("relation"),
            sa.json_build_object(
                "_id", tn.c.id, "name", tn.c.name, "label", tn.c.label,
                "properties", tn.c.properties, "chunks", tn.c.chunks
            ).label("tail_node"),
            triples.c.chunks.label("chunks"),
        )
        .select_from(
            triples.outerjoin(hn, triples.c.head_node_id == hn.c.id)
                   .outerjoin(tn, triples.c.tail_node_id == tn.c.id)
        )
        .where(*where)
        .order_by(
            triples.c.created_at.asc() if order == 1 else triples.c.created_at.desc(),
            triples.c.id.asc() if order == 1 else triples.c.id.desc(),
        )
        .offset(skip)
    )
    if limit >= 0: stmt = stmt.limit(limit)

    rows = (await session.execute(stmt)).mappings().all()
    out = [TripleWithId(**dict(r)) for r in rows]
    return out, int(total)

# ----------------- 按 ID 集合提取三元组 -----------------
async def list_triples_by_ids(
    session: AsyncSession,
    user_id: UUID,
    graph_id: UUID,
    triple_ids: List[UUID],
) -> List[TripleWithId]:
    where = [triples.c.id.in_(triple_ids), triples.c.graph_id == graph_id, triples.c.created_by == user_id]
    hn = nodes.alias("hn"); tn = nodes.alias("tn")
    stmt = (
        sa.select(
            triples.c.id,
            sa.json_build_object("_id", hn.c.id, "name", hn.c.name, "label", hn.c.label,
                                 "properties", hn.c.properties, "chunks", hn.c.chunks).label("head_node"),
            sa.json_build_object("name", triples.c.relation_name, "properties", triples.c.properties).label("relation"),
            sa.json_build_object("_id", tn.c.id, "name", tn.c.name, "label", tn.c.label,
                                 "properties", tn.c.properties, "chunks", tn.c.chunks).label("tail_node"),
            triples.c.chunks.label("chunks"),
        )
        .select_from(triples.outerjoin(hn, triples.c.head_node_id == hn.c.id)
                            .outerjoin(tn, triples.c.tail_node_id == tn.c.id))
        .where(*where)
    )
    rows = (await session.execute(stmt)).mappings().all()
    return [TripleWithId(**dict(r)) for r in rows]


# ----------------- 根据图取 chunks（整合 node/triple 引用的 chunk） -----------------
async def get_graph_chunks(
    session: AsyncSession,
    user_id: UUID | None,
    graph_id: UUID,
    workspace_id: UUID,
    skip: int = 0,
    limit: int = 100,
    order: int = -1,
) -> Tuple[list[ChunksOutWithWorkspaceDetails], int]:
    where_nodes = [nodes.c.graph_id == graph_id]
    where_tris  = [triples.c.graph_id == graph_id]
    if user_id:
        where_nodes.append(nodes.c.created_by == user_id)
        where_tris.append(triples.c.created_by == user_id)

    node_chunks = (await session.execute(sa.select(nodes.c.chunks).where(*where_nodes))).scalars().all()
    tri_chunks  = (await session.execute(sa.select(triples.c.chunks).where(*where_tris))).scalars().all()
    chunk_ids: List[UUID] = list({cid for arr in (node_chunks + tri_chunks) for cid in (arr or [])})

    if not chunk_ids:
        return [], 0

    ws = workspaces.alias("ws"); doc = documents.alias("doc")
    where = [
        chunks.c.id.in_(chunk_ids),
        sa.func.array_position(chunks.c.workspaces, workspace_id) != None
    ]
    if user_id: where.append(chunks.c.created_by == user_id)

    total = await session.scalar(sa.select(sa.func.count()).select_from(chunks).where(*where)) or 0

    order_by = [chunks.c.created_at.asc() if order == 1 else chunks.c.created_at.desc(), chunks.c.id.asc() if order == 1 else chunks.c.id.desc()]
    stmt = (
        sa.select(
            chunks.c.id, chunks.c.workspaces, chunks.c.data_type,
            chunks.c.content, chunks.c.content_obj, chunks.c.tags,
            chunks.c.user_metadata, chunks.c.metadata, chunks.c.created_at,
            sa.json_build_object("_id", ws.c.id, "name", ws.c.name).label("ws_obj"),
            sa.json_build_object("_id", doc.c.id,
                                 "filename", sa.func.coalesce(doc.c.metadata["filename"], sa.null())).label("doc_obj")
        )
        .select_from(chunks
                     .join(ws, workspace_id == ws.c.id)
                     .outerjoin(doc, chunks.c.document_id == doc.c.id))
        .where(*where)
        .order_by(*order_by)
        .offset(skip)
    )
    if limit >= 0: stmt = stmt.limit(limit)

    rows = (await session.execute(stmt)).mappings().all()

    out: List[Dict[str, Any]] = []
    for r in rows:
        ws_id = workspace_id
        um = (r["user_metadata"] or {}).get(str(ws_id), {})
        tg = (r["tags"] or {}).get(str(ws_id), [])
        out.append({
            "_id": r["id"],
            "workspaces": [r["ws_obj"]],
            "data_type": r["data_type"],
            "content": r["content"] if r["data_type"] == "string" else None,
            "content_obj": r["content_obj"] if r["data_type"] == "object" else None,
            "tags": {str(ws_id): tg},
            "user_metadata": {str(ws_id): um},
            "metadata": r["metadata"],
            "created_at": r["created_at"],
            "document": r["doc_obj"] if r["doc_obj"].get("_id") else None
        })

    return [ChunksOutWithWorkspaceDetails(**c) for c in out], int(total)

