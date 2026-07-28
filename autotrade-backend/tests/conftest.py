"""Project-wide pytest fixtures.

Autouse fixtures here apply to every test in tests/ without each file needing
its own copy — used for cross-cutting concerns that would otherwise make
tests flaky/time-of-day-dependent or accidentally hit live external services.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

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
