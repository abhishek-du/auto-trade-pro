"""Project-wide pytest fixtures.

Autouse fixtures here apply to every test in tests/ without each file needing
its own copy — used for cross-cutting concerns that would otherwise make
tests flaky/time-of-day-dependent or accidentally hit live external services.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


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
