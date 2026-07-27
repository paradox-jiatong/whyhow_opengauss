# services/crud/chunks_pg.py
from __future__ import annotations

import json
import logging
import sys
from io import BytesIO, StringIO
from typing import Any, Callable, Dict, List, Tuple, get_args
from uuid import UUID, uuid4
from enum import Enum
from fastapi import HTTPException

import pandas as pd
import sqlalchemy as sa
from pandas.errors import EmptyDataError, ParserError
from pypdf import PdfReader
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from whyhow_api.config import Settings
from whyhow_api.models.common import LLMClient
from whyhow_api.schemas.base import AllowedChunkContentTypes, ErrorDetails, File_Extensions
from whyhow_api.schemas.chunks import (
    AddChunkModel, ChunkAssignments, ChunkDocumentModel, ChunkMetadata, ChunkOut,
    ChunksOutWithWorkspaceDetails, ChunkUnassignments, UpdateChunkModel,
)
from whyhow_api.services.crud.base_pg import insert_returning
from whyhow_api.utilities.common import embed_texts

logger = logging.getLogger(__name__)
settings = Settings()

metadata = sa.MetaData()
UUIDT = sa.dialects.postgresql.UUID(as_uuid=True)

chunks = sa.Table(
    "chunks", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("document_id", UUIDT),
    sa.Column("workspaces", sa.ARRAY(UUIDT), nullable=False),
    sa.Column("data_type", sa.Text, nullable=False),
    sa.Column("content", sa.Text),
    sa.Column("content_obj", sa.JSON),
    sa.Column("embedding", sa.JSON),
    sa.Column("tags", sa.JSON, nullable=False),
    sa.Column("user_metadata", sa.JSON, nullable=False),
    sa.Column("metadata", sa.JSON, nullable=False),
    sa.Column("created_by", UUIDT, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
)

documents = sa.Table(
    "documents", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("metadata", sa.JSON),
)

workspaces = sa.Table(
    "workspaces", metadata,
    sa.Column("id", UUIDT, primary_key=True),
    sa.Column("name", sa.Text),
)

nodes_tbl = sa.Table(
    "nodes", metadata,
    sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
    sa.Column("chunks", sa.ARRAY(PGUUID(as_uuid=True))),
)

triples_tbl = sa.Table(
    "triples", metadata,
    sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
    sa.Column("chunks", sa.ARRAY(PGUUID(as_uuid=True))),
)

# -------- 查询（含 workspace 与 document 展开） --------
async def get_chunks(
    session: AsyncSession,
    user_id: UUID,
    llm_client: LLMClient | None = None,
    include_embeddings: bool = False,
    filters: Dict[str, Any] | None = None,
    skip: int = 0,
    limit: int = 10,
    order: int = 1,
    populate: bool = True,
) -> List[ChunksOutWithWorkspaceDetails] | List[ChunkDocumentModel]:

    filters = dict(filters or {})

    seed_concept = filters.pop("seed_concept", None)
    if seed_concept:
        raise ValueError("Vector search is not implemented for openGauss yet.")

    if "workspace_id" in filters and "workspaces" not in filters:
        filters["workspaces"] = filters.pop("workspace_id")

    where = [chunks.c.created_by == user_id]

    if "workspaces" in filters and filters["workspaces"]:
        ws_id = UUID(str(filters["workspaces"]))
        ws_bind = sa.bindparam("ws_id", ws_id)
        where.append(sa.cast(ws_bind, UUIDT) == sa.any_(chunks.c.workspaces))

    if "data_type" in filters:
        where.append(chunks.c.data_type == filters["data_type"])
    if "_id" in filters:
        where.append(chunks.c.id == filters["_id"])
    if "document_id" in filters:
        where.append(chunks.c.document_id == filters["document_id"])

    order_by = [
        chunks.c.created_at.asc() if order == 1 else chunks.c.created_at.desc(),
        chunks.c.id.asc() if order == 1 else chunks.c.id.desc(),
    ]
    stmt = sa.select(chunks).where(*where).order_by(*order_by).offset(skip)
    if limit >= 0:
        stmt = stmt.limit(limit)

    rows = (await session.execute(stmt)).mappings().all()
    if not rows:
        return []

    if not populate:
        return [ChunkDocumentModel(**dict(r)) for r in rows]

    out: List[Dict[str, Any]] = []
    for r in rows:
        for_w = filters.get("workspaces")
        ws_id = for_w or ((r["workspaces"] or [None])[0])

        ws_name = None
        if ws_id:
            wrow = (await session.execute(
                sa.select(workspaces.c.id, workspaces.c.name)
                .where(workspaces.c.id == ws_id)
            )).mappings().first()
            if wrow:
                ws_name = wrow["name"]

        doc_obj = None
        if r["document_id"]:
            drow = (await session.execute(
                sa.select(documents.c.id, documents.c.metadata)
                .where(documents.c.id == r["document_id"])
            )).mappings().first()
            if drow:
                meta = drow["metadata"] or {}

                fn = (meta.get("filename")
                    or meta.get("name")
                    or meta.get("title")
                    or meta.get("path")
                    or "").strip()

                if not fn:
                    dt = (r.get("data_type") or "").lower()
                    if dt in ("string", "text"):
                        ext = "txt"
                    elif dt in ("object", "json") or r.get("content_obj") is not None:
                        ext = "json"
                    else:
                        ext = meta.get("extension") or "bin"
                    fn = f"{drow['id']}.{ext}"

                doc_obj = {"_id": drow["id"], "filename": fn}


        um = (r["user_metadata"] or {}).get(str(ws_id), {}) if ws_id else {}
        tg = (r["tags"] or {}).get(str(ws_id), []) if ws_id else []

        item: Dict[str, Any] = {
            "_id": r["id"],
            "workspaces": ([{"_id": ws_id, "name": ws_name}] if ws_id else []),
            "data_type": r["data_type"],
            "tags": {str(ws_id): tg} if ws_id else {},
            "user_metadata": {str(ws_id): um} if ws_id else {},
            "metadata": r["metadata"],
            "document": doc_obj,
            "created_by": r["created_by"],
            "created_at": r["created_at"],
        }

        dt = (r.get("data_type") or "").lower()

        if r.get("content") is not None:
            val = r["content"]
            if isinstance(val, str) and val == "":
                val = " "
            item["content"] = val
        elif r.get("content_obj") is not None:
            item["content"] = r["content_obj"]
        else:
            item["content"] = (" " if dt in ("string", "text") else {})

        if include_embeddings and r.get("embedding") is not None:
            item["embedding"] = r["embedding"]

        out.append(item)

    return [ChunksOutWithWorkspaceDetails(**c) for c in out]

async def get_chunk_basic(
    session: AsyncSession,
    chunk_id: UUID,
    user_id: UUID,
) -> ChunkDocumentModel | None:
    """
    获取指定 chunk 的基本信息。
    - 查询 chunks 表。
    - 返回包括工作空间和文档信息的 chunk 数据。
    """
    stmt = (
        sa.select(
            chunks.c.id,
            chunks.c.document_id,
            chunks.c.workspaces,
            chunks.c.data_type,
            chunks.c.content,
            chunks.c.tags,
            chunks.c.user_metadata,
            chunks.c.metadata,
            chunks.c.created_by,
            chunks.c.created_at,
            chunks.c.updated_at,
        )
        .where(chunks.c.id == chunk_id, chunks.c.created_by == user_id)
    )

    result = await session.execute(stmt)
    row = result.mappings().first()

    if row:
        return ChunkDocumentModel(**dict(row))
    else:
        return None
    
# -------- 文本切块 --------
def split_text_into_chunks(text: str, page_number: int | None = None) -> List[Dict[str, Any]]:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.api.max_chars_per_chunk, chunk_overlap=0
    )
    chunks_: List[Dict[str, Any]] = []
    loc = 0
    for d in text_splitter.split_text(text):
        _length = len(d)
        meta = {"start": loc, "end": loc + _length}
        if page_number is not None: meta["page"] = page_number
        chunks_.append({"content": d, "metadata": meta})
        loc += _length
    return chunks_

