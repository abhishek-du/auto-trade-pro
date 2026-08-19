"""Regression tests for engine/decision_router.py — the central execution
gate every TradeIntent must pass through.

This subsystem had ZERO test coverage before this file (confirmed via a
2026-07-21 coverage audit): no test instantiated TradeIntent, called
authorize_trade_intent(), or exercised _verify_canonical_event(). That's a
real gap given this is the single choke point enforcing:
  - the News-Only hard-block on TECHNICAL trade origination (the exact
    thing commit "fix(security): remove central-gate bypass in
    run_master_intelligence_cycle()" fixed a regression in once already),
  - "NO EVENT -> NO TRADE",
  - confidence-provenance / event-directness tiering,
  - WATCHLIST_ONLY routing for speculative/incomplete second-order candidates.

These tests mock the AsyncSession and downstream I/O (RuntimeConfig,
VirtualWallet, risk_manager.validate_signal) so they run in-process with
no real DB/network — the point is to lock in gate BEHAVIOR, not integration
plumbing.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.decision_router import (
    ConfidenceSource,
    EventDirectness,
    RoutingOutcome,
    StrategyFamily,
    TradeIntent,
    TradeMode,
    _intent_to_signal,
    authorize_trade_intent,
)


# ── Shared builders ───────────────────────────────────────────────────────────

def make_canonical_event(id=1, materiality="HIGH", bullish=None, bearish=None, confidence=0.8):
    """A stand-in for a db.models.CausalEvent row — _verify_canonical_event()
    only ever reads .id/.country/.bullish_stocks/.bearish_stocks/.confidence/
    .event_title off it, so a SimpleNamespace is sufficient and avoids a real DB."""
    return SimpleNamespace(
        id=id,
        country=materiality,   # CausalEvent.country stores materiality (event_pipeline.py convention)
        bullish_stocks=bullish or [],
        bearish_stocks=bearish or [],
        event_title="Q1 Results",
        confidence=confidence,
    )


def make_intent(**overrides) -> TradeIntent:
    defaults = dict(
        strategy="NEWS_DIRECT",
        symbol="TESTCO.NS",
        action="BUY",
        instrument_type="EQUITY",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        confidence=80.0,
        confidence_source=ConfidenceSource.CALCULATED,
        strategy_family=StrategyFamily.EVENT_DRIVEN,
        event_directness=EventDirectness.DIRECT,
        evidence_ids=["1"],
        event_id=1,
    )
    defaults.update(overrides)
    return TradeIntent(**defaults)


def make_session(canonical_event=None) -> AsyncMock:
    """AsyncSession stand-in. session.get() resolves the canonical CausalEvent;
    session.add()/commit() are no-ops (the gate's audit logging happens
    against this same mock and must not raise). session.execute() is wired
    for the EQUITY leg's `(await session.execute(...)).scalars().all()`
    open-positions query -- returning a plain MagicMock (not AsyncMock) so
    .scalars()/.all() are ordinary sync calls, not further coroutines."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=canonical_event)
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=exec_result)
    session.add = MagicMock()   # real AsyncSession.add() is sync, not a coroutine
    return session


def _patch_resolve_mode(mode=TradeMode.PAPER):
    return patch("engine.decision_router.resolve_mode", AsyncMock(return_value=mode))


def _patch_equity_approval():
    """Patch the EQUITY risk-validation leg (VirtualWallet + validate_signal)
    so a fully-valid intent reaches AuthorizationResult(approved=True)."""
    wallet_patch = patch(
        "paper_trading.virtual_wallet.VirtualWallet.get_summary",
        AsyncMock(return_value={"balance": 1_000_000.0}),
    )
    risk_patch = patch(
        "engine.risk_manager.validate_signal",
        AsyncMock(return_value=(True, "ok")),
    )
    return wallet_patch, risk_patch


# ── 1. TECHNICAL hard-block (News-Only architecture invariant) ───────────────
# This is the single most important regression guard in this file: a prior
# incident (fixed in "fix(security): remove central-gate bypass in
# run_master_intelligence_cycle()") shows this exact check has been bypassed
# in the wild before. If a future change reorders the gate's checks, adds a
# new call site, or someone flips a per-caller settings flag instead of
# routing through this function, these tests catch it.

