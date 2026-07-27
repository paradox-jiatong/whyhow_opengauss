"""Graph service (openGauss/PostgreSQL version).

说明：
- 完整移除 Mongo 依赖；统一通过 PG 的 CRUD 层实现。
- 字段映射：node.type -> nodes.label；triple.type -> triples.relation_name
- 属性/chunks 的“合并”在应用层做（Python 合并 dict / 去重列表），再写回 PG。
- 向量检索暂不实现；embedding 更新已接入 triple_pg.update_triple_embeddings。
"""

from __future__ import annotations
import asyncio
import json
import logging
from collections import defaultdict
from typing import Any, DefaultDict, Dict, List, Mapping, Sequence, Tuple
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.config import Settings
from whyhow_api.exceptions import NotFoundException
from whyhow_api.models.common import (
    SchemaTriplePattern,
    StructuredSchemaEntity,
    StructuredSchemaTriplePattern,
    EntityField,
)
from whyhow_api.schemas.base import ErrorDetails, get_utc_now
from whyhow_api.schemas.chunks import ChunkDocumentModel, ChunksOutWithWorkspaceDetails
from whyhow_api.schemas.graphs import (
    CreateGraphBody,
    GraphStateErrorsUpdate,
    Triple,
)
from whyhow_api.schemas.nodes import NodeDocumentModel, NodeWithId
from whyhow_api.schemas.triples import TripleDocumentModel, TripleWithId
from whyhow_api.utilities.builders import OpenAIBuilder, SpacyEntityExtractor
from whyhow_api.utilities.common import clean_text
from whyhow_api.utilities.config import (
    create_schema_guided_graph_prompt,
    openai_completions_configs,
)
# ---- 依赖 PG 版 CRUD ----
from whyhow_api.services.crud.graph_pg import (
    list_nodes as pg_list_nodes,
    list_relations as pg_list_relations,
    list_triples as pg_list_triples,
    get_graph as pg_get_graph,
    delete_graphs as pg_delete_graphs,
)
from whyhow_api.services.crud.chunks_pg import get_chunks as pg_get_chunks
from whyhow_api.services.crud.triple_pg import update_triple_embeddings as pg_update_triple_embeddings
from whyhow_api.services.crud.base_pg import insert_returning

logger = logging.getLogger(__name__)
settings = Settings()

metadata = sa.MetaData()
UUIDT = sa.dialects.postgresql.UUID(as_uuid=True)

