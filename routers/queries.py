# whyhow_api/routers/queries.py
from __future__ import annotations
from typing import Any, Dict, Optional, List, Tuple
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api.dependencies import get_pg, get_llm_client
from whyhow_api.models.common import LLMClient
from whyhow_api.services.crud.user_pg import get_user_by_api_key
from whyhow_api.services.crud.queries_pg import (
    list_queries, count_queries, get_query, delete_query, queries,
)
import sqlalchemy as sa

metadata = sa.MetaData()
UUIDT = sa.dialects.postgresql.UUID(as_uuid=True)

chunks = sa.Table(
    "chunks", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("workspaces", sa.ARRAY(UUIDT)),
    sa.Column("data_type", sa.Text),
    sa.Column("content", sa.Text),
    sa.Column("content_obj", sa.JSON),
    sa.Column("embedding", sa.JSON),
    sa.Column("tags", sa.JSON),
    sa.Column("user_metadata", sa.JSON),
    sa.Column("document_id", UUIDT),
    sa.Column("created_by", UUIDT),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
)

router = APIRouter(tags=["Queries"], prefix="/queries")

async def _require_user_id(session: AsyncSession, api_key: str) -> UUID:
    u = await get_user_by_api_key(session, api_key)
    if not u:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return u["id"]

async def _run_rag_core(
    *,
    session: AsyncSession,
    api_key: str,
    llm_client: LLMClient,
    workspace_id: UUID,
    text: str,
    top_k: int = 5,
    filters: Dict[str, Any] | None = None,
    model: str = "gpt-4o-mini",
    max_tokens: int = 512,
    temperature: float = 0.2,
) -> Dict[str, Any]:
    user_id = await _require_user_id(session, api_key)
    if not text.strip():
        raise HTTPException(status_code=422, detail="text is required")
    top_k = max(1, min(int(top_k), 50))
    filters = filters or {}

    from whyhow_api.utilities.common import embed_texts
    qv = (await embed_texts(llm_client=llm_client, texts=[text]))[0]

    ws_bind = sa.bindparam("ws_id", workspace_id)
    where = [
        chunks.c.created_by == user_id,
        chunks.c.embedding.is_not(None),
        sa.cast(ws_bind, UUIDT) == sa.any_(chunks.c.workspaces),
    ]
    if filters.get("document_id"):
        where.append(chunks.c.document_id == UUID(str(filters["document_id"])))

    cand_rows = (await session.execute(
        sa.select(
            chunks.c.id, chunks.c.data_type, chunks.c.content, chunks.c.content_obj,
            chunks.c.embedding, chunks.c.tags, chunks.c.user_metadata, chunks.c.document_id
        )
        .where(*where)
        .order_by(chunks.c.created_at.desc(), chunks.c.id.desc())
        .limit(500)
    )).mappings().all()

    import math
    def cos(a: list[float], b: list[float]) -> float:
        if not a or not b: return -1.0
        s = sum(x*y for x, y in zip(a, b))
        na = math.sqrt(sum(x*x for x in a))
        nb = math.sqrt(sum(y*y for y in b))
        return (s/(na*nb)) if na and nb else -1.0

    scored = []
    for r in cand_rows:
        emb = r["embedding"]
        if isinstance(emb, dict): emb = emb.get("vector")
        if isinstance(emb, list): scored.append((cos(qv, emb), r))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    import json
    def chunk_to_text(r: Dict[str, Any]) -> str:
        if r["data_type"] == "string":
            return r["content"] or ""
        return json.dumps(r["content_obj"] or r["content"] or {}, ensure_ascii=False)

    context_lines, picked = [], []
    for s, r in top:
        picked.append({"id": str(r["id"]),
                       "score": float(s),
                       "data_type": r["data_type"],
                       "document_id": str(r["document_id"]) if r["document_id"] else None})
        context_lines.append(f"- [{s:.3f}] {chunk_to_text(r)[:800]}")

    context = "\n".join(context_lines) if context_lines else "(no context)"

    answer = None
    try:
        msgs = [
            {"role": "system", "content": "你是专业知识助手，只基于提供的上下文回答；如果无法确定就直说。"},
            {"role": "user",   "content": f"问题：{text}\n\n可用上下文：\n{context}"},
        ]
        if hasattr(llm_client, "client"):
            resp = await llm_client.client.chat.completions.create(
                model=model, messages=msgs, temperature=temperature, max_tokens=max_tokens
            )
            answer = (resp.choices[0].message.content or "").strip()
        elif hasattr(llm_client, "chat"):
            resp = await llm_client.chat(messages=msgs, model=model,
                                         temperature=temperature, max_tokens=max_tokens)
            answer = (resp["content"] if isinstance(resp, dict) else str(resp)).strip()
    except Exception:
        answer = None

    payload = {
        "request": {"workspace_id": str(workspace_id), "text": text, "top_k": top_k, "filters": filters},
        "hits": picked,
        "answer": answer,
    }
    row = (await session.execute(
        sa.insert(queries).values(
            id=uuid4(), user_id=user_id, graph_id=None, status="completed",
            name=text[:255], payload=payload
        ).returning(*queries.c)
    )).mappings().one()
    await session.commit()

    return {"message": "ok", "status": "success",
            "query_id": str(row["id"]), "answer": answer, "top_chunks": picked}

