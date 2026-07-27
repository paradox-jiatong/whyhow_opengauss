"""Initialize the local openGauss demo schema."""

from __future__ import annotations

import asyncio
import os
from textwrap import dedent

import asyncpg

DEMO_USER_ID = "11111111-1111-1111-1111-111111111111"
DEMO_API_KEY = "demo-api-key"

DDL = [
    """
    CREATE TABLE IF NOT EXISTS users (
      id UUID PRIMARY KEY,
      email VARCHAR(255) UNIQUE NOT NULL,
      username VARCHAR(255) NOT NULL,
      firstname VARCHAR(255) NOT NULL,
      lastname VARCHAR(255) NOT NULL,
      api_key VARCHAR(64) UNIQUE NOT NULL,
      providers JSON,
      active BOOLEAN NOT NULL DEFAULT TRUE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workspaces (
      id UUID PRIMARY KEY,
      name VARCHAR(128) UNIQUE NOT NULL,
      description TEXT,
      created_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schemas (
      id UUID PRIMARY KEY,
      workspace_id UUID NOT NULL,
      created_by UUID NOT NULL,
      name TEXT,
      body JSONB,
      created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS graphs (
      id UUID PRIMARY KEY,
      schema_id UUID NULL,
      workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      created_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      public BOOLEAN NOT NULL DEFAULT FALSE,
      name TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_graphs_created_by ON graphs(created_by)",
    "CREATE INDEX IF NOT EXISTS idx_graphs_workspace_id ON graphs(workspace_id)",
    """
    CREATE TABLE IF NOT EXISTS documents (
      id UUID PRIMARY KEY,
      created_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      status VARCHAR(32) NOT NULL DEFAULT 'uploaded',
      metadata JSONB,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS document_workspaces (
      document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
      workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      PRIMARY KEY (document_id, workspace_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chunks (
      id UUID PRIMARY KEY,
      document_id UUID NULL REFERENCES documents(id) ON DELETE CASCADE,
      workspaces UUID[] NOT NULL,
      data_type TEXT NOT NULL,
      content TEXT,
      content_obj JSON,
      embedding JSON,
      embedding_vector FLOAT8[],
      tags JSON NOT NULL DEFAULT '{}'::JSON,
      user_metadata JSON NOT NULL DEFAULT '{}'::JSON,
      metadata JSON NOT NULL DEFAULT '{}'::JSON,
      created_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS nodes (
      id UUID PRIMARY KEY,
      graph_id UUID NOT NULL,
      name TEXT NOT NULL,
      label TEXT NOT NULL,
      properties JSON NOT NULL DEFAULT '{}'::JSON,
      chunks UUID[] NOT NULL DEFAULT '{}',
      created_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS triples (
      id UUID PRIMARY KEY,
      graph_id UUID NOT NULL,
      head_node_id UUID,
      tail_node_id UUID,
      relation_name TEXT NOT NULL,
      properties JSON NOT NULL DEFAULT '{}'::JSON,
      chunks UUID[] NOT NULL DEFAULT '{}',
      created_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      embedding JSON,
      embedding_vector FLOAT8[],
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
      id CHAR(36) PRIMARY KEY,
      user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      title VARCHAR(255) NOT NULL,
      description TEXT,
      status VARCHAR(50) NOT NULL DEFAULT 'pending',
      created_at TIMESTAMPTZ DEFAULT NOW(),
      updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rules (
      id UUID PRIMARY KEY,
      workspace_id UUID NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      created_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      name TEXT,
      body JSON,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS queries (
      id UUID PRIMARY KEY,
      user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      graph_id UUID,
      status VARCHAR(32) NOT NULL DEFAULT 'pending',
      name TEXT,
      payload JSON,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
]


async def main() -> None:
    conn = await asyncpg.connect(
        host=os.getenv("WHYHOW__OPENGAUSS__HOST", "127.0.0.1"),
        port=int(os.getenv("WHYHOW__OPENGAUSS__PORT", "5432")),
        user=os.getenv("WHYHOW__OPENGAUSS__USER", "gaussdb"),
        password=os.getenv("WHYHOW__OPENGAUSS__PASSWORD", "Enmo@123"),
        database=os.getenv("WHYHOW__OPENGAUSS__DATABASE", "postgres"),
    )
    try:
        for statement in DDL:
            await conn.execute(dedent(statement).strip())

        for table_name in ("chunks", "triples"):
            has_embedding_vector = await conn.fetchval(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = $1 AND column_name = 'embedding_vector'
                LIMIT 1
                """,
                table_name,
            )
            if not has_embedding_vector:
                await conn.execute(f"ALTER TABLE {table_name} ADD COLUMN embedding_vector FLOAT8[]")

        await conn.execute(
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

        has_created_by = await conn.fetchval(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'workspaces' AND column_name = 'created_by'
            LIMIT 1
            """
        )
        if not has_created_by:
            await conn.execute("ALTER TABLE workspaces ADD COLUMN created_by UUID")

        has_legacy_user_id = await conn.fetchval(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'workspaces' AND column_name = 'user_id'
            LIMIT 1
            """
        )
        if has_legacy_user_id:
            await conn.execute("UPDATE workspaces SET created_by = user_id WHERE created_by IS NULL AND user_id IS NOT NULL")
            await conn.execute("ALTER TABLE workspaces ALTER COLUMN user_id DROP NOT NULL")

        existing = await conn.fetchval("SELECT id FROM users WHERE api_key = $1", DEMO_API_KEY)
        if existing is None:
            await conn.execute(
                """
                INSERT INTO users (id, email, username, firstname, lastname, api_key)
                VALUES ($1, 'demo@example.com', 'demo', 'Demo', 'User', $2)
                """,
                DEMO_USER_ID,
                DEMO_API_KEY,
            )
    finally:
        await conn.close()

    print(f"Initialized demo schema. API key: {DEMO_API_KEY}")


if __name__ == "__main__":
    asyncio.run(main())
