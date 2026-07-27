"""Dependencies for FastAPI (PG-only)."""

import logging
from functools import cache
from typing import Any, AsyncGenerator, Dict, List
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from openai import AsyncAzureOpenAI, AsyncOpenAI
from pydantic import ValidationError

from whyhow_api.config import Settings
from whyhow_api.database import get_pg_session
from whyhow_api.models.common import LLMClient
from whyhow_api.schemas.chunks import ChunkDocumentModel
from whyhow_api.schemas.documents import DocumentOutWithWorkspaceDetails
from whyhow_api.schemas.graphs import CreateGraphBody, DetailedGraphDocumentModel, GraphDocumentModel
from whyhow_api.schemas.nodes import NodeDocumentModel
from whyhow_api.schemas.queries import QueryDocumentModel
from whyhow_api.schemas.schemas import SchemaDocumentModel, SchemaOutWithWorkspaceDetails
from whyhow_api.schemas.triples import TripleDocumentModel
from whyhow_api.schemas.users import BYOAzureOpenAIMetadata, BYOOpenAIMetadata, ProviderConfig
from whyhow_api.schemas.workspaces import WorkspaceDocumentModel

from whyhow_api.services.crud.user_pg import get_user_by_api_key
from whyhow_api.services.crud.workspace_pg import get_workspace
from whyhow_api.services.crud.schema_pg import get_schema_with_workspace
from whyhow_api.services.crud.document_pg import get_document_with_workspace
from whyhow_api.services.crud.chunks_pg import get_chunk_basic
from whyhow_api.services.crud.node_pg import get_node
from whyhow_api.services.crud.triple_pg import get_triple
from whyhow_api.services.crud.graph_pg import get_graph as get_graph_pg
from whyhow_api.services.crud.queries_pg import get_query

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)

@cache
def get_settings() -> Settings:
    return Settings()

# ---------- PG 会话 ----------
async def get_pg() -> AsyncGenerator[AsyncSession, None]:
    async with get_pg_session() as session:
        yield session

# ---------- 用户鉴权 ----------
async def get_user_pg(
    request: Request,
    api_key: str | None = Depends(api_key_header),
    session: AsyncSession = Depends(get_pg),
) -> UUID:
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing x-api-key")
    row = await get_user_by_api_key(session, api_key)
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    return row["id"]  # UUID

# ---------- LLM 客户端 ----------
async def get_llm_client(
    user_id: UUID = Depends(get_user_pg),
    session: AsyncSession = Depends(get_pg),
    settings: Settings = Depends(get_settings),
) -> LLMClient:
    """
    优先：读 users.providers(JSONB) 里的 BYO 配置；
    退化：使用全局 Settings（openai）配置。
    """
    row = (await session.execute(
        sa.text("SELECT providers FROM users WHERE id = :uid"),
        {"uid": str(user_id)}
    )).mappings().first()

    providers_json = row["providers"] if row and "providers" in row else None
    if providers_json:
        try:
            provider_config = ProviderConfig.model_validate({"providers": providers_json})
            llm_providers = [p for p in provider_config.providers if p.type == "llm"]
            if llm_providers:
                lp = llm_providers[0]
                if lp.value == "byo-azure-openai":
                    meta = BYOAzureOpenAIMetadata.model_validate(lp.metadata["byo-azure-openai"])
                    if not lp.api_key or not meta.api_version or not meta.azure_endpoint or not meta.language_model_name or not meta.embedding_name:
                        raise HTTPException(status_code=401, detail="Invalid BYO Azure OpenAI config")
                    client = AsyncAzureOpenAI(api_key=lp.api_key, api_version=meta.api_version, azure_endpoint=meta.azure_endpoint)
                    return LLMClient(client, meta)
                elif lp.value == "byo-openai":
                    meta = BYOOpenAIMetadata.model_validate(lp.metadata["byo-openai"])
                    if not lp.api_key:
                        raise HTTPException(status_code=401, detail="Missing BYO OpenAI key")
                    client = AsyncOpenAI(api_key=lp.api_key)
                    return LLMClient(client, meta)
        except ValidationError as e:
            logger.error(f"Invalid provider config in PG: {e}")

    if settings.generative.openai.api_key is None:
        raise HTTPException(status_code=401, detail="No LLM provider configured")
    client = AsyncOpenAI(api_key=settings.generative.openai.api_key.get_secret_value())
    meta = BYOOpenAIMetadata(
        language_model_name=settings.generative.openai.model,
        embedding_name=settings.embedding.openai.model,
        temperature=settings.generative.openai.temperature,
        max_tokens=settings.generative.openai.max_tokens,
    )
    return LLMClient(client, meta)

