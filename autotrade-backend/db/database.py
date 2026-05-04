from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from utils.config import settings

# make_url() decodes %40/@, %23/# etc. in the password before handing to asyncpg
_db_url = make_url(settings.DATABASE_URL)

# statement_cache_size=0 is required for Supabase transaction-mode pooler (port 6543)
engine = create_async_engine(
    _db_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
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
    """FastAPI dependency — yields a session, commits on success, rolls back on error."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


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
