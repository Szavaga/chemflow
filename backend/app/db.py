from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.models.orm import Base

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Add enrichment columns to simulation_results if they were added after
        # the table was first created (create_all does not ALTER existing tables).
        await conn.execute(text("""
            ALTER TABLE simulation_results
                ADD COLUMN IF NOT EXISTS process_metrics     JSONB,
                ADD COLUMN IF NOT EXISTS stream_annotations  JSONB,
                ADD COLUMN IF NOT EXISTS solver_diagnostics  JSONB,
                ADD COLUMN IF NOT EXISTS process_summary     TEXT,
                ADD COLUMN IF NOT EXISTS node_summaries      JSONB
        """))


async def get_db() -> AsyncSession:  # type: ignore[return]
    async with AsyncSessionLocal() as session:
        yield session
