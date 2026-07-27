"""Main entrypoint (PG-only)."""

import logging
from contextlib import asynccontextmanager
from logging import basicConfig
from pathlib import Path
from typing import Annotated, Any

import logfire
from asgi_correlation_id import CorrelationIdMiddleware
from asgi_correlation_id.context import correlation_id
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from whyhow_api import __version__
from whyhow_api.config import Settings
from whyhow_api.custom_logging import configure_logging
from whyhow_api.middleware import RateLimiter
from whyhow_api.routers import (
    chunks,
    documents,
    graphs,
    nodes,
    queries,
    rules,
    schemas,
    tasks,
    triples,
    users,
    workspaces,
)
from whyhow_api.database import connect_to_pg, close_pg
from whyhow_api.dependencies import get_settings, get_pg

logger = logging.getLogger("whyhow_api.main")


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore
    settings = app.dependency_overrides.get(get_settings, get_settings)()

    configure_logging(project_log_level=settings.dev.log_level)
    basicConfig(handlers=[logfire.LogfireLoggingHandler()])

    await connect_to_pg(settings)
    logger.info("Connected to openGauss/Postgres.")
    try:
        yield
    finally:
        await close_pg()

settings_ = get_settings()
logfire_token = (
    None if settings_.logfire.token is None else settings_.logfire.token.get_secret_value()
)
logfire.configure(token=logfire_token, send_to_logfire="if-token-present", console=False)

app = FastAPI(
    title="WhyHow API",
    summary="RAG with knowledge graphs",
    version=__version__,
    lifespan=lifespan,
    openapi_url=get_settings().dev.openapi_url,
)
logfire.instrument_fastapi(app)

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    return await http_exception_handler(
        request,
        HTTPException(
            500,
            "Internal Server Error",
            headers={"X-Request-ID": correlation_id.get() or ""},
        ),
    )

app.add_middleware(RateLimiter)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(CorrelationIdMiddleware)

app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static")

app.include_router(workspaces.router)
app.include_router(schemas.router)
app.include_router(graphs.router)
app.include_router(triples.router)
app.include_router(nodes.router)
app.include_router(documents.router)
app.include_router(chunks.router)
app.include_router(users.router)
app.include_router(queries.router)
app.include_router(rules.router)
app.include_router(tasks.router)

@app.get("/")
def root() -> str:
    return f"Welcome to version {__version__} of the WhyHow API."

@app.get("/db")
async def database(session: AsyncSession = Depends(get_pg)) -> str:
    await session.execute(sa.text("SELECT 1"))
    return "Connected to openGauss (pg)."

@app.get("/settings")
def settings(settings: Annotated[Settings, Depends(get_settings)]) -> Any:
    return settings
