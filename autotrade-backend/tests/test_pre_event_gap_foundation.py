"""Phase 1 (foundation) tests for the parallel Pre-Event Expectation Gap strategy.

Covers: strategy identity, decision states, config flags (all default OFF),
the new nullable `source` attribution column, audit serialization, and — most
importantly — the gate-isolation guarantee: a StrategyFamily.PRE_EVENT intent
routes through the existing central execution gate on its own merits (not
TECHNICAL-blocked, not requiring a news CausalEvent) WITHOUT weakening any
existing check. Fully mocked; no network, no DB.
"""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import json
import pytest

from engine.pre_event_expectation_gap import (
    STRATEGY_ID, TRADE_SOURCE, STRATEGY_VERSION,
    PreEventDecision, PreEventType, NowcastStatus, PriceDiscountStatus,
    ScheduledEvent, NowcastResult, ExpectationEstimate, PriceDiscount,
    RelativeStrength, PreEventPrediction,
)
from engine.decision_router import (
    TradeIntent, TradeMode, RoutingOutcome, ConfidenceSource, EventDirectness,
    StrategyFamily, authorize_trade_intent,
)
from db.models import PaperTrade, AgentTrade
from utils.config import settings


# ── local gate-test helpers (self-contained; mirror test_decision_router.py) ──

def _make_pre_event_intent(**overrides) -> TradeIntent:
    defaults = dict(
        strategy=STRATEGY_ID,
        symbol="TVSMOTOR.NS",
        action="BUY",
        instrument_type="EQUITY",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        confidence=80.0,
        confidence_source=ConfidenceSource.CALCULATED,
        strategy_family=StrategyFamily.PRE_EVENT,
        event_directness=EventDirectness.NOT_APPLICABLE,   # not a news CausalEvent
        evidence_ids=[],
        event_id=None,
    )
    defaults.update(overrides)
    return TradeIntent(**defaults)


def _make_session(canonical_event=None) -> AsyncMock:
    session = AsyncMock()
    session.get = AsyncMock(return_value=canonical_event)
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=exec_result)
    session.add = MagicMock()
    return session


def _patch_resolve_mode(mode=TradeMode.PAPER):
    return patch("engine.decision_router.resolve_mode", AsyncMock(return_value=mode))


def _patch_equity_approval():
    return (
        patch("paper_trading.virtual_wallet.VirtualWallet.get_summary",
              AsyncMock(return_value={"balance": 1_000_000.0})),
        patch("engine.risk_manager.validate_signal", AsyncMock(return_value=(True, "ok"))),
    )


# ── 1. Strategy identity ─────────────────────────────────────────────────────

class TestStrategyIdentity:
    def test_strategy_id(self):
        assert STRATEGY_ID == "PRE_EVENT_EXPECTATION_GAP"

    def test_trade_source_is_ai_predict(self):
        assert TRADE_SOURCE == "AI Predict"

    def test_version_present(self):
        assert STRATEGY_VERSION.startswith("v")


# ── 2. Decision states ───────────────────────────────────────────────────────

class TestDecisionStates:
    def test_exactly_four_states(self):
        assert {d.value for d in PreEventDecision} == {"LONG", "SHORT", "WAIT", "NO_TRADE"}

    def test_wait_and_no_trade_exist(self):
        assert PreEventDecision.WAIT.value == "WAIT"
        assert PreEventDecision.NO_TRADE.value == "NO_TRADE"


# ── 3. Config flags (all default OFF — fail-closed) ─────────────────────────

class TestConfigFlags:
    def test_master_flag_default_off(self):
        from utils.config import Settings
        assert Settings.model_fields["PRE_EVENT_GAP_ENABLED"].default is False

    def test_paper_flag_default_off(self):
        from utils.config import Settings
        assert Settings.model_fields["PRE_EVENT_GAP_PAPER_TRADING"].default is False

    def test_live_flag_default_off(self):
        from utils.config import Settings
        assert Settings.model_fields["PRE_EVENT_GAP_LIVE_TRADING"].default is False

    def test_flags_are_bools_at_runtime(self):
        for name in ("PRE_EVENT_GAP_ENABLED", "PRE_EVENT_GAP_PAPER_TRADING", "PRE_EVENT_GAP_LIVE_TRADING"):
            assert isinstance(getattr(settings, name), bool)


# ── 4. Source attribution column ─────────────────────────────────────────────

class TestSourceColumn:
    def test_paper_trade_has_nullable_source(self):
        col = PaperTrade.__table__.columns["source"]
        assert col.nullable is True

    def test_agent_trade_has_nullable_source(self):
        col = AgentTrade.__table__.columns["source"]
        assert col.nullable is True

    def test_source_defaults_to_none_on_new_row(self):
        # A freshly-constructed PaperTrade (no source set) must be NULL, so
        # every existing/News row stays "AI" and only the new strategy sets
        # "AI Predict" -- no existing value is ever overwritten.
        t = PaperTrade.__new__(PaperTrade)
        assert getattr(t, "source", None) is None

    def test_existing_strategy_name_column_unchanged(self):
        # Regression: the News Strategy's attribution field must still exist,
        # unchanged, alongside the new source column.
        assert "strategy_name" in PaperTrade.__table__.columns
        assert PaperTrade.__table__.columns["strategy_name"].nullable is True


