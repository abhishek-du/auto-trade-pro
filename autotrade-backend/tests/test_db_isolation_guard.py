"""The fail-closed guard must land the suite on the allowlisted TEST database.

These assertions are the whole point of the guard: if they ever fail, the suite
is talking to something other than the database it was told to use — and the
one time that went unnoticed it put 144 rows into production simulation_logs.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url


def test_settings_point_at_the_allowlisted_test_database():
    from utils.config import settings
    url = make_url(settings.DATABASE_URL)
    assert url.database == "autotrade_test", (
        f"settings.DATABASE_URL resolves to {url.database!r}; the guard should "
        f"have replaced it with the allowlisted test database or aborted."
    )
    assert url.database != "autotrade_pro"


def test_the_shared_session_factory_is_bound_to_the_test_database():
    """db.database is imported by production code at module import time — this
    is the binding that actually matters."""
    from db.database import engine
    assert engine.url.database == "autotrade_test", (
        f"db.database.engine is bound to {engine.url.database!r}"
    )


def test_the_celery_session_factory_would_also_use_the_test_database():
    """tasks/_db.py::celery_session reads settings.DATABASE_URL on every call,
    so patching db.database alone would not have covered it."""
    from utils.config import settings
    assert make_url(settings.DATABASE_URL).database == "autotrade_test"


@pytest.mark.asyncio
async def test_a_real_write_lands_in_the_test_database_only():
    """Write one row through the production session factory and read it back.

    If the guard were absent this row would go to production — which is exactly
    what used to happen. The row is rolled back, so the test DB is left clean.
    """
    from db.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        db = (await session.execute(text("SELECT current_database()"))).scalar()
        assert db == "autotrade_test", f"connected to {db!r}"

        await session.execute(text(
            "INSERT INTO simulation_logs (event_type, symbol, message, timestamp) "
            "VALUES ('PHASE10_ISOLATION_PROBE', 'ISOLATIONTEST.NS', 'guard proof', now())"
        ))
        n = (await session.execute(text(
            "SELECT COUNT(*) FROM simulation_logs WHERE symbol='ISOLATIONTEST.NS'"))).scalar()
        assert n == 1
        await session.rollback()

    async with AsyncSessionLocal() as session:
        left = (await session.execute(text(
            "SELECT COUNT(*) FROM simulation_logs WHERE symbol='ISOLATIONTEST.NS'"))).scalar()
        assert left == 0, "probe row survived the rollback"
