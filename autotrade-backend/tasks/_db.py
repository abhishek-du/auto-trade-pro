# Celery-safe async DB session factory.
#
# SQLAlchemy's default connection pool (QueuePool) caches asyncpg connections
# that are bound to the event loop they were created on.  Celery prefork workers
# run each task in asyncio.run(), which creates AND DESTROYS a fresh event loop
# per invocation.  When the loop closes, any pooled connections from the previous
# invocation belong to a dead loop — triggering MissingGreenlet / "Fatal error on
# SSL transport" during pool teardown.
#
# NullPool sidesteps this entirely: no connection is ever cached, every
# `async with session:` block opens a fresh asyncpg connection and closes it
# before asyncio.run() shuts the loop.  No stale state, no teardown errors.

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool


@asynccontextmanager
async def celery_session():
    """Yield a fresh AsyncSession backed by a NullPool engine.

    Usage inside a Celery task coroutine::

        async with celery_session() as session:
            await do_work(session)
            await session.commit()
    """
    from utils.config import settings

    engine = create_async_engine(
        make_url(settings.DATABASE_URL),
        poolclass=NullPool,
        connect_args={"statement_cache_size": 0},
    )
    Session = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    try:
        async with Session() as session:
            yield session
    finally:
        # dispose() is a no-op for NullPool but keeps the interface consistent.
        await engine.dispose()
