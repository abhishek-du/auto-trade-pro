"""Regression tests for news_discovery_engine.py::_check_reentry_watches()
and _evidence_from_event_id() -- the breakout-detection half of the
T1-reanalysis/re-entry feature (2026-07-22).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from news_discovery_engine import _check_reentry_watches, _evidence_from_event_id


def _watch(id=1, symbol="TESTCO.NS", direction="BUY", watch_level=105.0,
           status="WATCHING", event_id=2848, evidence_ids=None, expires_delta_hours=5):
    return SimpleNamespace(
        id=id, symbol=symbol, direction=direction, watch_level=watch_level,
        status=status, event_id=event_id, evidence_ids=evidence_ids or [str(event_id)],
        reason="reversal risk", expires_at=datetime.utcnow() + timedelta(hours=expires_delta_hours),
    )


def _mock_session_ctx(session=None):
    session = session or AsyncMock()
    session.add = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx), session


class TestEvidenceFromEventId:
    @pytest.mark.asyncio
    async def test_resolves_real_event(self):
        canonical = SimpleNamespace(id=2848, event_title="EARNINGS", country="HIGH", confidence=0.85)
        session = AsyncMock()
        session.get = AsyncMock(return_value=canonical)
        evidence = await _evidence_from_event_id(2848, "BUY", session)
        assert evidence is not None
        assert evidence.direction == "BULLISH"
        assert evidence.materiality == "HIGH"

    @pytest.mark.asyncio
    async def test_sell_side_yields_bearish_direction(self):
        canonical = SimpleNamespace(id=2848, event_title="EARNINGS", country="HIGH", confidence=0.85)
        session = AsyncMock()
        session.get = AsyncMock(return_value=canonical)
        evidence = await _evidence_from_event_id(2848, "SELL", session)
        assert evidence.direction == "BEARISH"

    @pytest.mark.asyncio
    async def test_missing_event_returns_none(self):
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)
        evidence = await _evidence_from_event_id(999999, "BUY", session)
        assert evidence is None


class TestBreakoutDetection:
    @pytest.mark.asyncio
    async def test_buy_watch_triggers_above_level(self):
        watch = _watch(direction="BUY", watch_level=105.0)
        find_ctx, watch_session = _mock_session_ctx()
        watch_session.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [watch])))

        refetch_session = AsyncMock()
        refetch_session.get = AsyncMock(return_value=watch)
        refetch_ctx = MagicMock()
        refetch_ctx.__aenter__ = AsyncMock(return_value=refetch_session)
        refetch_ctx.__aexit__ = AsyncMock(return_value=False)

        evidence_session = AsyncMock()
        evidence_ctx = MagicMock()
        evidence_ctx.__aenter__ = AsyncMock(return_value=evidence_session)
        evidence_ctx.__aexit__ = AsyncMock(return_value=False)

        session_ctx_factory = MagicMock(side_effect=[find_ctx.return_value, refetch_ctx, evidence_ctx])

        with patch("news_discovery_engine.AsyncSessionLocal", session_ctx_factory), \
             patch("crawler.market_snapshot.get_market_snapshot",
                   AsyncMock(return_value=SimpleNamespace(ltp=110.0, source="zerodha_rest", fetched_at_ist="t"))), \
             patch("news_discovery_engine._evidence_from_event_id", AsyncMock(return_value=SimpleNamespace())), \
             patch("news_discovery_engine.llm_tooluse_candidate", AsyncMock(return_value=None)):
            await _check_reentry_watches()

        assert watch.status == "TRIGGERED"

    @pytest.mark.asyncio
    async def test_buy_watch_does_not_trigger_below_level(self):
        watch = _watch(direction="BUY", watch_level=105.0)
        find_ctx, watch_session = _mock_session_ctx()
        watch_session.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [watch])))

        with patch("news_discovery_engine.AsyncSessionLocal", find_ctx), \
             patch("crawler.market_snapshot.get_market_snapshot",
                   AsyncMock(return_value=SimpleNamespace(ltp=100.0, source="zerodha_rest", fetched_at_ist="t"))):
            await _check_reentry_watches()

        assert watch.status == "WATCHING"

    @pytest.mark.asyncio
    async def test_sell_watch_triggers_below_level(self):
        watch = _watch(direction="SELL", watch_level=95.0)
        find_ctx, watch_session = _mock_session_ctx()
        watch_session.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [watch])))

        refetch_session = AsyncMock()
        refetch_session.get = AsyncMock(return_value=watch)
        refetch_ctx = MagicMock()
        refetch_ctx.__aenter__ = AsyncMock(return_value=refetch_session)
        refetch_ctx.__aexit__ = AsyncMock(return_value=False)

        evidence_session = AsyncMock()
        evidence_ctx = MagicMock()
        evidence_ctx.__aenter__ = AsyncMock(return_value=evidence_session)
        evidence_ctx.__aexit__ = AsyncMock(return_value=False)

        session_ctx_factory = MagicMock(side_effect=[find_ctx.return_value, refetch_ctx, evidence_ctx])

        with patch("news_discovery_engine.AsyncSessionLocal", session_ctx_factory), \
             patch("crawler.market_snapshot.get_market_snapshot",
                   AsyncMock(return_value=SimpleNamespace(ltp=90.0, source="zerodha_rest", fetched_at_ist="t"))), \
             patch("news_discovery_engine._evidence_from_event_id", AsyncMock(return_value=SimpleNamespace())), \
             patch("news_discovery_engine.llm_tooluse_candidate", AsyncMock(return_value=None)):
            await _check_reentry_watches()

        assert watch.status == "TRIGGERED"

    @pytest.mark.asyncio
    async def test_sell_watch_does_not_trigger_above_level(self):
        watch = _watch(direction="SELL", watch_level=95.0)
        find_ctx, watch_session = _mock_session_ctx()
        watch_session.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [watch])))

        with patch("news_discovery_engine.AsyncSessionLocal", find_ctx), \
             patch("crawler.market_snapshot.get_market_snapshot",
                   AsyncMock(return_value=SimpleNamespace(ltp=100.0, source="zerodha_rest", fetched_at_ist="t"))):
            await _check_reentry_watches()

        assert watch.status == "WATCHING"


class TestExpiry:
    @pytest.mark.asyncio
    async def test_expired_watch_marked_expired_without_price_check(self):
        watch = _watch(expires_delta_hours=-1)  # already past expiry
        find_ctx, watch_session = _mock_session_ctx()
        watch_session.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [watch])))

        with patch("news_discovery_engine.AsyncSessionLocal", find_ctx), \
             patch("crawler.market_snapshot.get_market_snapshot", AsyncMock()) as mock_snap:
            await _check_reentry_watches()

        assert watch.status == "EXPIRED"
        mock_snap.assert_not_called()


class TestReentryTriggersFreshFullAnalysis:
    @pytest.mark.asyncio
    async def test_take_verdict_executes_new_trade(self):
        watch = _watch(direction="BUY", watch_level=105.0, event_id=2848)
        find_ctx, watch_session = _mock_session_ctx()
        watch_session.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [watch])))

        refetch_session = AsyncMock()
        refetch_session.get = AsyncMock(return_value=watch)
        refetch_ctx = MagicMock()
        refetch_ctx.__aenter__ = AsyncMock(return_value=refetch_session)
        refetch_ctx.__aexit__ = AsyncMock(return_value=False)

        evidence_session = AsyncMock()
        evidence_ctx = MagicMock()
        evidence_ctx.__aenter__ = AsyncMock(return_value=evidence_session)
        evidence_ctx.__aexit__ = AsyncMock(return_value=False)

        session_ctx_factory = MagicMock(side_effect=[find_ctx.return_value, refetch_ctx, evidence_ctx])

        take_verdict = {"verdict": "TAKE", "confidence": 82, "bull": "fresh breakout confirmed"}

        with patch("news_discovery_engine.AsyncSessionLocal", session_ctx_factory), \
             patch("crawler.market_snapshot.get_market_snapshot",
                   AsyncMock(return_value=SimpleNamespace(ltp=110.0, source="zerodha_rest", fetched_at_ist="t"))), \
             patch("news_discovery_engine._evidence_from_event_id",
                   AsyncMock(return_value=SimpleNamespace(materiality="HIGH", event_category="EARNINGS"))), \
             patch("news_discovery_engine.llm_tooluse_candidate", AsyncMock(return_value=take_verdict)), \
             patch("engine.event_classifier.validate_evidence_consistency",
                   MagicMock(return_value=SimpleNamespace(consistent=True))), \
             patch("news_discovery_engine._execute_news_trade", AsyncMock(return_value=True)) as mock_execute:
            await _check_reentry_watches()

        mock_execute.assert_called_once()
        call_kwargs = mock_execute.call_args
        assert call_kwargs[0][0] == "TESTCO.NS"
        assert call_kwargs[0][1] == "BUY"
        assert call_kwargs[1]["event_id"] == 2848

    @pytest.mark.asyncio
    async def test_skip_verdict_does_not_execute_trade(self):
        watch = _watch(direction="BUY", watch_level=105.0, event_id=2848)
        find_ctx, watch_session = _mock_session_ctx()
        watch_session.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [watch])))

        refetch_session = AsyncMock()
        refetch_session.get = AsyncMock(return_value=watch)
        refetch_ctx = MagicMock()
        refetch_ctx.__aenter__ = AsyncMock(return_value=refetch_session)
        refetch_ctx.__aexit__ = AsyncMock(return_value=False)

        evidence_session = AsyncMock()
        evidence_ctx = MagicMock()
        evidence_ctx.__aenter__ = AsyncMock(return_value=evidence_session)
        evidence_ctx.__aexit__ = AsyncMock(return_value=False)

        session_ctx_factory = MagicMock(side_effect=[find_ctx.return_value, refetch_ctx, evidence_ctx])

        with patch("news_discovery_engine.AsyncSessionLocal", session_ctx_factory), \
             patch("crawler.market_snapshot.get_market_snapshot",
                   AsyncMock(return_value=SimpleNamespace(ltp=110.0, source="zerodha_rest", fetched_at_ist="t"))), \
             patch("news_discovery_engine._evidence_from_event_id",
                   AsyncMock(return_value=SimpleNamespace(materiality="HIGH", event_category="EARNINGS"))), \
             patch("news_discovery_engine.llm_tooluse_candidate", AsyncMock(return_value={"verdict": "SKIP"})), \
             patch("news_discovery_engine._execute_news_trade", AsyncMock()) as mock_execute:
            await _check_reentry_watches()

        mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_unresolvable_event_skips_without_error(self):
        watch = _watch(direction="BUY", watch_level=105.0, event_id=999999)
        find_ctx, watch_session = _mock_session_ctx()
        watch_session.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [watch])))

        refetch_session = AsyncMock()
        refetch_session.get = AsyncMock(return_value=watch)
        refetch_ctx = MagicMock()
        refetch_ctx.__aenter__ = AsyncMock(return_value=refetch_session)
        refetch_ctx.__aexit__ = AsyncMock(return_value=False)

        evidence_session = AsyncMock()
        evidence_ctx = MagicMock()
        evidence_ctx.__aenter__ = AsyncMock(return_value=evidence_session)
        evidence_ctx.__aexit__ = AsyncMock(return_value=False)

        session_ctx_factory = MagicMock(side_effect=[find_ctx.return_value, refetch_ctx, evidence_ctx])

        with patch("news_discovery_engine.AsyncSessionLocal", session_ctx_factory), \
             patch("crawler.market_snapshot.get_market_snapshot",
                   AsyncMock(return_value=SimpleNamespace(ltp=110.0, source="zerodha_rest", fetched_at_ist="t"))), \
             patch("news_discovery_engine._evidence_from_event_id", AsyncMock(return_value=None)), \
             patch("news_discovery_engine._execute_news_trade", AsyncMock()) as mock_execute:
            await _check_reentry_watches()

        mock_execute.assert_not_called()