class TestTechnicalHardBlock:
    @pytest.mark.asyncio
    async def test_technical_strategy_always_blocked(self):
        intent = make_intent(strategy_family=StrategyFamily.TECHNICAL, event_id=None, evidence_ids=[])
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, make_session())
        assert result.approved is False
        assert result.outcome == RoutingOutcome.BLOCKED_TECHNICAL_ORIGIN

    @pytest.mark.asyncio
    async def test_technical_blocked_even_with_high_confidence(self):
        # A high, genuinely-CALCULATED confidence must NOT buy TECHNICAL a
        # way around the hard-block -- confidence tiering is a separate gate.
        intent = make_intent(
            strategy_family=StrategyFamily.TECHNICAL, confidence=99.0,
            confidence_source=ConfidenceSource.CALCULATED, event_id=None, evidence_ids=[],
        )
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, make_session())
        assert result.approved is False
        assert result.outcome == RoutingOutcome.BLOCKED_TECHNICAL_ORIGIN

    @pytest.mark.asyncio
    async def test_technical_block_runs_before_event_check(self):
        # Structural guarantee: TECHNICAL is rejected for BEING technical, not
        # merely because it lacks an event -- proven by giving it a fully
        # valid canonical event and confirming the outcome is still
        # BLOCKED_TECHNICAL_ORIGIN, not an event-check outcome.
        canonical = make_canonical_event()
        intent = make_intent(strategy_family=StrategyFamily.TECHNICAL, event_id=1, evidence_ids=["1"])
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, make_session(canonical))
        assert result.outcome == RoutingOutcome.BLOCKED_TECHNICAL_ORIGIN

# ── 2. NO EVENT -> NO TRADE ────────────────────────────────────────────────────

class TestNoEventNoTrade:
    @pytest.mark.asyncio
    async def test_event_driven_without_event_id_blocked(self):
        intent = make_intent(event_id=None)
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, make_session())
        assert result.approved is False
        assert result.outcome == RoutingOutcome.BLOCKED_NO_EVENT

    @pytest.mark.asyncio
    async def test_event_driven_without_evidence_ids_blocked(self):
        intent = make_intent(event_id=1, evidence_ids=[])
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, make_session())
        assert result.approved is False
        assert result.outcome == RoutingOutcome.BLOCKED_NO_EVENT

    @pytest.mark.asyncio
    async def test_dangling_event_id_blocked(self):
        # event_id set, but no such CausalEvent row exists (session.get -> None).
        intent = make_intent(event_id=999)
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, make_session(canonical_event=None))
        assert result.approved is False
        assert result.outcome == RoutingOutcome.BLOCKED_NO_EVENT

# ── 3. Materiality floor + evidence drift ─────────────────────────────────────

