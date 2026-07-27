# services/crud/triple_pg.py
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.services.crud.base_pg import insert_returning, list_rows
from whyhow_api.models.common import LLMClient
from whyhow_api.schemas.chunks import ChunksOutWithWorkspaceDetails
from whyhow_api.schemas.triples import TripleDocumentModel
from whyhow_api.utilities.common import embed_texts

logger = logging.getLogger(__name__)

metadata = sa.MetaData()
UUIDT = sa.dialects.postgresql.UUID(as_uuid=True)

triples = sa.Table(
    "triples", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("graph_id", UUIDT, nullable=False),
    sa.Column("head_node_id", UUIDT),
    sa.Column("tail_node_id", UUIDT),
    sa.Column("relation_name", sa.Text, nullable=False),
    sa.Column("properties", sa.JSON, nullable=False),
    sa.Column("chunks", sa.ARRAY(UUIDT), nullable=False),
    sa.Column("created_by", UUIDT, nullable=False),
    sa.Column("embedding", sa.JSON, nullable=True), 
)

nodes = sa.Table(
    "nodes", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("label", sa.Text, nullable=False),
)

graphs = sa.Table(
    "graphs", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("workspace_id", UUIDT, nullable=False),
)

workspaces = sa.Table(
    "workspaces", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
)

chunks_tbl = sa.Table(
    "chunks", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("workspaces", sa.ARRAY(UUIDT)),
    sa.Column("user_metadata", sa.JSON),
    sa.Column("tags", sa.JSON),
    sa.Column("document_id", UUIDT),
    sa.Column("created_by", UUIDT),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)

documents = sa.Table(
    "documents", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("metadata", sa.JSON),
)

async def create_triple(
    session: AsyncSession,
    graph_id: UUID,
    subject: str,
    predicate: str,
    object_: str,
    created_by: UUID,
) -> Dict[str, Any]:
    """
    创建三元组并将其插入到数据库中。
    """
    values = {
        "id": uuid4(),
        "graph_id": graph_id,
        "head_node_id": None,
        "tail_node_id": None,
        "relation_name": predicate,
        "properties": {},
        "chunks": [],
        "created_by": created_by,
    }

    row = await insert_returning(session, triples, values)
    await session.commit()

    return dict(row)