# ---------- 资源校验（PG 版） ----------
async def valid_workspace_id(workspace_id: UUID, user_id: UUID = Depends(get_user_pg), session: AsyncSession = Depends(get_pg)) -> WorkspaceDocumentModel:
    ws = await get_workspace(session, workspace_id, user_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return WorkspaceDocumentModel.model_validate(ws)

async def valid_schema_id(schema_id: UUID, user_id: UUID = Depends(get_user_pg), session: AsyncSession = Depends(get_pg)) -> SchemaOutWithWorkspaceDetails:
    sc = await get_schema_with_workspace(session, schema_id, user_id)
    if sc is None:
        raise HTTPException(status_code=404, detail="Schema not found")
    return SchemaOutWithWorkspaceDetails.model_validate(sc)

async def valid_chunk_id(chunk_id: UUID, user_id: UUID = Depends(get_user_pg), session: AsyncSession = Depends(get_pg)) -> ChunkDocumentModel:
    ck = await get_chunk_basic(session, chunk_id, user_id)
    if ck is None:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return ChunkDocumentModel.model_validate(ck)

async def valid_node_id(node_id: UUID, user_id: UUID = Depends(get_user_pg), session: AsyncSession = Depends(get_pg)) -> NodeDocumentModel:
    nd = await get_node(session, node_id, user_id)
    if nd is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return NodeDocumentModel.model_validate(nd)

async def valid_triple_id(triple_id: UUID, user_id: UUID = Depends(get_user_pg), session: AsyncSession = Depends(get_pg)) -> TripleDocumentModel:
    tp = await get_triple(session, triple_id, user_id)
    if tp is None:
        raise HTTPException(status_code=404, detail="Triple not found")
    return TripleDocumentModel.model_validate(tp)

async def valid_graph_id(graph_id: UUID, user_id: UUID = Depends(get_user_pg), session: AsyncSession = Depends(get_pg)) -> DetailedGraphDocumentModel:
    gp = await get_graph_pg(session, graph_id, user_id, include_details=True)
    if gp is None:
        raise HTTPException(status_code=404, detail="Graph not found")
    return DetailedGraphDocumentModel.model_validate(gp)

async def valid_public_graph_id(graph_id: UUID, session: AsyncSession = Depends(get_pg)) -> DetailedGraphDocumentModel:
    gp = await get_graph_pg(session, graph_id, user_id=None, public=True, include_details=True)
    if gp is None:
        raise HTTPException(status_code=404, detail="Graph not found")
    return DetailedGraphDocumentModel.model_validate(gp)

async def valid_query_id(query_id: UUID, user_id: UUID = Depends(get_user_pg), session: AsyncSession = Depends(get_pg)) -> QueryDocumentModel:
    q = await get_query(session, query_id, user_id)
    if q is None:
        raise HTTPException(status_code=404, detail="Query not found")
    return QueryDocumentModel.model_validate(q)

async def valid_document_id(document_id: UUID, user_id: UUID = Depends(get_user_pg), session: AsyncSession = Depends(get_pg)) -> DocumentOutWithWorkspaceDetails:
    doc = await get_document_with_workspace(session, document_id, user_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentOutWithWorkspaceDetails.model_validate(doc)

async def valid_create_graph(body: CreateGraphBody, user_id: UUID = Depends(get_user_pg), session: AsyncSession = Depends(get_pg)) -> bool:
    ws = await get_workspace(session, body.workspace, user_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    if body.schema_ is not None:
        sc = await get_schema_with_workspace(session, body.schema_, user_id)
        if sc is None:
            raise HTTPException(status_code=404, detail="Schema not found.")
    filters: Dict[str, Any] = {"name": body.name, "workspace": body.workspace}
    if body.schema_:
        filters["schema"] = body.schema_
    exist = await get_graph_pg(session, None, user_id, filters=filters)
    if exist:
        raise HTTPException(status_code=409, detail="Graph already exists or is being created.")
    return True