class TestMaterialityAndEvidenceDrift:
    @pytest.mark.asyncio
    async def test_low_materiality_direct_blocked(self):
        canonical = make_canonical_event(materiality="LOW")
        intent = make_intent(event_directness=EventDirectness.DIRECT)
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, make_session(canonical))
        assert result.approved is False
        assert result.outcome == RoutingOutcome.BLOCKED_NO_EVENT
        assert "materiality" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_none_materiality_direct_blocked(self):
        canonical = make_canonical_event(materiality="NONE")
        intent = make_intent(event_directness=EventDirectness.DIRECT)
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, make_session(canonical))
        assert result.approved is False
        assert result.outcome == RoutingOutcome.BLOCKED_NO_EVENT

    @pytest.mark.asyncio
    async def test_snapshot_materiality_drift_blocked(self):
        # Canonical row says HIGH; caller's own evidence snapshot claims MEDIUM.
        # The canonical DB row must win -- a caller cannot self-report a
        # different materiality than what the classifier actually persisted.
        from engine.event_classifier import DecisionEvidence
        canonical = make_canonical_event(materiality="HIGH")
        evidence = DecisionEvidence(
            source_type="NSE_ANNOUNCEMENT", source_id="1", title="t", summary="s",
            event_category="EARNINGS", materiality="MEDIUM", direction="BULLISH", confidence=0.7,
        )
        intent = make_intent(evidence=evidence)
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, make_session(canonical))
        assert result.approved is False
        assert result.outcome == RoutingOutcome.BLOCKED_EVIDENCE_DRIFT

    @pytest.mark.asyncio
    async def test_direction_not_confirmed_by_canonical_blocked(self):
        # Canonical event lists OTHER symbols as bullish, not this one --
        # snapshot claiming BULLISH for THIS symbol must be rejected, not
        # merely allowed through because it isn't in the bearish list either.
        from engine.event_classifier import DecisionEvidence
        canonical = make_canonical_event(materiality="HIGH", bullish=["OTHERCO"], bearish=[])
        evidence = DecisionEvidence(
            source_type="NSE_ANNOUNCEMENT", source_id="1", title="t", summary="s",
            event_category="EARNINGS", materiality="HIGH", direction="BULLISH", confidence=0.8,
        )
        intent = make_intent(symbol="TESTCO.NS", evidence=evidence)
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, make_session(canonical))
        assert result.approved is False
        assert result.outcome == RoutingOutcome.BLOCKED_EVIDENCE_DRIFT
        assert "bullish" in result.reason.lower() or "drift" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_direction_confirmed_by_canonical_passes_event_check(self):
        from engine.event_classifier import DecisionEvidence
        canonical = make_canonical_event(materiality="HIGH", bullish=["TESTCO"], bearish=[])
        evidence = DecisionEvidence(
            source_type="NSE_ANNOUNCEMENT", source_id="1", title="t", summary="s",
            event_category="EARNINGS", materiality="HIGH", direction="BULLISH", confidence=0.8,
        )
        intent = make_intent(symbol="TESTCO.NS", evidence=evidence)
        wallet_patch, risk_patch = _patch_equity_approval()
        with _patch_resolve_mode(), wallet_patch, risk_patch:
            result = await authorize_trade_intent(intent, make_session(canonical))
        # Should clear the event check entirely (and, with equity approval
        # mocked true, the whole gate) -- not rejected for drift/no-event.
        assert result.outcome not in (RoutingOutcome.BLOCKED_EVIDENCE_DRIFT, RoutingOutcome.BLOCKED_NO_EVENT)
        assert result.approved is True


# ── 4. WATCHLIST_ONLY routing ─────────────────────────────────────────────────

class TestWatchlistOnly:
    @pytest.mark.asyncio
    async def test_speculative_is_watchlist_only(self):
        canonical = make_canonical_event(materiality="HIGH")
        intent = make_intent(event_directness=EventDirectness.SPECULATIVE)
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, make_session(canonical))
        assert result.approved is False
        assert result.outcome == RoutingOutcome.WATCHLIST_ONLY

    @pytest.mark.asyncio
    async def test_second_order_missing_scoring_factors_is_watchlist_only(self):
        canonical = make_canonical_event(materiality="HIGH")
        intent = make_intent(event_directness=EventDirectness.SECOND_ORDER, extra={})
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, make_session(canonical))
        assert result.approved is False
        assert result.outcome == RoutingOutcome.WATCHLIST_ONLY

    @pytest.mark.asyncio
    async def test_second_order_partial_scoring_factors_still_watchlist_only(self):
        # Only SOME of the four required factors present -- must still block,
        # no partial credit / no default substitution for the missing ones.
        canonical = make_canonical_event(materiality="HIGH")
        intent = make_intent(
            event_directness=EventDirectness.SECOND_ORDER,
            extra={"relationship_type": "supplier", "relationship_strength": 0.8},
        )
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, make_session(canonical))
        assert result.outcome == RoutingOutcome.WATCHLIST_ONLY

    @pytest.mark.asyncio
    async def test_second_order_complete_factors_but_low_confidence_blocked(self):
        canonical = make_canonical_event(materiality="HIGH")
        intent = make_intent(
            event_directness=EventDirectness.SECOND_ORDER,
            confidence=50.0,  # below SECOND_ORDER_MIN_CONFIDENCE default (70.0)
            extra={
                "relationship_type": "supplier", "relationship_strength": 0.8,
                "company_exposure": 0.5, "market_confirmation": "POSITIVE",
            },
        )
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, make_session(canonical))
        assert result.approved is False
        assert result.outcome == RoutingOutcome.BLOCKED_SECOND_ORDER

    @pytest.mark.asyncio
    async def test_second_order_complete_factors_and_sufficient_confidence_passes_tier_check(self):
        canonical = make_canonical_event(materiality="HIGH")
        intent = make_intent(
            event_directness=EventDirectness.SECOND_ORDER,
            confidence=85.0,
            extra={
                "relationship_type": "supplier", "relationship_strength": 0.8,
                "company_exposure": 0.5, "market_confirmation": "POSITIVE",
            },
        )
        wallet_patch, risk_patch = _patch_equity_approval()
        with _patch_resolve_mode(), wallet_patch, risk_patch:
            result = await authorize_trade_intent(intent, make_session(canonical))
        assert result.outcome not in (RoutingOutcome.WATCHLIST_ONLY, RoutingOutcome.BLOCKED_SECOND_ORDER)
        assert result.approved is True