async def get_triple(
    session: AsyncSession,
    triple_id: UUID,
    user_id: UUID,
) -> TripleDocumentModel | None:
    """
    获取单个 triple 及其相关的节点信息（head 和 tail）和图。
    """
    stmt = sa.select(
        triples.c.id,
        triples.c.graph_id,
        triples.c.head_node_id,
        triples.c.tail_node_id,
        triples.c.relation_name,
        triples.c.properties,
        triples.c.created_by,
        triples.c.created_at,
        triples.c.updated_at,
    ).where(
        triples.c.id == triple_id,
        triples.c.created_by == user_id
    )

    result = await session.execute(stmt)
    row = result.mappings().first()

    if not row:
        return None

    head_node = None
    tail_node = None

    if row["head_node_id"]:
        head_node = await session.execute(
            sa.select(nodes.c.id, nodes.c.name, nodes.c.label)
            .where(nodes.c.id == row["head_node_id"])
        ).mappings().first()

    if row["tail_node_id"]:
        tail_node = await session.execute(
            sa.select(nodes.c.id, nodes.c.name, nodes.c.label)
            .where(nodes.c.id == row["tail_node_id"])
        ).mappings().first()

    return TripleDocumentModel(
        id=row["id"],
        graph_id=row["graph_id"],
        head_node=head_node,
        tail_node=tail_node,
        relation_name=row["relation_name"],
        properties=row["properties"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"]
    )

async def list_triples(
    session: AsyncSession,
    graph_id: UUID,
    created_by: UUID,
    skip: int = 0,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """
    列出图谱中的三元组，支持分页。
    """
    stmt = (
        sa.select(triples.c.id, triples.c.graph_id, triples.c.head_node_id, triples.c.tail_node_id,
                  triples.c.relation_name, triples.c.properties, triples.c.chunks, triples.c.created_by)
        .where(triples.c.graph_id == graph_id, triples.c.created_by == created_by)
        .offset(skip)
    )
    
    if limit >= 0:
        stmt = stmt.limit(limit)

    rows = await list_rows(session, stmt)
    if not rows:
        return []
    
    return [dict(row) for row in rows]

async def update_triple(
    session: AsyncSession,
    *,
    triple_id: UUID,
    created_by: UUID,
    predicate: Optional[str] = None,
    subject: Optional[str] = None,
    object_: Optional[str] = None,
    properties: Optional[Dict[str, Any]] = None,
    head_node_id: Optional[UUID] = None,
    tail_node_id: Optional[UUID] = None,
    chunks: Optional[List[UUID]] = None,
    merge_properties: bool = True,
) -> Optional[Dict[str, Any]]:
    """仅允许创建人更新 triple；不存在则返回 None。"""

    old = (await session.execute(
        sa.select(triples).where(
            triples.c.id == triple_id,
            triples.c.created_by == created_by,
        ).limit(1)
    )).mappings().first()
    if not old:
        return None

    to_set: Dict[str, Any] = {}

    if predicate is not None:
        to_set["relation_name"] = predicate
    if head_node_id is not None:
        to_set["head_node_id"] = head_node_id
    if tail_node_id is not None:
        to_set["tail_node_id"] = tail_node_id
    if chunks is not None:
        to_set["chunks"] = chunks

    if merge_properties:
        new_props = dict(old["properties"] or {})
        if properties:
            new_props.update(properties)
        if subject is not None:
            new_props["subject"] = subject
        if object_ is not None:
            new_props["object"] = object_
        if new_props != (old["properties"] or {}):
            to_set["properties"] = new_props
    else:
        new_props = properties or {}
        if subject is not None:
            new_props["subject"] = subject
        if object_ is not None:
            new_props["object"] = object_
        to_set["properties"] = new_props

    if not to_set:
        return dict(old)

    row = (await session.execute(
        sa.update(triples)
        .where(triples.c.id == triple_id, triples.c.created_by == created_by)
        .values(**to_set)
        .returning(*triples.c)
    )).mappings().first()

    await session.commit()
    return dict(row) if row else None

async def delete_triple(
    session: AsyncSession,
    *,
    user_id: UUID,
    triple_id: UUID,
) -> bool:
    res = await session.execute(
        sa.delete(triples).where(
            triples.c.id == triple_id,
            triples.c.created_by == user_id,
        )
    )
    await session.commit()
    return (res.rowcount or 0) > 0

def _compose_triple_text(hn: Optional[Dict[str, Any]], rel: str, tn: Optional[Dict[str, Any]]) -> str:
    h = f'{hn["name"]} ({hn["label"]})' if hn else "∅"
    t = f'{tn["name"]} ({tn["label"]})' if tn else "∅"
    return f"{h} -[{rel}]-> {t}"

async def update_triple_embeddings(
    session: AsyncSession,
    llm_client: LLMClient,
    triple_ids: Optional[List[UUID]] = None,
    graph_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
) -> int:
    """
    为给定的 triples 生成/更新 embedding。可按 triple_ids 或按 graph_id（以及可选 user_id）筛选。
    返回更新条数。
    """
    where = []
    if triple_ids:
        where.append(triples.c.id.in_(triple_ids))
    if graph_id:
        where.append(triples.c.graph_id == graph_id)
    if user_id:
        where.append(triples.c.created_by == user_id)
    if not where:
        return 0

    hn = nodes.alias("hn"); tn = nodes.alias("tn")
    stmt = (
        sa.select(
            triples.c.id,
            triples.c.relation_name,
            hn.c.id.label("hn_id"), hn.c.name.label("hn_name"), hn.c.label.label("hn_label"),
            tn.c.id.label("tn_id"), tn.c.name.label("tn_name"), tn.c.label.label("tn_label"),
        )
        .select_from(
            triples.outerjoin(hn, triples.c.head_node_id == hn.c.id)
                   .outerjoin(tn, triples.c.tail_node_id == tn.c.id)
        )
        .where(*where)
    )
    rows = (await session.execute(stmt)).mappings().all()
    if not rows:
        return 0

    texts: List[str] = []
    ids: List[UUID] = []
    for r in rows:
        hn_obj = {"id": r["hn_id"], "name": r["hn_name"], "label": r["hn_label"]} if r["hn_id"] else None
        tn_obj = {"id": r["tn_id"], "name": r["tn_name"], "label": r["tn_label"]} if r["tn_id"] else None
        texts.append(_compose_triple_text(hn_obj, r["relation_name"], tn_obj))
        ids.append(r["id"])

    vectors = await embed_texts(llm_client=llm_client, texts=texts)

    for tid, vec in zip(ids, vectors):
        await session.execute(
            sa.update(triples).where(triples.c.id == tid).values(embedding=vec)
        )
    await session.commit()
    return len(ids)

async def get_triple_chunks(
    session: AsyncSession,
    triple_id: UUID,
    user_id: Optional[UUID] = None,
    skip: int = 0,
    limit: int = 100,
    order: int = -1,
) -> Tuple[List[ChunksOutWithWorkspaceDetails], int]:
    trow = (await session.execute(sa.select(triples.c.graph_id, triples.c.chunks, triples.c.created_by)
                                  .where(triples.c.id == triple_id))).mappings().first()
    if not trow:
        return [], 0
    if user_id and trow["created_by"] != user_id:
        return [], 0

    chunk_ids: List[UUID] = list(trow["chunks"] or [])
    if not chunk_ids:
        return [], 0

    grow = (await session.execute(sa.select(graphs.c.workspace_id).where(graphs.c.id == trow["graph_id"]))).mappings().first()
    if not grow:
        return [], 0
    ws_id: UUID = grow["workspace_id"]

    where = [chunks_tbl.c.id.in_(chunk_ids),
             sa.func.array_position(chunks_tbl.c.workspaces, ws_id) != None]
    if user_id:
        where.append(chunks_tbl.c.created_by == user_id)

    total = await session.scalar(sa.select(sa.func.count()).select_from(chunks_tbl).where(*where)) or 0

    order_by = [chunks_tbl.c.created_at.asc() if order == 1 else chunks_tbl.c.created_at.desc(),
                chunks_tbl.c.id.asc() if order == 1 else chunks_tbl.c.id.desc()]

    stmt = (
        sa.select(
            chunks_tbl.c.id, chunks_tbl.c.user_metadata, chunks_tbl.c.tags,
            chunks_tbl.c.document_id, chunks_tbl.c.created_at,
            workspaces.c.id.label("ws_id"), workspaces.c.name.label("ws_name"),
            documents.c.id.label("doc_id"), documents.c.metadata.label("doc_meta"),
        )
        .select_from(
            chunks_tbl.join(workspaces, workspaces.c.id == ws_id)
                      .outerjoin(documents, chunks_tbl.c.document_id == documents.c.id)
        )
        .where(*where)
        .order_by(*order_by)
        .offset(skip)
    )
    if limit >= 0:
        stmt = stmt.limit(limit)

    rows = (await session.execute(stmt)).mappings().all()
    out: List[ChunksOutWithWorkspaceDetails] = []
    for r in rows:
        um = (r["user_metadata"] or {}).get(str(ws_id), {})
        tg = (r["tags"] or {}).get(str(ws_id), [])
        doc_obj = None
        if r["doc_id"]:
            doc_obj = {"_id": r["doc_id"], "filename": (r["doc_meta"] or {}).get("filename")}
        out.append(ChunksOutWithWorkspaceDetails(
            _id=r["id"],
            workspaces=[{"_id": r["ws_id"], "name": r["ws_name"]}],
            user_metadata={str(ws_id): um},
            tags={str(ws_id): tg},
            document=doc_obj,
            created_at=r["created_at"],
        ))
    return out, int(total)
