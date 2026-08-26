"""Sector-breadth veto — refuse a long into a falling sector (2026-08-21).

THE INCIDENT
------------
The government allowed duty-free import of 10 lakh tonnes of raw sugar and the
complex fell 3-7%. At 09:14 IST the tactical pipeline BOUGHT DHAMPURSUG on a
GAP_AND_GO pattern. Measured at that minute: all 13 sugar peers down, and
DHAMPURSUG itself already -1.16%.

WHY THE OBVIOUS FIX FAILS
-------------------------
"Block when a bearish event names the symbol" does nothing here. The classifier
read the news as BULLISH — the 05:06 and 07:00 REGULATORY_CHANGE rows
(importance 78) and the 06:43 SECTOR_MOMENTUM row all carry the sugar tickers in
`bullish_stocks`, with `bearish_stocks` empty. The system did not miss the news;
it got the direction backwards. So this veto ignores what the news CLAIMS and
measures what the sector is DOING.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from engine.sector_breadth_veto import sector_breadth_veto


class TestOnlyVetoesLongs:

    @pytest.mark.asyncio
    async def test_shorts_are_never_vetoed(self):
        """A short into a falling sector is aligned with breadth, not against
        it — vetoing it would block the correct trade.

        Built so ONLY the side check can save it: the same inputs are proven to
        veto a BUY below. An earlier version passed session=None, which made the
        function error out and fail open — so it passed even with the side check
        deleted, and proved nothing.
        """
        class _Sess:
            async def execute(self, *a, **k):
                class _R:
                    @staticmethod
                    def fetchall():
                        return [("A.NS", -3.0), ("B.NS", -2.0), ("C.NS", -1.5), ("D.NS", -4.0)]
                return _R()

        peers = AsyncMock(return_value=("Sugar", ["A", "B", "C", "D"]))
        with patch("engine.sector_breadth_veto.sector_peers", peers):
            buy, why = await sector_breadth_veto("X.NS", "BUY", _Sess())
            sell, _ = await sector_breadth_veto("X.NS", "SELL", _Sess())

        assert buy is True and "down" in why, "fixture must veto the long"
        assert sell is False, "a short into a falling sector must be allowed"


class TestFailsOpen:
    """This is a filter on top of the existing gates, not one of them. Firing on
    thin data would block ordinary trades for no reason."""

    @pytest.mark.asyncio
    async def test_no_sector_means_no_opinion(self):
        with patch("engine.sector_breadth_veto.sector_peers",
                   AsyncMock(return_value=(None, []))):
            v, _ = await sector_breadth_veto("X.NS", "BUY", session=object())
        assert v is False

    @pytest.mark.asyncio
    async def test_too_few_peers_allows(self):
        with patch("engine.sector_breadth_veto.sector_peers",
                   AsyncMock(return_value=("Sugar", ["A"]))):
            v, _ = await sector_breadth_veto("X.NS", "BUY", session=object())
        assert v is False

    @pytest.mark.asyncio
    async def test_any_error_allows(self):
        with patch("engine.sector_breadth_veto.sector_peers",
                   AsyncMock(side_effect=RuntimeError("db down"))):
            v, _ = await sector_breadth_veto("X.NS", "BUY", session=object())
        assert v is False

    @pytest.mark.asyncio
    async def test_flag_disables_it(self):
        with patch("utils.config.settings.TACTICAL_SECTOR_BREADTH_VETO", False):
            v, _ = await sector_breadth_veto("X.NS", "BUY", session=None)
        assert v is False


class TestWiredIntoTactical:

    def test_executor_consults_the_veto(self):
        import inspect

        from engine import tactical_executor

        src = inspect.getsource(tactical_executor)
        assert "sector_breadth_veto(" in src

    def test_veto_runs_before_the_intent_is_built(self):
        """Must reject before execute_trade_intent, or it is not a veto."""
        import inspect

        from engine import tactical_executor

        src = inspect.getsource(tactical_executor)
        assert src.index("sector_breadth_veto(") < src.index("execute_trade_intent(")

    def test_threshold_can_still_fire_on_thin_membership(self):
        """Membership is harvested from events, which name a mix of tickers and
        company names; the name forms cannot be matched to candle symbols. The
        sugar day yielded only 4 usable peers, so a min_peers of 5 made the veto
        unable to fire on the incident it was built for."""
        from utils.config import settings

        assert settings.TACTICAL_SECTOR_VETO_MIN_PEERS <= 4