# ---------- RAG One-shot：POST /queries ----------
@router.post("/rag")
async def rag_post(
    body: Dict[str, Any],
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
    llm_client: LLMClient = Depends(get_llm_client),
):
    if not body.get("workspace_id") or not body.get("text"):
        raise HTTPException(status_code=422, detail="workspace_id 与 text 均为必填")
    return await _run_rag_core(
        session=session, api_key=api_key, llm_client=llm_client,
        workspace_id=UUID(str(body["workspace_id"])),
        text=str(body["text"]),
        top_k=int(body.get("top_k") or 5),
        filters=body.get("filters") or {},
        model=body.get("model") or "gpt-4o-mini",
        max_tokens=int(body.get("max_tokens") or 512),
        temperature=float(body.get("temperature") or 0.2),
    )

@router.get("/rag")
async def rag_get(
    workspace_id: UUID = Query(...),
    text: str = Query(...),
    top_k: int = Query(5),
    document_id: UUID | None = Query(None),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
    llm_client: LLMClient = Depends(get_llm_client),
):
    filters = {}
    if document_id: filters["document_id"] = str(document_id)
    return await _run_rag_core(
        session=session, api_key=api_key, llm_client=llm_client,
        workspace_id=workspace_id, text=text, top_k=top_k, filters=filters,
    )

