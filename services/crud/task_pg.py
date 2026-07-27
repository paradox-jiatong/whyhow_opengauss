# services/crud/task_pg.py
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID, uuid4

from .base_pg import get_one as pg_get_one

metadata = sa.MetaData()

tasks = sa.Table(
    "tasks",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("user_id", PGUUID(as_uuid=True), nullable=False),
    sa.Column("title", sa.String(255), nullable=False),
    sa.Column("description", sa.Text, nullable=True),
    sa.Column("status", sa.String(50), nullable=False, server_default=sa.text("'pending'")),
)

async def get_task(session: AsyncSession, task_id: UUID, created_by: UUID) -> dict | None:
    return await pg_get_one(session, tasks, {"id": str(task_id), "user_id": created_by})

async def create_task(
    session: AsyncSession,
    created_by: UUID,
    title: str,
    description: str | None = None,
) -> dict:
    """创建一条 pending 任务并返回整行"""
    new_id = str(uuid4())
    insert_values = {
        "id": new_id,
        "user_id": created_by,
        "title": title,
        "description": description,
        "status": "pending",
    }

    stmt = (
        tasks.insert()
        .values(**insert_values)
        .returning(*tasks.c)
    )

    row = (await session.execute(stmt)).mappings().one()
    await session.commit()
    return dict(row)