# ── 5. Confidence provenance ───────────────────────────────────────────────────

class TestConfidenceProvenance:
    @pytest.mark.asyncio
    async def test_hardcoded_confidence_blocked_regardless_of_value(self):
        canonical = make_canonical_event(materiality="HIGH")
        intent = make_intent(confidence=99.0, confidence_source=ConfidenceSource.HARDCODED)
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, make_session(canonical))
        assert result.approved is False
        assert result.outcome == RoutingOutcome.BLOCKED_CONFIDENCE_INTEGRITY

    @pytest.mark.asyncio
    async def test_override_confidence_blocked(self):
        canonical = make_canonical_event(materiality="HIGH")
        intent = make_intent(confidence_source=ConfidenceSource.OVERRIDE)
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, make_session(canonical))
        assert result.approved is False
        assert result.outcome == RoutingOutcome.BLOCKED_CONFIDENCE_INTEGRITY

    @pytest.mark.asyncio
    async def test_calculated_confidence_clears_provenance_check(self):
        canonical = make_canonical_event(materiality="HIGH")
        intent = make_intent(confidence_source=ConfidenceSource.CALCULATED)
        wallet_patch, risk_patch = _patch_equity_approval()
        with _patch_resolve_mode(), wallet_patch, risk_patch:
            result = await authorize_trade_intent(intent, make_session(canonical))
        assert result.outcome != RoutingOutcome.BLOCKED_CONFIDENCE_INTEGRITY


# ── 6. Equity risk gate + full happy path ─────────────────────────────────────

class TestEquityRiskGateAndApproval:
    @pytest.mark.asyncio
    async def test_equity_risk_validation_failure_blocks(self):
        canonical = make_canonical_event(materiality="HIGH")
        intent = make_intent()
        wallet_patch = patch(
            "paper_trading.virtual_wallet.VirtualWallet.get_summary",
            AsyncMock(return_value={"balance": 1_000_000.0}),
        )
        risk_patch = patch(
            "engine.risk_manager.validate_signal",
            AsyncMock(return_value=(False, "sector cap exceeded")),
        )
        with _patch_resolve_mode(), wallet_patch, risk_patch:
            result = await authorize_trade_intent(intent, make_session(canonical))
        assert result.approved is False
        assert result.outcome == RoutingOutcome.BLOCKED_GATE
        assert "sector cap" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_fully_valid_event_driven_intent_is_approved(self):
        """Positive control: a well-formed EVENT_DRIVEN/DIRECT/CALCULATED
        intent with a matching canonical event and passing risk validation
        must clear the entire gate. Without this, an over-eager future fix
        to any single check above could silently block 100% of legitimate
        trades and no test here would notice."""
        canonical = make_canonical_event(materiality="HIGH", bullish=["TESTCO"], bearish=[])
        intent = make_intent()
        wallet_patch, risk_patch = _patch_equity_approval()
        with _patch_resolve_mode(), wallet_patch, risk_patch:
            result = await authorize_trade_intent(intent, make_session(canonical))
        assert result.approved is True
        assert result.reason == "approved"
        assert result.signal is not None