@router.post("")
async def run_rag_query(
    body: Dict[str, Any] = Body(..., description="RAG 查询体"),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
    llm_client: LLMClient = Depends(get_llm_client),
):
    """
    一步完成：按 workspace 过滤 chunk -> 语义相似度选 TopK -> 组上下文 -> 调用 LLM。
    请求体示例:
    {
      "workspace_id": "<uuid>",
      "text": "openGauss 的优势是什么？",
      "top_k": 5,
      "filters": { "document_id": "<uuid-可选>" },
      "model": "gpt-4o-mini",           # 可选
      "max_tokens": 512,                 # 可选
      "temperature": 0.2                 # 可选
    }
    """
    user_id = await _require_user_id(session, api_key)

    # ---- 校验/默认值
    ws_id: Optional[UUID] = body.get("workspace_id")
    text: str = (body.get("text") or "").strip()
    if not ws_id or not text:
        raise HTTPException(status_code=422, detail="workspace_id 与 text 均为必填")

    top_k = int(body.get("top_k") or 5)
    top_k = max(1, min(top_k, 50))
    filters: Dict[str, Any] = body.get("filters") or {}
    model = body.get("model") or "gpt-4o-mini"
    max_tokens = int(body.get("max_tokens") or 512)
    temperature = float(body.get("temperature") or 0.2)

    # ---- 1) 生成查询向量
    from whyhow_api.utilities.common import embed_texts
    qv = (await embed_texts(llm_client=llm_client, texts=[text]))[0]  # list[float]

    # ---- 2) 取候选 chunks（只看当前用户写入的，且 embedding 非空，且包含 workspace_id）
    where = [
        chunks.c.created_by == user_id,
        chunks.c.embedding.is_not(None),
        # workspaces 包含 ws_id。openGauss/PG 兼容：ARRAY CONTAINS
        chunks.c.workspaces.contains([ws_id]),
    ]
    if "document_id" in filters and filters["document_id"]:
        where.append(chunks.c.document_id == UUID(str(filters["document_id"])))

    # 先取前 500 作为候选，避免全表扫
    cand_rows = (await session.execute(
        sa.select(
            chunks.c.id, chunks.c.data_type, chunks.c.content, chunks.c.content_obj,
            chunks.c.embedding, chunks.c.tags, chunks.c.user_metadata, chunks.c.document_id
        )
        .where(*where)
        .order_by(chunks.c.created_at.desc(), chunks.c.id.desc())
        .limit(500)
    )).mappings().all()

    # ---- 3) 计算相似度（余弦），选 TopK
    import math
    def cos(a: list[float], b: list[float]) -> float:
        if not a or not b:
            return -1.0
        s = sum(x*y for x, y in zip(a, b))
        na = math.sqrt(sum(x*x for x in a))
        nb = math.sqrt(sum(y*y for y in b))
        return (s / (na*nb)) if na and nb else -1.0

    scored = []
    for r in cand_rows:
        emb = r["embedding"]
        if isinstance(emb, dict):  # 兼容 {"vector":[...]}
            emb = emb.get("vector")
        if not isinstance(emb, list):
            continue
        scored.append((cos(qv, emb), r))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    # ---- 4) 组上下文
    def chunk_to_text(r: Dict[str, Any]) -> str:
        if r["data_type"] == "string":
            return r["content"] or ""
        # 非字符串做个压缩展示
        import json
        return json.dumps(r["content_obj"] or r["content"] or {}, ensure_ascii=False)

    context_lines = []
    picked_ids: List[str] = []
    for score, r in top:
        picked_ids.append(str(r["id"]))
        snippet = chunk_to_text(r)[:800]  # 控制单段长度
        context_lines.append(f"- [{score:.3f}] {snippet}")

    context = "\n".join(context_lines) if context_lines else "(no context)"

    # ---- 5) 调用 LLM 生成答案（若失败也正常返回 top chunks）
    answer = None
    try:
        # 你自家的 LLMClient 一般暴露 openai 兼容接口：llm_client.client.chat.completions.create(...)
        # 保守写法：同时兼容 .client 和直接 .chat 两种封装
        msgs = [
            {"role": "system", "content": "你是一个专业的知识助手。请只基于提供的上下文回答；若上下文没有答案，就明确说无法确定。"},
            {"role": "user", "content": f"问题：{text}\n\n可用上下文：\n{context}"},
        ]
        if hasattr(llm_client, "client"):  # OpenAI 风格
            resp = await llm_client.client.chat.completions.create(
                model=model, messages=msgs, temperature=temperature, max_tokens=max_tokens
            )
            answer = (resp.choices[0].message.content or "").strip()
        elif hasattr(llm_client, "chat"):  # 备用
            resp = await llm_client.chat(messages=msgs, model=model, temperature=temperature, max_tokens=max_tokens)
            answer = (resp["content"] if isinstance(resp, dict) else str(resp)).strip()
    except Exception as e:
        # 不抛错，继续返回检索结果，便于排查
        answer = None

    # ---- 6) 记录查询（payload 里把请求/命中/答案都塞进去）
    payload = {
        "request": {"workspace_id": str(ws_id), "text": text, "top_k": top_k, "filters": filters},
        "hits": [
            {
                "id": str(r["id"]),
                "score": float(score),
                "data_type": r["data_type"],
                "document_id": str(r["document_id"]) if r["document_id"] else None,
            } for score, r in top
        ],
        "answer": answer,
    }

    row = (await session.execute(
        sa.insert(queries).values(
            id=uuid4(), user_id=user_id, graph_id=None, status="completed",
            name=text[:255], payload=payload
        ).returning(*queries.c)
    )).mappings().one()
    await session.commit()

    return {
        "message": "ok",
        "status": "success",
        "query_id": str(row["id"]),
        "answer": answer,
        "top_chunks": payload["hits"],
    }


# ---------- 列表 ----------
@router.get("")
async def list_queries_endpoint(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=-1, le=50),
    order: int = Query(-1),
    status: Optional[str] = Query(None),
    graph_id: Optional[UUID] = Query(None),
    graph_name: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    rows = await list_queries(
        session, created_by=user_id, skip=skip, limit=limit, order=order,
        status=status, graph_id=graph_id, graph_name=graph_name
    )
    total = await count_queries(
        session, created_by=user_id, status=status, graph_id=graph_id, graph_name=graph_name
    )
    return {"message": "ok", "status": "success", "count": total, "queries": rows}


# ---------- 读取/删除 ----------
@router.get("/{query_id}")
async def get_query_endpoint(
    query_id: UUID,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    row = await get_query(session, query_id=query_id, created_by=user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Query not found.")
    return {"message": "ok", "status": "success", "count": 1, "queries": [row]}

@router.delete("/{query_id}")
async def delete_query_endpoint(
    query_id: UUID,
    session: AsyncSession = Depends(get_pg),
    api_key: str = Header(..., alias="x-api-key"),
):
    user_id = await _require_user_id(session, api_key)
    row = await delete_query(session, query_id=query_id, created_by=user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Query not found.")
    return {"message": "deleted", "status": "success", "count": 1, "queries": [row]}