graphs = sa.Table(
    "graphs", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("schema_id", UUIDT),
    sa.Column("workspace_id", UUIDT, nullable=False),
    sa.Column("created_by", UUIDT, nullable=False),
    sa.Column("public", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("name", sa.Text),
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
    sa.Column("head_node_id", UUIDT),
    sa.Column("tail_node_id", UUIDT),
    sa.Column("relation_name", sa.Text, nullable=False),
    sa.Column("properties", sa.JSON, nullable=False),
    sa.Column("chunks", sa.ARRAY(UUIDT), nullable=False),
    sa.Column("created_by", UUIDT, nullable=False),
    sa.Column("embedding", sa.JSON),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
)


# ---------------- 工具函数（与 Mongo 版语义对齐） ----------------

def _merge_dicts(d1: Dict[str, Any], d2: Dict[str, Any]) -> Dict[str, Any]:
    """将 d2 合入 d1；值不同则合并为 list；dict 递归；list 追加去重。"""
    for k, v in (d2 or {}).items():
        if k not in d1:
            d1[k] = v
            continue
        a = d1[k]
        if a == v:
            continue
        if isinstance(a, dict) and isinstance(v, dict):
            d1[k] = _merge_dicts(a, v)
        elif isinstance(a, list) and isinstance(v, list):
            seen = set()
            out = []
            for item in a + v:
                key = json.dumps(item, sort_keys=True) if isinstance(item, (dict, list)) else item
                if key in seen: continue
                seen.add(key)
                out.append(item)
            d1[k] = out
        elif isinstance(a, list):
            if v not in a: d1[k] = a + [v]
        elif isinstance(v, list):
            d1[k] = [a] + [x for x in v if x != a]
        else:
            d1[k] = [a, v]
    return d1

def _union_uuid_list(a: List[UUID] | None, b: List[UUID] | None) -> List[UUID]:
    s = []
    seen = set()
    for x in (a or []) + (b or []):
        if x in seen: continue
        seen.add(x); s.append(x)
    return s


# ---------------- 对应原：get_and_separate_chunks_on_data_type ----------------
async def get_and_separate_chunks_on_data_type_pg(
    session: AsyncSession,
    user_id: UUID,
    chunk_ids: List[UUID],
) -> dict[str, list[ChunkDocumentModel]]:
    """
    用 PG 获取 chunks 并按 data_type 分组；返回结构与 Mongo 版一致：
    { "string": [ChunkDocumentModel...], "object": [ChunkDocumentModel...] }
    """
    if not chunk_ids:
        return {}
    out = await pg_get_chunks(
        session=session,
        user_id=user_id,
        filters={"_id": chunk_ids[0]} if len(chunk_ids) == 1 else {},
        include_embeddings=False,
        populate=False,
        limit=-1,
    )
    rows = (await session.execute(
        sa.select(
            nodes.bind
        )
    ))
    from whyhow_api.services.crud.chunks_pg import chunks as chunks_tbl
    rows = (await session.execute(
        sa.select(chunks_tbl).where(chunks_tbl.c.id.in_(chunk_ids))
    )).mappings().all()

    grouped: DefaultDict[str, list[ChunkDocumentModel]] = defaultdict(list)
    for r in rows:
        data_type = r["data_type"] or "string"
        content = r["content"] if data_type == "string" else (r.get("content_obj") or {})
        grouped[data_type].append(
            ChunkDocumentModel(
                id=r["id"],
                workspaces=r["workspaces"] or [],
                data_type=data_type,
                content=content,
                metadata=r["metadata"] or {},
                user_metadata=r["user_metadata"] or {},
                tags=r["tags"] or {},
                document=r.get("document_id"),
                created_by=r["created_by"],
                created_at=r.get("created_at"),
                updated_at=r.get("updated_at"),
            )
        )
    return dict(grouped)


# ---------------- 对应原：build_graph（核心：插/并/去重） ----------------
async def build_graph_pg(
    session: AsyncSession,
    llm_client,
    graph_id: UUID,
    user_id: UUID,
    triples_in: list[Triple],
    task_id: UUID | None = None,
) -> None:
    """
    核心流程：
      1) upsert nodes（以 (graph_id, name, label, created_by) 判断“唯一”）
      2) upsert triples（以 (graph_id, head_node, tail_node, relation_name, created_by) 判断“唯一”）
      3) 写入后，批量为本批新增/更新的 triples 生成 embedding
      4) 如传入 task_id，可在外层路由中更新任务状态（此处不直接写 task 表）

    注意：需要数据库中建立相应唯一约束（推荐），否则此处用查后更改。
    """
    head_tail_pairs = set([(t.head, t.head_type) for t in triples_in] + [(t.tail, t.tail_type) for t in triples_in])
    existing = (await session.execute(
        sa.select(nodes.c.id, nodes.c.name, nodes.c.label)
        .where(nodes.c.graph_id == graph_id, nodes.c.created_by == user_id,
               sa.tuple_(nodes.c.name, nodes.c.label).in_(list(head_tail_pairs)))
    )).mappings().all()
    name_label_to_id: Dict[Tuple[str, str], UUID] = {(r["name"], r["label"]): r["id"] for r in existing}

    for t in triples_in:
        for name, label, props in [
            (t.head, t.head_type, t.head_properties or {}),
            (t.tail, t.tail_type, t.tail_properties or {}),
        ]:
            chunks_in = props.pop("chunks", [])
            nid = name_label_to_id.get((name, label))
            if nid:
                row = (await session.execute(
                    sa.select(nodes).where(nodes.c.id == nid)
                )).mappings().one()
                new_props = _merge_dicts(dict(row["properties"] or {}), dict(props or {}))
                new_chunks = _union_uuid_list(list(row["chunks"] or []), list(chunks_in or []))
                await session.execute(
                    sa.update(nodes).where(nodes.c.id == nid)
                    .values(properties=new_props, chunks=new_chunks, updated_at=get_utc_now())
                )
            else:
                values = {
                    "id": uuid4(),
                    "graph_id": graph_id,
                    "name": name,
                    "label": label,
                    "properties": props or {},
                    "chunks": list(chunks_in or []),
                    "created_by": user_id,
                    "created_at": get_utc_now(),
                    "updated_at": get_utc_now(),
                }
                row = await insert_returning(session, nodes, values)
                name_label_to_id[(name, label)] = row["id"]
    await session.commit()

    touched_triple_ids: List[UUID] = []
    for t in triples_in:
        hn = name_label_to_id.get((t.head, t.head_type))
        tn = name_label_to_id.get((t.tail, t.tail_type))
        if not hn or not tn:
            raise NotFoundException("head/tail node not found after upsert")

        props = dict(t.relation_properties or {})
        chunks_in = props.pop("chunks", [])

        exist = (await session.execute(
            sa.select(triples).where(
                triples.c.graph_id == graph_id,
                triples.c.created_by == user_id,
                triples.c.head_node_id == hn,
                triples.c.tail_node_id == tn,
                triples.c.relation_name == t.relation,
            ).limit(1)
        )).mappings().first()

        if exist:
            new_props = _merge_dicts(dict(exist["properties"] or {}), dict(props or {}))
            hn_row = (await session.execute(sa.select(nodes.c.chunks).where(nodes.c.id == hn))).mappings().one()
            tn_row = (await session.execute(sa.select(nodes.c.chunks).where(nodes.c.id == tn))).mappings().one()
            inter = set(hn_row["chunks"] or []).intersection(set(tn_row["chunks"] or []))
            new_chunks = _union_uuid_list(list(exist["chunks"] or []), list(chunks_in or []))
            new_chunks = _union_uuid_list(list(new_chunks), list(inter))
            await session.execute(
                sa.update(triples).where(triples.c.id == exist["id"])
                .values(properties=new_props, chunks=new_chunks, updated_at=get_utc_now())
            )
            touched_triple_ids.append(exist["id"])
        else:
            hn_row = (await session.execute(sa.select(nodes.c.chunks).where(nodes.c.id == hn))).mappings().one()
            tn_row = (await session.execute(sa.select(nodes.c.chunks).where(nodes.c.id == tn))).mappings().one()
            inter = set(hn_row["chunks"] or []).intersection(set(tn_row["chunks"] or []))
            values = {
                "id": uuid4(),
                "graph_id": graph_id,
                "head_node_id": hn,
                "tail_node_id": tn,
                "relation_name": t.relation,
                "properties": props or {},
                "chunks": _union_uuid_list(list(chunks_in or []), list(inter)),
                "created_by": user_id,
                "created_at": get_utc_now(),
                "updated_at": get_utc_now(),
            }
            row = await insert_returning(session, triples, values)
            touched_triple_ids.append(row["id"])
    await session.commit()

    if touched_triple_ids:
        await pg_update_triple_embeddings(
                session=session,
                llm_client=llm_client,
                triple_ids=touched_triple_ids,
                user_id=user_id,
            )
    logger.info("Graph build (PG) completed.")


# ---------------- 结构化抽取 ----------------
def create_structured_patterns(patterns: List[SchemaTriplePattern]) -> List[StructuredSchemaTriplePattern]:
    structured: List[StructuredSchemaTriplePattern] = []
    for p in patterns:
        for hf in p.head.fields:
            for tf in p.tail.fields:
                structured.append(
                    StructuredSchemaTriplePattern(
                        head=StructuredSchemaEntity(name=p.head.name, field=hf),
                        relation=p.relation.name,
                        tail=StructuredSchemaEntity(name=p.tail.name, field=tf),
                    )
                )
    return structured

def extract_structured_graph_triples(
    patterns: List[SchemaTriplePattern], chunks: List[ChunkDocumentModel]
) -> List[Triple]:
    structured_patterns = create_structured_patterns(patterns)
    out: List[Triple] = []
    for c in chunks:
        if c.data_type != "object":
            continue
        for p in structured_patterns:
            if p.head.field.name not in c.content or p.tail.field.name not in c.content:
                continue
            head = c.content.get(p.head.field.name)
            tail = c.content.get(p.tail.field.name)
            head_properties = {prop: c.content.get(prop) for prop in p.head.field.properties or []}
            tail_properties = {prop: c.content.get(prop) for prop in p.tail.field.properties or []}
            head_properties["chunks"] = [c.id]; tail_properties["chunks"] = [c.id]
            out.append(
                Triple(
                    head=str(head) if head is not None else "Unnamed",
                    head_type=p.head.name,
                    head_properties=head_properties,
                    relation=p.relation,
                    relation_properties={"chunks": [c.id]},
                    tail=str(tail) if tail is not None else "Unnamed",
                    tail_type=p.tail.name,
                    tail_properties=tail_properties,
                )
            )
    return out