class TestIntentToSignalTraceability:
    """Regression guard for the 2026-07-22 bug: TradeIntent computed
    target_2/atr (via _compute_news_trade_levels) but never threaded them
    through to TradingSignal, so open_paper_trade()'s `target_2 = signal.
    target_2 or target_1` fallback silently collapsed every News-Only
    trade's final target to target_1 -- the position's hard take-profit
    coincided with T1, so "ride the second half to T2" never actually
    happened. Also covers event_id/evidence_ids, needed for the T1-
    reanalysis re-entry feature to trace a re-entry back to its origin event."""

    def test_target_2_and_atr_carried_through(self):
        intent = make_intent(target_2=130.0, atr=2.5)
        signal = _intent_to_signal(intent)
        assert signal.target_2 == 130.0
        assert signal.atr == 2.5

    def test_default_target_2_and_atr_are_zero_not_missing(self):
        intent = make_intent()
        signal = _intent_to_signal(intent)
        assert signal.target_2 == 0.0
        assert signal.atr == 0.0

    def test_event_id_and_evidence_ids_carried_through(self):
        intent = make_intent(event_id=2848, evidence_ids=["2848"])
        signal = _intent_to_signal(intent)
        assert signal.event_id == 2848
        assert signal.evidence_ids == ["2848"]

    def test_none_event_id_stays_none(self):
        intent = make_intent(event_id=None, evidence_ids=[])
        signal = _intent_to_signal(intent)
        assert signal.event_id is None
        assert signal.evidence_ids == []

    def test_confidence_factors_carried_through(self):
        # 2026-07-22, second incident: a SECOND_ORDER cascade's confidence was
        # found hardcoded to a fake 80% with zero record of why -- this field
        # is what lets the UI show the real breakdown instead of a bare number.
        factors = {"kind": "second_order_formula", "confidence": 42.0, "relationship_strength": 0.7}
        intent = make_intent(confidence_factors=factors)
        signal = _intent_to_signal(intent)
        assert signal.confidence_factors == factors
        # must be a copy, not the same dict instance, so a caller mutating one
        # side later can't silently corrupt the other
        assert signal.confidence_factors is not factors

    def test_default_confidence_factors_is_empty_not_missing(self):
        intent = make_intent()
        signal = _intent_to_signal(intent)
        assert signal.confidence_factors == {}


# ── Market-hours gate (2026-07-27) ───────────────────────────────────────────
# SHAKTIPUMP.BO opened live at 15:51 IST, 21 minutes after NSE's real 15:30
# close, because news_discovery_engine.py's market_open flag came from a
# DIFFERENT function (tasks.india_tasks._is_india_trading_window(), which
# deliberately extends to 16:00 IST for position-management purposes) than
# the strict real-hours check this gate now enforces. This is the single
# most important regression guard for that incident: every TradeIntent, from
# every strategy family, must funnel through this check regardless of what
# any individual caller's own (possibly wrong) market-hours logic decided.
# tests/conftest.py's `_market_always_open` autouse fixture patches
# is_nse_market_open() to True for every OTHER test in the suite (so they
# aren't flaky by time-of-day) — these tests explicitly override it back to
# verify the gate itself.

class TestMarketHoursGate:
    @pytest.mark.asyncio
    async def test_blocks_new_equity_position_when_market_closed(self):
        intent = make_intent(strategy_family=StrategyFamily.EVENT_DRIVEN)
        canonical = make_canonical_event(bullish=["TESTCO"])
        with _patch_resolve_mode(), \
             patch("crawler.india_price_feed.is_nse_market_open", return_value=False):
            result = await authorize_trade_intent(intent, make_session(canonical))
        assert result.approved is False
        assert result.outcome == RoutingOutcome.BLOCKED_MARKET_CLOSED

    @pytest.mark.asyncio
    async def test_applies_regardless_of_strategy_family(self):
        # PRE_EVENT and DIRECT_NEWS are both exempt from the EVENT_DRIVEN-
        # specific canonical-event consistency check, but neither is exempt
        # from this one -- the whole point is no strategy can bypass it. Both
        # families' own 3-flag gates default True already, so no extra
        # patching is needed for those; the market-hours check runs first
        # regardless and short-circuits before either gate is even reached.
        for family in (StrategyFamily.PRE_EVENT, StrategyFamily.DIRECT_NEWS):
            intent = make_intent(strategy_family=family, event_id=None, evidence_ids=[])
            with _patch_resolve_mode(), \
                 patch("crawler.india_price_feed.is_nse_market_open", return_value=False):
                result = await authorize_trade_intent(intent, make_session())
            assert result.outcome == RoutingOutcome.BLOCKED_MARKET_CLOSED, f"family={family}"

    @pytest.mark.asyncio
    async def test_does_not_block_when_market_open(self):
        # Positive control: with the gate patched open (matching the autouse
        # fixture's default) and everything else valid, the intent reaches
        # past this check (approved, or blocked for an unrelated reason —
        # never BLOCKED_MARKET_CLOSED).
        intent = make_intent(strategy_family=StrategyFamily.EVENT_DRIVEN)
        canonical = make_canonical_event(bullish=["TESTCO"])
        wallet_patch, risk_patch = _patch_equity_approval()
        with _patch_resolve_mode(), wallet_patch, risk_patch, \
             patch("crawler.india_price_feed.is_nse_market_open", return_value=True):
            result = await authorize_trade_intent(intent, make_session(canonical))
        assert result.outcome != RoutingOutcome.BLOCKED_MARKET_CLOSED

