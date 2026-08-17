from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from utils.config import settings

# make_url() decodes %40/@, %23/# etc. in the password before handing to asyncpg
_db_url = make_url(settings.DATABASE_URL)

# Pooling depends on WHAT we're connected to.
#
# Against a transaction-mode pooler (Supabase/PgBouncer on :6543) SQLAlchemy
# must NOT pool: that pooler reassigns a backend connection per transaction and
# returns it to its own pool afterwards, so a cached connection goes stale
# between transactions → "connection was closed in the middle of operation".
# statement_cache_size=0 is required there too, since prepared statements don't
# survive the backend swap.
#
# Against a DIRECT Postgres both settings are pure overhead, and this project
# migrated to a local Postgres on :5432 (2026-06-10) without revisiting them.
# Measured 2026-08-17 on the live DB: NullPool + statement_cache_size=0 costs
# 47.9 ms per DB-touching request versus 1.8 ms pooled — a 26x penalty paid on
# EVERY request, because NullPool opens a fresh TCP connection and re-does auth
# and setup each time, and disabling the statement cache makes Postgres re-parse
# every query. That overhead is a large part of why DB-backed endpoints ran
# multi-second while /health (no DB) stayed at ~1 ms.
#
# So: detect the pooler and keep the old behaviour for it; pool otherwise.
# Celery is unaffected either way — tasks/_db.py builds its own NullPool engine
# per task, which is correct there: each task runs its own event loop
# (asyncio.run), and a pooled connection created under one loop cannot be
# reused under the next.
_is_txn_pooler = (
    _db_url.port == 6543
    or "pooler.supabase.com" in (_db_url.host or "")
    or "pgbouncer" in (_db_url.host or "")
)

# Pooling also requires ONE long-lived event loop. uvicorn has that; pytest does
# not — pytest-asyncio builds and closes a loop per test, so a connection pooled
# under one test's loop is torn down after that loop is gone and asyncpg raises
# "RuntimeError: Event loop is closed" during cleanup (seen live in
# test_alert_threading.py). This is the same constraint tasks/_db.py documents
# for Celery, which runs asyncio.run() per task. Detected at import because
# pytest is always imported before the modules under test.
import sys as _sys
_multi_loop_runtime = "pytest" in _sys.modules

if _is_txn_pooler or _multi_loop_runtime:
    engine = create_async_engine(
        _db_url,
        echo=False,
        poolclass=NullPool,
        connect_args={"statement_cache_size": 0},
    )
