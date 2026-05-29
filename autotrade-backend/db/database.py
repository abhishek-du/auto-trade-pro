from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from utils.config import settings

# make_url() decodes %40/@, %23/# etc. in the password before handing to asyncpg
_db_url = make_url(settings.DATABASE_URL)

# Supabase transaction-mode pooler (PgBouncer, port 6543) reassigns a backend
# connection per transaction and returns it to its own pool afterwards. Layering
# SQLAlchemy's QueuePool on top means cached connections go stale between
# transactions → "connection was closed in the middle of operation". NullPool
# delegates all pooling to PgBouncer: every checkout is a fresh connection,
# closed immediately after use. This matches tasks/_db.py (Celery workers).
#
# statement_cache_size=0 is also required for the transaction-mode pooler.
engine = create_async_engine(
    _db_url,
    echo=False,
    poolclass=NullPool,
    connect_args={"statement_cache_size": 0},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Shared declarative base — all ORM models inherit from this."""
    pass


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields a session, commits on success, rolls back on error.

    Cleanup is guarded so that a dropped connection (common with PgBouncer
    transaction-mode pooling) can't raise a confusing secondary
    PendingRollbackError that masks the original failure.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            try:
                await session.rollback()
            except Exception:
                pass  # connection already dead — nothing to roll back
            raise
        finally:
            try:
                await session.close()
            except Exception:
                pass


async def init_db() -> None:
    """Create all tables and ensure enum values are in sync.

    Called at startup in main.py lifespan.  Non-fatal — the app starts even if
    the DB is temporarily unreachable (e.g. Supabase pooler still propagating).
    """
    from sqlalchemy import text

    # ALTER TYPE ADD VALUE must run outside a transaction (autocommit).
    # Safe to re-run — IF NOT EXISTS is idempotent.
    async with engine.connect() as conn:
        ac = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for value in ("STOPPED",):
            await ac.execute(
                text(f"ALTER TYPE tradestatus ADD VALUE IF NOT EXISTS '{value}'")
            )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
