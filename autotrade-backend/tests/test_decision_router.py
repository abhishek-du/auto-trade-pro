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

    @pytest.mark.asyncio
    async def test_fno_strategy_family_not_hard_blocked(self):
        # The hard-block is specific to TECHNICAL -- FNO must not be caught
        # by the same net (it has its own separate gating elsewhere).
        intent = make_intent(
            strategy_family=StrategyFamily.FNO, instrument_type="FUTURE",
            event_directness=EventDirectness.NOT_APPLICABLE, event_id=None, evidence_ids=[],
        )
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, make_session())
        assert result.outcome != RoutingOutcome.BLOCKED_TECHNICAL_ORIGIN


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

    @pytest.mark.asyncio
    async def test_technical_and_fno_intents_skip_event_check(self):
        # _verify_canonical_event() short-circuits True for non-EVENT_DRIVEN
        # families -- confirmed directly since TECHNICAL is blocked earlier
        # for a DIFFERENT reason (see TestTechnicalHardBlock); here we prove
        # FNO with no event_id at all does not hit BLOCKED_NO_EVENT.
        intent = make_intent(
            strategy_family=StrategyFamily.FNO, instrument_type="FUTURE",
            event_directness=EventDirectness.NOT_APPLICABLE, event_id=None, evidence_ids=[],
        )
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, make_session())
        assert result.outcome != RoutingOutcome.BLOCKED_NO_EVENT


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
