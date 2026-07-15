# ============================================================
# PostgreSQL Connection — Async SQLAlchemy Engine
#
# Provides the async engine, session factory, and startup helper
# for the optional PostgreSQL backend.  When DATABASE_URL is not
# set (or is empty), every export resolves to None / False so the
# existing SQLite path continues to work without changes.
#
# USAGE:
#   from src.database.postgres import get_session, init_postgres
#
#   # FastAPI dependency
#   async def my_route(session=Depends(get_session)):
#       ...
#
#   # Application startup
#   await init_postgres()
# ============================================================

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src import config

logger = logging.getLogger(__name__)


# ── Engine & Session Factory ──────────────────────────────────────────────────
# Both are set to None when DATABASE_URL is absent, keeping the module
# importable without a running Postgres instance.

_database_url: str | None = getattr(config, "DATABASE_URL", None) or None



engine = (
    create_async_engine(
        _database_url,
        echo=False,
        pool_size=15,
        max_overflow=20,
        pool_recycle=1800,
    )
    if _database_url
    else None
)

AsyncSessionLocal = (
    async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    if engine
    else None
)


# ── Dependency — async session generator ──────────────────────────────────────

@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager that yields an ``AsyncSession``.

    Intended for use as a FastAPI dependency::

        @router.get("/items")
        async def list_items(session=Depends(get_session)):
            ...

    Commits on success, rolls back on exception, and always closes.
    Raises ``RuntimeError`` if PostgreSQL is not configured.
    """
    if AsyncSessionLocal is None:
        raise RuntimeError(
            "PostgreSQL is not configured. "
            "Set DATABASE_URL in your .env file to enable Postgres."
        )

    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Table Initialisation ──────────────────────────────────────────────────────

async def init_postgres() -> None:
    """
    Create all tables defined in ``models.Base.metadata``.

    Safe to call multiple times — SQLAlchemy's ``create_all`` uses
    ``CREATE TABLE IF NOT EXISTS`` under the hood.

    Called once during application startup (only when Postgres is enabled).
    """
    if engine is None:
        logger.debug("init_postgres() skipped — DATABASE_URL is not set.")
        return

    try:
        from src.database.models import Base  # noqa: WPS433 (nested import)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("PostgreSQL tables created / verified successfully.")
    except Exception as exc:
        logger.error("Failed to initialise PostgreSQL tables: %s", exc)
        raise


# ── Helper ────────────────────────────────────────────────────────────────────

def is_postgres_configured() -> bool:
    """Return ``True`` if ``DATABASE_URL`` is set and the engine was created."""
    return engine is not None