# ── 5. Audit serialization ───────────────────────────────────────────────────

def _sample_prediction(decision=PreEventDecision.NO_TRADE) -> PreEventPrediction:
    ev = ScheduledEvent(
        symbol="TVSMOTOR.NS", event_type=PreEventType.QUARTERLY_RESULT,
        event_date=date(2026, 7, 24), event_confidence=0.95, source="market_event_calendar",
    )
    return PreEventPrediction(
        symbol="TVSMOTOR.NS", event=ev, prediction_cutoff=datetime(2026, 7, 23, 15, 30),
        decision=decision, nowcast=NowcastResult(), expectation=ExpectationEstimate(),
        price_discount=PriceDiscount(), relative_strength=RelativeStrength(),
        decision_reason="nowcast unavailable",
    )


class TestAuditSerialization:
    def test_audit_dict_is_json_serializable(self):
        d = _sample_prediction().to_audit_dict()
        assert json.dumps(d)   # must not raise

    def test_audit_dict_carries_source_and_decision(self):
        d = _sample_prediction(PreEventDecision.LONG).to_audit_dict()
        assert d["source"] == "AI Predict"
        assert d["prediction_direction"] == "LONG"
        assert d["strategy"] == STRATEGY_ID

    def test_audit_dict_never_stores_decision_alone(self):
        # The spec: never store only the final decision — the full component
        # breakdown must be present for auditability.
        d = _sample_prediction().to_audit_dict()
        for k in ("nowcast", "expectation", "price_discount", "relative_strength"):
            assert k in d["components"]

    def test_nowcast_unavailable_is_first_class_state(self):
        nc = NowcastResult()
        assert nc.status == NowcastStatus.UNAVAILABLE
        assert nc.available is False


# ── 6. Gate isolation (the critical guarantee) ───────────────────────────────

class TestGateIsolation:
    @pytest.mark.asyncio
    async def test_pre_event_not_hit_by_technical_hard_block(self):
        intent = _make_pre_event_intent()
        with _patch_resolve_mode():
            w, r = _patch_equity_approval()
            with w, r:
                result = await authorize_trade_intent(intent, _make_session())
        assert result.outcome != RoutingOutcome.BLOCKED_TECHNICAL_ORIGIN

    @pytest.mark.asyncio
    async def test_pre_event_does_not_require_a_causal_event(self):
        # event_id=None / no evidence_ids must NOT trigger BLOCKED_NO_EVENT —
        # PRE_EVENT is not EVENT_DRIVEN, so the news-event invariant doesn't apply.
        intent = _make_pre_event_intent(event_id=None, evidence_ids=[])
        with _patch_resolve_mode():
            w, r = _patch_equity_approval()
            with w, r:
                result = await authorize_trade_intent(intent, _make_session())
        assert result.outcome != RoutingOutcome.BLOCKED_NO_EVENT

    @pytest.mark.asyncio
    async def test_pre_event_with_calculated_conf_and_valid_risk_is_approved(self):
        intent = _make_pre_event_intent()
        with _patch_resolve_mode():
            w, r = _patch_equity_approval()
            with w, r:
                result = await authorize_trade_intent(intent, _make_session())
        assert result.approved is True

    @pytest.mark.asyncio
    async def test_pre_event_is_NOT_a_bypass_hardcoded_conf_still_blocked(self):
        # Proves PRE_EVENT skips only the two family-specific blocks, NOT the
        # universal gates: a hardcoded (non-calculated) confidence must still
        # be rejected exactly like any other family.
        intent = _make_pre_event_intent(confidence_source=ConfidenceSource.HARDCODED)
        with _patch_resolve_mode():
            result = await authorize_trade_intent(intent, _make_session())
        assert result.approved is False
        assert result.outcome == RoutingOutcome.BLOCKED_CONFIDENCE_INTEGRITY

    @pytest.mark.asyncio
    async def test_pre_event_still_subject_to_equity_risk_validation(self):
        # If validate_signal rejects (e.g. cash buffer / sector cap), PRE_EVENT
        # must be blocked too — it gets the SAME risk protection as everything else.
        intent = _make_pre_event_intent()
        with _patch_resolve_mode(), \
             patch("paper_trading.virtual_wallet.VirtualWallet.get_summary",
                   AsyncMock(return_value={"balance": 1_000_000.0})), \
             patch("engine.risk_manager.validate_signal",
                   AsyncMock(return_value=(False, "sector cap reached"))):
            result = await authorize_trade_intent(intent, _make_session())
        assert result.approved is False
        assert result.outcome == RoutingOutcome.BLOCKED_GATE