else:
    engine = create_async_engine(
        _db_url,
        echo=False,
        # Sized for one uvicorn process serving the SPA's polling plus the
        # lifespan background loops; overflow absorbs bursts.
        pool_size=10,
        max_overflow=20,
        # A pooled connection can be killed underneath us (Postgres restart,
        # idle timeout, the container being bounced). pre_ping turns that into
        # a transparent reconnect instead of a failed request; recycle keeps
        # connections from ageing out server-side.
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_timeout=30,
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

    Each ALTER TABLE runs in its own AUTOCOMMIT statement so a concurrent
    backfill (which holds AccessShareLocks on candles) cannot deadlock the
    entire batch. Idempotent: ADD COLUMN IF NOT EXISTS / ALTER TYPE IF NOT EXISTS.
    """
    import asyncio as _asyncio
    from sqlalchemy import text

    # ALTER TYPE ADD VALUE must run in AUTOCOMMIT (PostgreSQL restriction).
    async with engine.connect() as conn:
        ac = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for value in ("STOPPED",):
            await ac.execute(
                text(f"ALTER TYPE tradestatus ADD VALUE IF NOT EXISTS '{value}'")
            )

    # CREATE TABLE (safe in a single transaction — no contention with backfill).
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # ALTER TABLE statements that can deadlock with concurrent inserts:
    # run each one in AUTOCOMMIT so there is no cross-statement lock accumulation.
    # If a single statement deadlocks (e.g. backfill holds a lock momentarily),
    # retry it up to 3× with short backoff rather than failing the entire batch.
    _alter_stmts = (
        "ALTER TABLE market_shortlist ADD COLUMN IF NOT EXISTS upper_circuit_days INTEGER DEFAULT 0",
        "ALTER TABLE market_shortlist ADD COLUMN IF NOT EXISTS volume_surge FLOAT DEFAULT 1.0",
        "ALTER TABLE agent_trades ADD COLUMN IF NOT EXISTS product VARCHAR(10) DEFAULT 'CNC'",
        "ALTER TABLE open_positions ADD COLUMN IF NOT EXISTS product VARCHAR(10) DEFAULT 'CNC'",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS product VARCHAR(10) DEFAULT 'CNC'",
        *[
            f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col}"
            for tbl in ("paper_trades", "open_positions", "agent_trades", "agent_decisions")
            for col in (
                "instrument_type VARCHAR(10) DEFAULT 'EQUITY'",
                "underlying_symbol VARCHAR(30)",
                "strike_price FLOAT",
                "option_type VARCHAR(2)",
                "expiry_date DATE",
                "lot_size INTEGER DEFAULT 1",
            )
        ],
        *[
            f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col}"
            for tbl in ("paper_trades", "open_positions", "agent_trades")
            for col in (
                "contract_multiplier FLOAT DEFAULT 1.0",
                "margin_blocked FLOAT DEFAULT 0.0",
            )
        ],
        "ALTER TABLE paper_trades   ALTER COLUMN symbol TYPE VARCHAR(50)",
        "ALTER TABLE open_positions ALTER COLUMN symbol TYPE VARCHAR(50)",
        "ALTER TABLE simulation_logs ALTER COLUMN symbol TYPE VARCHAR(50)",
        # Strategy source label (2026-07-24) — parallel Pre-Event Expectation
        # Gap strategy attribution. Nullable; existing rows stay NULL ("AI"),
        # the new strategy writes "AI Predict". Additive, backward-compatible.
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS source VARCHAR(20)",
        "ALTER TABLE agent_trades ADD COLUMN IF NOT EXISTS source VARCHAR(20)",
        "CREATE INDEX IF NOT EXISTS ix_pt_source ON paper_trades (source)",
        # Trade attribution columns (0003_trade_attribution) — entry snapshot
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS strategy_name VARCHAR(40)",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS regime_at_entry VARCHAR(20)",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS entry_reason VARCHAR(40)",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS confidence_bucket VARCHAR(8)",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS instrument_segment VARCHAR(12)",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS initial_risk_inr FLOAT",
        # Exit snapshot
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS exit_reason VARCHAR(20)",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS regime_at_exit VARCHAR(20)",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS r_multiple FLOAT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS holding_bars INTEGER",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS holding_hours FLOAT",
        # Excursion summary
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS mfe_abs FLOAT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS mfe_pct FLOAT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS mfe_r FLOAT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS mae_abs FLOAT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS mae_pct FLOAT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS mae_r FLOAT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS max_open_profit FLOAT",
        "ALTER TABLE news_items ADD COLUMN IF NOT EXISTS news_metadata JSONB",
        # Indexes for attribution filters
        "CREATE INDEX IF NOT EXISTS ix_pt_strategy_name  ON paper_trades (strategy_name)",
        "CREATE INDEX IF NOT EXISTS ix_pt_regime_entry   ON paper_trades (regime_at_entry)",
        "CREATE INDEX IF NOT EXISTS ix_pt_conf_bucket    ON paper_trades (confidence_bucket)",
        "CREATE INDEX IF NOT EXISTS ix_pt_instrument_seg ON paper_trades (instrument_segment)",
        "CREATE INDEX IF NOT EXISTS ix_pt_exit_reason    ON paper_trades (exit_reason)",
    )

    async with engine.connect() as conn:
        ac = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for stmt in _alter_stmts:
            for attempt in range(3):
                try:
                    await ac.execute(text(stmt))
                    break
                except Exception as exc:
                    msg = str(exc).lower()
                    if "deadlock" in msg and attempt < 2:
                        await _asyncio.sleep(3 * (attempt + 1))
                        continue
                    # Column already exists or other benign error — move on.
                    break