def validate_and_convert(value: Any) -> Any:
    if not isinstance(value, get_args(AllowedChunkContentTypes)):
        return str(value)
    return value

# -------- 入库准备（手动添加） --------
def prepare_chunks(
    chunks_in: List[AddChunkModel],
    workspace_id: UUID,
    user_id: UUID
) -> List[ChunkDocumentModel]:
    items: List[ChunkDocumentModel] = []
    ws_key = str(workspace_id)

    for c in chunks_in:
        if c.content is None:
            raise ValueError("chunks_in[].content is required (str or dict)")

        is_text = isinstance(c.content, str)
        length = len(c.content) if is_text else len(getattr(c.content, "keys", lambda: [])())
        size_bytes = sys.getsizeof(c.content)

        items.append(
            ChunkDocumentModel(
                workspaces=[workspace_id],
                content=c.content,
                data_type="string" if is_text else "object",
                tags={ws_key: (c.tags or [])} if c.tags else {},
                user_metadata={ws_key: (c.user_metadata or {})} if c.user_metadata else {},
                metadata=ChunkMetadata(
                    length=length,
                    size=size_bytes,
                    data_source_type="manual",
                ),
                created_by=user_id,
            )
        )
    return items


# -------- 批量插入（嵌入 + 返回插入后的数据） --------
async def add_chunks(
    session: AsyncSession,
    llm_client: LLMClient,
    chunks_in: List[ChunkDocumentModel],
) -> list[ChunkOut]:
    embeddings = await embed_texts(
        llm_client=llm_client,
        texts=[
            (c.content if c.data_type == "string" else json.dumps(c.content))
            for c in chunks_in
        ],
    )

    inserted: List[ChunkOut] = []

    for idx, c in enumerate(chunks_in):
        values = {
            "id": uuid4(),
            "document_id": getattr(c, "document", None),
            "workspaces": getattr(c, "workspaces", []),
            "data_type": c.data_type,
            "content": c.content if c.data_type == "string" else None,
            "content_obj": None if c.data_type == "string" else c.content,
            "embedding": embeddings[idx],
            "tags": c.tags or {},
            "user_metadata": c.user_metadata or {},
            "metadata": (
                c.metadata.model_dump()
                if hasattr(c.metadata, "model_dump")
                else dict(c.metadata or {})
            ),
            "created_by": c.created_by,
        }

        row = await insert_returning(session, chunks, values)

        # -------- 统一为 ChunkOut 需要的结构 --------
        rd = dict(row)

        if rd.get("data_type") == "object":
            rd["content"] = rd.pop("content_obj", None)
            rd.pop("content", None) if rd.get("content") is None else None
        else:
            rd["content"] = rd.get("content", None)
            rd.pop("content_obj", None)

        rd.pop("embedding", None)
        rd["embedding"] = None

        rd["tags"] = rd.get("tags") or {}
        rd["user_metadata"] = rd.get("user_metadata") or {}
        rd["metadata"] = rd.get("metadata") or {}

        inserted.append(ChunkOut(**rd))
        # ---------------------------------------

    await session.commit()
    return inserted


