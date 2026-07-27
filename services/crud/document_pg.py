# services/crud/document_pg.py
import boto3
from typing import Any, Dict, List, Optional, Tuple
import logging
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.sql import func
from sqlalchemy.future import select

from whyhow_api.config import Settings
from whyhow_api.models.common import LLMClient
from whyhow_api.schemas.base import ErrorDetails
from whyhow_api.services.crud.base_pg import (
    delete_where,
    get_one as pg_get_one,
    insert_returning,
    list_rows,
    update_returning,
)


logger = logging.getLogger(__name__)

metadata = sa.MetaData()

documents = sa.Table(
    "documents",
    metadata,
    sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
    sa.Column("created_by", PGUUID(as_uuid=True), nullable=False),
    sa.Column("status", sa.String(32), nullable=False, server_default="uploaded"),
    sa.Column("metadata", JSONB, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
)

workspaces = sa.Table(
    "workspaces",
    metadata,
    sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
    sa.Column("name", sa.String(255), nullable=False),
    sa.Column("created_by", PGUUID(as_uuid=True), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
)

document_workspaces = sa.Table(
    "document_workspaces",
    metadata,
    sa.Column("document_id", PGUUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", PGUUID(as_uuid=True), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    sa.PrimaryKeyConstraint("document_id", "workspace_id"),
)

chunks = sa.Table(
    "chunks",
    metadata,
    sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
    sa.Column("document_id", PGUUID(as_uuid=True), nullable=False),
    sa.Column("created_by", PGUUID(as_uuid=True), nullable=False),
    sa.Column("workspaces", sa.ARRAY(PGUUID(as_uuid=True)), nullable=True),
)

def _group_documents(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id: Dict[UUID, Dict[str, Any]] = {}
    for r in rows:
        doc_id: UUID = r["id"]
        if doc_id not in by_id:
            by_id[doc_id] = {
                "id": doc_id,
                "created_by": r["created_by"],
                "status": r["status"],
                "metadata": r.get("metadata"),
                "created_at": r.get("created_at"),
                "updated_at": r.get("updated_at"),
                "workspaces": [],
            }
        if r.get("ws_id"):
            by_id[doc_id]["workspaces"].append({"_id": r["ws_id"], "name": r["ws_name"]})
    return list(by_id.values())

# ---------- 创建 ----------
async def create_document(
    session: AsyncSession,
    user_id: UUID,
    *,
    status: str = "uploaded",
    metadata: Optional[Dict[str, Any]] = None,
    workspace_id: Optional[UUID] = None,
) -> Dict[str, Any]:
    """
    新建一条 document 记录，并可选绑定到 workspace。
    返回值与 get_document 一致（包含 workspace 信息）。
    """
    doc_id = uuid4()

    values = {
        "id": doc_id,
        "created_by": user_id,
        "status": status or "uploaded",
        "metadata": metadata or {},
    }
    await session.execute(sa.insert(documents).values(**values))

    if workspace_id:
        ws_ok = await session.execute(
            sa.select(workspaces.c.id).where(
                workspaces.c.id == workspace_id,
                workspaces.c.created_by == user_id,
            )
        )
        if ws_ok.scalar_one_or_none() is not None:
            stmt = pg_insert(document_workspaces).values(
                document_id=doc_id,
                workspace_id=workspace_id,
            ).on_conflict_do_nothing(
                index_elements=[
                    document_workspaces.c.document_id,
                    document_workspaces.c.workspace_id,
                ]
            )
            await session.execute(stmt)
        else:
            logger.warning(
                "Workspace %s not found or not owned by user %s; skip binding.",
                workspace_id,
                user_id,
            )

    await session.commit()
    created = await get_document(session, user_id=user_id, doc_id=doc_id)
    return created

# ---------- 查询 ----------
async def get_documents(
    session: AsyncSession,
    user_id: UUID,
    filters: Dict[str, Any] | None = None,
    skip: int = 0,
    limit: int = 10,
    order: int = 1,
) -> List[Dict[str, Any]] | None:
    """
    列出用户的文档（联表拿 workspace 名称，按 created_at & id 排序/分页）
    """
    where = [documents.c.created_by == user_id]
    if filters:
        if "status" in filters:
            where.append(documents.c.status == filters["status"])

    ws_join = documents.outerjoin(
        document_workspaces, documents.c.id == document_workspaces.c.document_id
    ).outerjoin(
        workspaces, document_workspaces.c.workspace_id == workspaces.c.id
    )

    stmt = (
        sa.select(
            documents.c.id,
            documents.c.created_by,
            documents.c.status,
            documents.c.metadata,
            documents.c.created_at,
            documents.c.updated_at,
            workspaces.c.id.label("ws_id"),
            workspaces.c.name.label("ws_name"),
        )
        .select_from(ws_join)
        .where(*where)
        .order_by(
            documents.c.created_at.asc() if order == 1 else documents.c.created_at.desc(),
            documents.c.id.asc() if order == 1 else documents.c.id.desc(),
        )
        .offset(skip)
    )
    if limit >= 0:
        stmt = stmt.limit(limit)

    rows = await list_rows(session, stmt)
    if not rows:
        return None
    return _group_documents(rows)


async def get_document(
    session: AsyncSession,
    user_id: UUID,
    doc_id: UUID,
) -> Dict[str, Any] | None:
    """
    获取单个文档（联表 workspace）
    """
    ws_join = documents.outerjoin(
        document_workspaces, documents.c.id == document_workspaces.c.document_id
    ).outerjoin(
        workspaces, document_workspaces.c.workspace_id == workspaces.c.id
    )

    stmt = (
        sa.select(
            documents.c.id,
            documents.c.created_by,
            documents.c.status,
            documents.c.metadata,
            documents.c.created_at,
            documents.c.updated_at,
            workspaces.c.id.label("ws_id"),
            workspaces.c.name.label("ws_name"),
        )
        .select_from(ws_join)
        .where(documents.c.id == doc_id, documents.c.created_by == user_id)
    )

    rows = await list_rows(session, stmt)
    if not rows:
        return None
    return _group_documents(rows)[0]

async def list_documents(
    session: AsyncSession,
    user_id: UUID,
    skip: int = 0,
    limit: int = 10,
    order: int = 1,
    filters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    列出用户的文档，支持分页和排序。
    """
    where = [documents.c.created_by == user_id]
    if filters:
        if "status" in filters:
            where.append(documents.c.status == filters["status"])

    ws_join = documents.outerjoin(
        document_workspaces, documents.c.id == document_workspaces.c.document_id
    ).outerjoin(
        workspaces, document_workspaces.c.workspace_id == workspaces.c.id
    )

    stmt = (
        sa.select(
            documents.c.id,
            documents.c.created_by,
            documents.c.status,
            documents.c.metadata,
            documents.c.created_at,
            documents.c.updated_at,
            workspaces.c.id.label("ws_id"),
            workspaces.c.name.label("ws_name"),
        )
        .select_from(ws_join)
        .where(*where)
        .order_by(
            documents.c.created_at.asc() if order == 1 else documents.c.created_at.desc(),
            documents.c.id.asc() if order == 1 else documents.c.id.desc(),
        )
        .offset(skip)
    )
    if limit >= 0:
        stmt = stmt.limit(limit)

    rows = await list_rows(session, stmt)
    if not rows:
        return []
    return _group_documents(rows)


# ---------- 更新 ----------
async def update_document(
    session: AsyncSession,
    user_id: UUID,
    doc_id: UUID,
    update_body: Dict[str, Any],
) -> Dict[str, Any] | None:
    """
    更新文档（状态 / metadata 等）；为了简单与可控，这里采用“整体字段覆盖”的策略：
      - 如果 update_body 含有 'status' 则更新 status
      - 如果含有 'metadata'（dict）则整体替换 metadata
    """
    values: Dict[str, Any] = {"updated_at": func.now()}
    if "status" in update_body:
        values["status"] = update_body["status"]
    if "metadata" in update_body:
        values["metadata"] = update_body["metadata"]

    updated = await update_returning(
        session,
        documents,
        where={"id": doc_id, "created_by": user_id},
        values=values,
    )
    await session.commit()
    return updated

async def update_document_state_errors(
    session: AsyncSession,
    user_id: UUID,
    doc_id: UUID,
    status_value: Optional[str],
    errors: Optional[List[dict]],
) -> Dict[str, Any] | None:
    """
    更新文档的状态和错误信息。
    """
    values: Dict[str, Any] = {"updated_at": func.now()}
    if status_value:
        values["status"] = status_value
    if errors is not None:
        values["metadata"] = {"errors": errors}

    updated = await update_returning(
        session,
        documents,
        where={"id": doc_id, "created_by": user_id},
        values=values,
    )
    await session.commit()
    return updated

# ---------- 分配/取消分配 ----------
async def assign_documents_to_workspace(
    session: AsyncSession,
    document_ids: List[UUID],
    workspace_id: UUID,
    user_id: UUID,
) -> List[Dict[str, Any]]:
    """
    将文档分配到指定工作区。
    """
    results = []
    for doc_id in document_ids:
        doc = await pg_get_one(session, documents, {"id": doc_id, "created_by": user_id})
        if not doc:
            continue

        doc_ws = await session.execute(
            sa.select(document_workspaces.c.workspace_id)
            .where(document_workspaces.c.document_id == doc_id)
        )
        if workspace_id not in [r[0] for r in doc_ws.fetchall()]:
            await session.execute(
                document_workspaces.insert().values(
                    document_id=doc_id, workspace_id=workspace_id
                )
            )
            results.append({"document_id": doc_id, "workspace_id": workspace_id})

    await session.commit()
    return results

async def unassign_documents_from_workspace(
    session: AsyncSession,
    document_ids: List[UUID],
    workspace_id: UUID,
    user_id: UUID,
) -> List[Dict[str, Any]]:
    """
    将文档从指定工作区中取消分配。
    """
    results = []
    for doc_id in document_ids:
        doc = await pg_get_one(session, documents, {"id": doc_id, "created_by": user_id})
        if not doc:
            continue

        stmt = sa.select(document_workspaces).where(
            document_workspaces.c.document_id == doc_id,
            document_workspaces.c.workspace_id == workspace_id,
        )
        row = await session.execute(stmt)
        if row:
            await session.execute(
                sa.delete(document_workspaces).where(
                    document_workspaces.c.document_id == doc_id,
                    document_workspaces.c.workspace_id == workspace_id,
                )
            )
            results.append({"document_id": doc_id, "workspace_id": workspace_id})

    await session.commit()
    return results

async def delete_document_from_s3(
    user_id: UUID, filename: str, settings: Settings
) -> None:
    try:
        s3_client = boto3.client("s3")
        s3_client.delete_object(
            Bucket=settings.aws.s3.bucket,
            Key=f"{user_id}/{filename}",
        )
    except Exception as e:
        logger.error(f"Failed to delete {filename} from S3 for user {user_id}: {e}")

async def delete_document(
    session: AsyncSession,
    user_id: UUID,
    doc_id: UUID,
    settings: Settings,
) -> Dict[str, Any] | None:
    """
    删除文档：先取文档信息（含 metadata.filename），事务内删除关联 chunks、映射表、文档本身；
    成功后再删 S3。
    """
    doc = await pg_get_one(session, documents, {"id": doc_id, "created_by": user_id})
    if not doc:
        return None

    try:
        await delete_where(session, chunks, {"document_id": doc_id, "created_by": user_id})
        await delete_where(session, document_workspaces, {"document_id": doc_id})
        await delete_where(session, documents, {"id": doc_id, "created_by": user_id})
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("Error deleting document and related rows")
        raise

    try:
        filename = (doc.get("metadata") or {}).get("filename")
        if filename:
            await delete_document_from_s3(user_id=user_id, filename=filename, settings=settings)
    except Exception:
        logger.exception("S3 deletion failed (ignored)")

    return doc

# ---------- 读取 S3 内容 ----------
async def get_document_content(
    session: AsyncSession,
    user_id: UUID,
    doc_id: UUID,
    bucket: str,
) -> Tuple[bytes | None, Dict[str, Any] | None]:
    doc = await pg_get_one(session, documents, {"id": doc_id, "created_by": user_id})
    if not doc:
        return None, None
    md = doc.get("metadata") or {}
    filename = md.get("filename")
    if not filename:
        return None, doc

    s3_client = boto3.client("s3")
    resp = s3_client.get_object(Bucket=bucket, Key=f"{user_id}/{filename}")
    content = resp["Body"].read()
    return content, doc

# ---------- 处理文档（状态流转 + 调用 chunk 处理） ----------
async def process_document(
    session: AsyncSession,
    user_id: UUID,
    doc_id: UUID,
    llm_client: LLMClient,
    bucket: str,
) -> None:
    """
    按 Mongo 版的逻辑做状态流转：
    - uploaded/failed -> processing -> processed
    - 如果处理中报错 -> failed 并记录 errors
    注：真正的“切 chunk + 入库”将在迁移 chunks_pg.py 时实现，这里留调用点。
    """
    content, doc = await get_document_content(session, user_id, doc_id, bucket)
    if not content or not doc:
        raise ValueError("Document not found or content missing.")

    status = doc.get("status")
    if status == "processing":
        raise ValueError("Document is currently being processed.")
    if status not in ("uploaded", "failed"):
        raise ValueError("Document has already been processed.")

    await update_document(session, user_id, doc_id, {"status": "processing"})

    error_message: Optional[str] = None
    try:
        await update_document(session, user_id, doc_id, {"status": "processed"})
    except Exception as e:
        error_message = str(e)
        logger.error(f"Error processing document: {e}")
        await update_document(
            session,
            user_id,
            doc_id,
            {
                "status": "failed",
                "metadata": {
                    **(doc.get("metadata") or {}),
                    "errors": [
                        ErrorDetails(message=error_message, level="critical").model_dump()
                    ],
                },
            },
        )
        raise

async def get_document_with_workspace(
    session: AsyncSession, document_id: UUID, user_id: UUID
) -> dict | None:
    stmt = (
        select(
            documents.c.id,
            documents.c.created_by,
            documents.c.status,
            documents.c.metadata,
            documents.c.created_at,
            documents.c.updated_at,
            workspaces.c.id.label("workspace_id"),
            workspaces.c.name.label("workspace_name"),
        )
        .select_from(documents)
        .join(
            document_workspaces,
            documents.c.id == document_workspaces.c.document_id,
        )
        .join(workspaces, document_workspaces.c.workspace_id == workspaces.c.id)
        .where(documents.c.id == document_id, documents.c.created_by == user_id)
    )

    result = await session.execute(stmt)
    row = result.mappings().first()

    if row:
        return dict(row)
    else:
        return None