# ── 8. Sector-mood entry gate (2026-08-04) ────────────────────────────────────
# HONASA.NS: entered while NSE-wide breadth was already STRONGLY_BEARISH (the
# existing check only halved size), then auto-closed 27 seconds later by the
# sector-reversal exit in paper_trading/trade_simulator.py -- which checks
# THIS symbol's own sector mood, a different signal the entry gate never
# looked at. execute_trade_intent() now blocks outright when the entry
# symbol's own sector already matches that exit trigger, instead of just
# halving size on the broader (and different) breadth signal.

def _fno_snap_none_session(canonical=None) -> AsyncMock:
    """Like make_session(), but also wires session.execute().scalar_one_or_none()
    to None so execute_trade_intent()'s sizing block
    (which runs before the sector-mood check) is a clean no-op."""
    session = make_session(canonical)
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = []
    exec_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=exec_result)
    return session


class TestSectorMoodEntryGate:
    @pytest.mark.asyncio
    async def test_blocks_buy_when_own_sector_already_strongly_bearish(self):
        from engine.decision_router import execute_trade_intent

        canonical = make_canonical_event(materiality="HIGH", bullish=["TESTCO"], bearish=[])
        intent = make_intent(position_size_hint={"units": 10, "usd_value": 1000.0})
        wallet_patch, risk_patch = _patch_equity_approval()
        sector_ctx = SimpleNamespace(sector_moods={"IT": "STRONGLY_BEARISH"})
        with _patch_resolve_mode(), wallet_patch, risk_patch, \
             patch("engine.intelligence_hub._get_sector_for_symbol", return_value="IT"), \
             patch("engine.intelligence_hub.build_sector_context", return_value=sector_ctx):
            result = await execute_trade_intent(intent, _fno_snap_none_session(canonical))
        assert result.outcome == RoutingOutcome.BLOCKED_GATE
        assert "sector" in result.reason.lower() and "strongly_bearish" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_does_not_block_when_own_sector_is_neutral(self):
        # Positive control: a NEUTRAL sector mood must not trip the new gate
        # (route_decision is mocked so the test doesn't need a full open-
        # position execution path -- the point is only that this check
        # doesn't wrongly block).
        from engine.decision_router import execute_trade_intent

        canonical = make_canonical_event(materiality="HIGH", bullish=["TESTCO"], bearish=[])
        intent = make_intent(position_size_hint={"units": 10, "usd_value": 1000.0})
        wallet_patch, risk_patch = _patch_equity_approval()
        sector_ctx = SimpleNamespace(sector_moods={"IT": "NEUTRAL"})
        fake_route_result = MagicMock(outcome=RoutingOutcome.EXECUTED_PAPER)
        with _patch_resolve_mode(), wallet_patch, risk_patch, \
             patch("engine.intelligence_hub._get_sector_for_symbol", return_value="IT"), \
             patch("engine.intelligence_hub.build_sector_context", return_value=sector_ctx), \
             patch("engine.decision_router.route_decision", AsyncMock(return_value=fake_route_result)), \
             patch("engine.decision_router._log_intent_audit", AsyncMock()):
            result = await execute_trade_intent(intent, _fno_snap_none_session(canonical))
        assert result.outcome != RoutingOutcome.BLOCKED_GATE or "sector" not in (result.reason or "").lower()