# -------- 结构化数据（CSV/JSON） --------
def create_structured_chunks(
    content: bytes,
    document_id: UUID,
    workspace_id: UUID,
    user_id: UUID,
    file_type: str,
) -> List[ChunkDocumentModel]:
    content_decoded = content.decode("utf-8")

    if file_type == "csv":
        df = pd.read_csv(StringIO(content_decoded))

    elif file_type == "json":
        raw = json.loads(content_decoded)
        if isinstance(raw, dict) and "items" in raw and isinstance(raw["items"], list):
            df = pd.DataFrame(raw["items"])
        elif isinstance(raw, list):
            df = pd.DataFrame(raw)
        else:
            df = pd.json_normalize(raw)
    else:
        raise ValueError("Unsupported file type")

    df = df.applymap(validate_and_convert)
    # NaN → None
    df = df.where(pd.notna(df), None)

    data = df.to_dict(orient="records")

    chunks_: List[ChunkDocumentModel] = []
    for idx, obj in enumerate(data):
        chunks_.append(
            ChunkDocumentModel(
                document=document_id,
                workspaces=[workspace_id],
                data_type="object",
                content=obj,
                tags={},
                metadata=ChunkMetadata(
                    length=len(obj.keys()),
                    size=sys.getsizeof(obj),
                    data_source_type="automatic",
                    index=idx,
                ),
                user_metadata={},
                created_by=user_id,
            )
        )
    return chunks_

