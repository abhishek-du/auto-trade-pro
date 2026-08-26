"""Project-wide pytest fixtures.

Autouse fixtures here apply to every test in tests/ without each file needing
its own copy — used for cross-cutting concerns that would otherwise make
tests flaky/time-of-day-dependent or accidentally hit live external services.

It also carries the FAIL-CLOSED DATABASE GUARD below, which runs at import
time — before pytest collects a single test.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════════
# FAIL-CLOSED TEST DATABASE GUARD  (2026-08-25)
# ═══════════════════════════════════════════════════════════════════════════
#
# WHY THIS EXISTS
# ---------------
# Running `pytest tests/` from this checkout could write to the PRODUCTION
# database, and did: `simulation_logs` holds 144 rows for a fixture symbol
# TESTCO.NS, written by tests/test_integration_pipeline.py.
#
# The chain was:
#   1. no test database, no DATABASE_URL override, no rollback fixture
#   2. db/database.py builds AsyncSessionLocal at IMPORT time from the
#      production settings.DATABASE_URL
#   3. the autouse market-hours fixture below patches is_nse_market_open True,
#      which removes the router gate that would otherwise refuse the write
#   4. the test patches `news_discovery_engine.AsyncSessionLocal`
#   5. engine/direct_news_strategy.py re-imports AsyncSessionLocal from
#      db.database at call time, so the patch never reaches it
#   6. _log_intent_audit() then add()s and commit()s to production
#
# Steps 4-5 are the proximate defect. Step 1 is the cause: nothing stood
# between the suite and production, so every future test would have to
# remember to patch the right name. This guard removes that requirement.
#
# WHY IT RUNS AT MODULE LEVEL AND NOT AS A FIXTURE
# ------------------------------------------------
# pytest imports conftest.py BEFORE collecting test modules. Test modules
# import db.database during collection, which binds AsyncSessionLocal to
# whatever settings.DATABASE_URL said at that moment. A session-scoped
# autouse fixture runs AFTER collection — too late. So this is module-level
# code, deliberately.
#
# WHY IT OVERRIDES settings.DATABASE_URL RATHER THAN AsyncSessionLocal
# --------------------------------------------------------------------
# There are two session factories: db/database.py:26 (module import time) and
# tasks/_db.py:38, which reads settings.DATABASE_URL fresh on every call.
# Patching only the first leaves the second pointed at production. The single
# value both derive from is settings.DATABASE_URL.
#
# WHAT IT DOES NOT DO
# -------------------
# It does not connect to any database, create one, guess a SQLite file, or
# fall back to DATABASE_URL. It decides from configuration alone, and when the
# configuration is missing or ambiguous it aborts the run.
#
# CONFIGURATION REQUIRED (none of these are in .env — that is intentional;
# this phase does not modify .env, so the suite fails closed until an operator
# sets them deliberately):
#
#   TEST_DATABASE_URL      full SQLAlchemy URL of a throwaway test database
#   ALLOWED_TEST_DB_HOSTS  comma-separated hosts that may host a test database
#   ALLOWED_TEST_DB_NAMES  comma-separated database names that are test DBs
#
# Supplied by the environment, or by a local gitignored .env.test beside
# pytest.ini. See .env.test.example. Production .env is never read for these.
#
# The allowlist is deliberate: a denylist of known production hosts silently
# permits every host nobody thought to add.

_ABORT_HEADLINE = "Tests aborted: no explicitly allowlisted test database is configured."


def _redact(url) -> str:
    """URL string with the password removed. Never log a raw URL."""
    try:
        return url.render_as_string(hide_password=True)
    except Exception:
        return "<unparseable url>"


def _abort(detail: str) -> None:
    pytest.exit(f"{_ABORT_HEADLINE}\n\n{detail}\n", returncode=4)


def _split_allowlist(raw: str | None) -> set[str]:
    return {x.strip().lower() for x in (raw or "").split(",") if x.strip()}


def _test_config() -> dict:
    """The three required settings, from the environment or from .env.test.

    The environment wins. .env.test is a LOCAL, gitignored file (`.env.*` is
    ignored at the repo root) so that `pytest tests/` works without every
    developer exporting three variables by hand — see .env.test.example.

    This is NOT a fallback to production: if neither source supplies all three,
    the guard still aborts. Production .env is never consulted here.
    """
    cfg = {k: os.environ.get(k) for k in
           ("TEST_DATABASE_URL", "ALLOWED_TEST_DB_HOSTS", "ALLOWED_TEST_DB_NAMES")}
    if all(cfg.values()):
        return cfg
    env_test = Path(__file__).resolve().parents[1] / ".env.test"
    if env_test.is_file():
        try:
            from dotenv import dotenv_values
            for k, v in (dotenv_values(env_test) or {}).items():
                if k in cfg and not cfg[k] and v:
                    cfg[k] = v
        except Exception:                            # pragma: no cover - defensive
            pass
    return cfg


def _enforce_test_database_isolation() -> None:
    from sqlalchemy.engine import make_url          # parsing only, no connection

    _cfg = _test_config()
    test_url_raw = _cfg["TEST_DATABASE_URL"]
    hosts = _split_allowlist(_cfg["ALLOWED_TEST_DB_HOSTS"])
    names = _split_allowlist(_cfg["ALLOWED_TEST_DB_NAMES"])

    missing = [n for n, v in (
        ("TEST_DATABASE_URL", test_url_raw),
        ("ALLOWED_TEST_DB_HOSTS", hosts or None),
        ("ALLOWED_TEST_DB_NAMES", names or None),
    ) if not v]
    if missing:
        _abort(
            f"Missing required test-database configuration: {', '.join(missing)}.\n"
            f"Set all three, pointing at a throwaway database. The suite will not "
            f"fall back to DATABASE_URL, because doing so is how 144 rows were "
            f"written to the production simulation_logs table."
        )

    try:
        test_url = make_url(test_url_raw)
    except Exception as exc:
        _abort(f"TEST_DATABASE_URL could not be parsed: {type(exc).__name__}.")
        return

    host = (test_url.host or "").lower()
    dbname = (test_url.database or "").lower()
    if not host or not dbname:
        _abort(
            f"TEST_DATABASE_URL is missing a host or database name "
            f"({_redact(test_url)}) — identity is ambiguous, so it is refused."
        )
    if host not in hosts:
        _abort(f"Host {host!r} is not in ALLOWED_TEST_DB_HOSTS ({sorted(hosts)}).")
    if dbname not in names:
        _abort(f"Database {dbname!r} is not in ALLOWED_TEST_DB_NAMES ({sorted(names)}).")

    # The configured test database must not BE the production one. Compared on
    # host+port+database, not on the raw string: the same database reached with
    # a different password or driver is still the same database.
    from utils.config import settings                # pydantic only; opens nothing
    try:
        prod_url = make_url(settings.DATABASE_URL)
    except Exception:
        _abort("DATABASE_URL could not be parsed, so it cannot be compared against the test URL.")
        return
    same = (
        (prod_url.host or "").lower() == host
        and (prod_url.port or 5432) == (test_url.port or 5432)
        and (prod_url.database or "").lower() == dbname
    )
    if same:
        _abort(
            f"TEST_DATABASE_URL resolves to the same database as DATABASE_URL "
            f"({_redact(test_url)}). Refusing to run the suite against production."
        )

    # Single point of truth: both session factories derive from this value.
    settings.DATABASE_URL = test_url_raw

    # If db.database was already imported (a plugin, or an earlier conftest),
    # its engine is already bound to the old URL — rebind it too.
    mod = sys.modules.get("db.database")
    if mod is not None:                              # pragma: no cover - defensive
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool
        mod.engine = create_async_engine(
            test_url, poolclass=NullPool, connect_args={"statement_cache_size": 0}
        )
        mod.AsyncSessionLocal = async_sessionmaker(
            bind=mod.engine, class_=AsyncSession, expire_on_commit=False,
            autocommit=False, autoflush=False,
        )


_enforce_test_database_isolation()


@pytest.fixture(autouse=True)
def _market_always_open():
    """engine.decision_router.authorize_trade_intent() gates every new
    TradeIntent on real NSE market hours (added 2026-07-27, after
    SHAKTIPUMP.BO opened live at 15:51 IST — 21 minutes past the real 15:30
    close — because a caller used an extended market-hours definition meant
    for a different purpose: position-management grace period, not "may a
    new trade open now"). Tests that build a TradeIntent and expect it to
    reach EXECUTED_PAPER/EXECUTED_LIVE/other gate outcomes would otherwise
    pass or fail purely based on what time of day the suite happens to run —
    patch the check open by default so tests are deterministic. A test that
    specifically wants to verify the market-closed block itself can still
    nest its own `patch("crawler.india_price_feed.is_nse_market_open", ...)`
    inside the test body — the inner patch wins for the duration of its own
    `with`/decorator scope and reverts to this fixture's True on exit.
    """
    with patch("crawler.india_price_feed.is_nse_market_open", return_value=True):
        yield


@pytest.fixture(autouse=True)
def _entry_confirmation_passes_by_default():
    """engine.agent.decision_engine._apply_confirmation_veto() and
    engine.direct_news_strategy.maybe_direct_trade() both gate a TAKE/entry on
    engine.entry_confirmation.check_price_volume_confirmation() (added
    2026-07-28, after live data showed every stopped-out trade that week
    shared near-zero MFE — no real price/volume follow-through at entry).
    That check needs a live MarketSnapshot with real change_pct/depth data,
    which tests don't have — patch it to pass by default so pre-existing
    TAKE-path tests stay deterministic and unrelated to this gate. A test that
    specifically wants to verify the confirmation veto itself can still nest
    its own patch of this same target inside the test body.
    """
    # Also short-circuit the underlying snapshot fetch -- otherwise the real
    # get_market_snapshot() (ws/rest/yfinance fallback chain) still runs and
    # burns several seconds per call attempting live network calls that will
    # never succeed in a test sandbox, even though its result is discarded by
    # the check-function patch above.
    with patch(
        "engine.entry_confirmation.check_price_volume_confirmation",
        return_value=(True, "test default: confirmed"),
    ), patch(
        "crawler.market_snapshot.get_market_snapshot",
        AsyncMock(return_value=None),
    ):
        yield