async def process_structured_chunks(
    session: AsyncSession,
    content: bytes,
    document_id: UUID,
    llm_client: LLMClient,
    workspace_id: UUID,
    user_id: UUID,
    file_type: str,
) -> None:
    err = None
    try:
        prepared = create_structured_chunks(content, document_id, workspace_id, user_id, file_type)
        await add_chunks(session, llm_client, prepared)
    except ValueError:
        raise HTTPException(status_code=400, detail="Unsupported file type selected. Please choose either 'csv' or 'json'.")
    except ParserError:
        raise HTTPException(status_code=400, detail="There was an error parsing the file. Please check the file format and contents.")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Failed to decode the file. Please ensure the file encoding is correct (e.g., UTF-8).")
    except EmptyDataError:
        raise HTTPException(status_code=400, detail="The provided file is empty. Please check the file content.")
    except Exception as e:
        logger.exception("Unexpected error while processing structured file: %s", e)
        raise HTTPException(status_code=400, detail="An unexpected error occurred during file processing.")


# -------- 非结构化（PDF/TXT） --------
def create_unstructured_chunks(content: bytes, document_id: UUID, workspace_id: UUID, user_id: UUID, file_type: str) -> List[ChunkDocumentModel]:
    text_chunks: List[Dict[str, Any]] = []
    if file_type == "pdf":
        reader = PdfReader(BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
        for page_text, page_number in zip(pages, range(len(pages))):
            text_chunks.extend(split_text_into_chunks(page_text, page_number))
    elif file_type == "txt":
        text = content.decode("utf-8")
        text_chunks.extend(split_text_into_chunks(text))
    else:
        raise ValueError("Unsupported file type")

    out: List[ChunkDocumentModel] = []
    for ch in text_chunks:
        out.append(
            ChunkDocumentModel(
                document=document_id, workspaces=[workspace_id], data_type="string",
                content=ch["content"], tags={}, user_metadata={},
                metadata=ChunkMetadata(length=len(ch["content"]), size=sys.getsizeof(ch["content"]),
                                       data_source_type="automatic", **ch["metadata"]),
                created_by=user_id
            )
        )
    return out

async def process_unstructured_chunks(
    session: AsyncSession,
    content: bytes,
    document_id: UUID,
    llm_client: LLMClient,
    workspace_id: UUID,
    user_id: UUID,
    file_type: str,
) -> None:
    err = None
    try:
        prepared = create_unstructured_chunks(content, document_id, workspace_id, user_id, file_type)
        await add_chunks(session, llm_client, prepared)
    except ValueError:
        err = "Unsupported file type selected. Please choose either 'pdf' or 'txt'."
    except ParserError:
        err = "There was an error parsing the file. Please check the file format and contents."
    except UnicodeDecodeError:
        err = "Failed to decode the file. Please ensure the file encoding is correct (e.g., UTF-8)."
    except EmptyDataError:
        err = "The provided file is empty. Please check the file content."
    except Exception:
        err = "An unexpected error occurred during file processing."
    finally:
        if err:
            raise Exception(err)

SUPPORTED_EXTENSIONS: Dict[str, Callable[..., Any]] = {
    "csv": process_structured_chunks,
    "json": process_structured_chunks,
    "pdf": process_unstructured_chunks,
    "txt": process_unstructured_chunks,
}

def _ext_str(ext: "File_Extensions|str") -> str:
    if isinstance(ext, Enum):
        return str(ext.value).lower()
    return str(ext or "").lower()

async def process_chunks(
    session: AsyncSession,
    content: bytes,
    document_id: UUID,
    llm_client: LLMClient,
    workspace_id: UUID,
    user_id: UUID,
    extension: "File_Extensions|str",
) -> None:
    ext = _ext_str(extension)
    handler = SUPPORTED_EXTENSIONS.get(ext)
    if not handler:
        raise HTTPException(status_code=400, detail=f"Unsupported extension: {ext}. Allowed: {', '.join(SUPPORTED_EXTENSIONS)}")
    await handler(
        session=session,
        content=content,
        document_id=document_id,
        llm_client=llm_client,
        workspace_id=workspace_id,
        user_id=user_id,
        file_type=ext,
    )

# -------- 分配到 workspace --------
async def assign_chunks_to_workspace(
    session: AsyncSession,
    chunk_ids: List[UUID],
    workspace_id: UUID,
    user_id: UUID,
) -> ChunkAssignments:
    results = ChunkAssignments(assigned=[], not_found=[], already_assigned=[])
    stmt = sa.select(chunks.c.id, chunks.c.workspaces).where(chunks.c.id.in_(chunk_ids), chunks.c.created_by == user_id)
    rows = (await session.execute(stmt)).mappings().all()
    found_ids = {r["id"] for r in rows}
    for r in rows:
        ws: list[UUID] = r["workspaces"] or []
        if workspace_id in ws:
            results.already_assigned.append(str(r["id"]))
        else:
            await session.execute(sa.update(chunks).where(chunks.c.id == r["id"])
                                  .values(workspaces=sa.func.array_append(chunks.c.workspaces, workspace_id)))
            results.assigned.append(str(r["id"]))
    for cid in chunk_ids:
        if cid not in found_ids:
            results.not_found.append(str(cid))
    await session.commit()
    return results

# -------- 从 workspace 取消分配，同时从 node/triple 的 chunks 中移除 --------
async def unassign_chunks_from_workspace(
    session: AsyncSession,
    chunk_ids: list[UUID],
    workspace_id: UUID,
    user_id: UUID,
) -> ChunkUnassignments:
    results = ChunkUnassignments(unassigned=[], not_found=[], not_found_in_workspace=[])

    rows = (await session.execute(
        sa.select(chunks.c.id, chunks.c.workspaces)
        .where(chunks.c.id.in_(chunk_ids), chunks.c.created_by == user_id)
    )).mappings().all()
    found_ids = {r["id"] for r in rows}

    cids_to_update: list[UUID] = []
    for r in rows:
        ws: list[UUID] = r["workspaces"] or []
        if workspace_id in ws:
            cids_to_update.append(r["id"])
        else:
            results.not_found_in_workspace.append(str(r["id"]))

    for cid in chunk_ids:
        if cid not in found_ids:
            results.not_found.append(str(cid))

    if cids_to_update:
        await session.execute(
            sa.update(chunks)
            .where(chunks.c.id.in_(cids_to_update))
            .values(workspaces=sa.func.array_remove(chunks.c.workspaces, workspace_id))
        )

        for cid in cids_to_update:
            await session.execute(
                sa.text("""
                    UPDATE nodes
                    SET chunks = array_remove(chunks, :cid)
                    WHERE :cid = ANY(chunks)
                """),
                {"cid": cid},
            )
            await session.execute(
                sa.text("""
                    UPDATE triples
                    SET chunks = array_remove(chunks, :cid)
                    WHERE :cid = ANY(chunks)
                """),
                {"cid": cid},
            )

        await session.commit()
        results.unassigned.extend([str(i) for i in cids_to_update])

    return results

# -------- 更新单个 chunk（tags/user_metadata 按 workspace 维度） --------
async def update_chunk(
    session: AsyncSession,
    chunk_id: UUID,
    workspace_id: UUID,
    body: UpdateChunkModel,
    user_id: UUID,
) -> Tuple[str, List[ChunksOutWithWorkspaceDetails] | List[ChunkDocumentModel]]:
    row = (await session.execute(sa.select(chunks).where(chunks.c.id == chunk_id,
                                                        chunks.c.created_by == user_id))).mappings().first()
    if not row:
        return "No chunk found to update", []

    tags = dict(row["tags"] or {})
    um   = dict(row["user_metadata"] or {})
    if body.tags is not None:
        tags[str(workspace_id)] = body.tags
    if body.user_metadata is not None:
        um[str(workspace_id)] = body.user_metadata

    await session.execute(sa.update(chunks).where(chunks.c.id == chunk_id)
                          .values(tags=tags, user_metadata=um))
    await session.commit()

    res = await get_chunks(session, user_id=user_id, include_embeddings=False,
                           filters={"_id": chunk_id}, populate=True)
    return "Chunk updated successfully", res

# -------- 删除单个 chunk（并从 node/triple 中去引用） --------
async def delete_chunk(
    session: AsyncSession,
    chunk_id: UUID,
    user_id: UUID,
) -> ChunkOut | None:
    row = (await session.execute(sa.select(chunks).where(chunks.c.id == chunk_id,
                                                        chunks.c.created_by == user_id))).mappings().first()
    if not row:
        return None

    await session.execute(sa.delete(chunks).where(chunks.c.id == chunk_id, chunks.c.created_by == user_id))

    nodes_tbl = sa.Table("nodes", metadata, autoload_with=session.bind)
    triples_tbl = sa.Table("triples", metadata, autoload_with=session.bind)
    await session.execute(sa.update(nodes_tbl).values(chunks=sa.func.array_remove(nodes_tbl.c.chunks, chunk_id)))
    await session.execute(sa.update(triples_tbl).values(chunks=sa.func.array_remove(triples_tbl.c.chunks, chunk_id)))

    await session.commit()
    return ChunkOut(**{k: v for k, v in dict(row).items() if k != "embedding"})

async def rebuild_chunk_embeddings(
    session: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID | None = None,
    chunk_ids: list[UUID] | None = None,
    force: bool = False,
    llm_client: LLMClient,
    batch_size: int = 64,
) -> dict:
    where = [chunks.c.created_by == user_id]

    where.append(sa.func.lower(chunks.c.data_type).in_(["string", "text"]))

    if workspace_id:
        ws_bind = sa.bindparam("ws_id", workspace_id)
        where.append(sa.cast(ws_bind, UUIDT) == sa.any_(chunks.c.workspaces))

    if chunk_ids:
        where.append(chunks.c.id.in_(chunk_ids))

    if not force:
        where.append(chunks.c.embedding.is_(None))

    rows = (await session.execute(
        sa.select(chunks.c.id, chunks.c.content)
          .where(*where)
          .order_by(chunks.c.created_at.asc(), chunks.c.id.asc())
    )).all()

    ids = [r.id for r in rows]
    texts = [str(r.content or "") for r in rows]

    updated, skipped = 0, 0
    for i in range(0, len(texts), batch_size):
        batch_ids = ids[i:i+batch_size]
        batch_txt = texts[i:i+batch_size]

        todo = [(bid, t) for bid, t in zip(batch_ids, batch_txt) if t.strip()]
        if not todo:
            skipped += len(batch_ids)
            continue

        vecs = await embed_texts(llm_client, [t for _, t in todo])
        for (bid, _), v in zip(todo, vecs):
            await session.execute(
                sa.update(chunks)
                  .where(chunks.c.id == bid)
                  .values(embedding=v, updated_at=sa.func.now())
            )
            updated += 1

    await session.commit()
    return {"total": len(ids), "updated": updated, "skipped": skipped}