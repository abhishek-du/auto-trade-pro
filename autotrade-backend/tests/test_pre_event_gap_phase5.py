"""Phase 5 tests: replay / validation harness.

Emphasis: (1) anti-lookahead — every prediction is frozen at as_of = event_date
- offset, strictly before the event; (2) outcome math incl. cost and Nifty
adjustment; (3) the verdict's sample-size gate and edge classification.
Fully mocked; no real DB.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import engine.pre_event_expectation_gap.replay as replay
from engine.pre_event_expectation_gap.replay import (
    _window_return, compute_replay_verdict, _summarize, replay_event, evaluate_outcome,
    OutcomeRecord, CUTOFF_OFFSETS, REACTION_WINDOWS, _ROUND_TRIP_COST,
)
from engine.pre_event_expectation_gap.types import PreEventType, PreEventDecision


# ── Pure helpers ─────────────────────────────────────────────────────────────

class TestWindowReturn:
    def test_basic(self):
        assert _window_return(100.0, 110.0) == pytest.approx(0.10)

    def test_none_inputs(self):
        assert _window_return(None, 110.0) is None
        assert _window_return(100.0, None) is None
        assert _window_return(0.0, 110.0) is None


# ── Anti-lookahead: predictions frozen before the event ──────────────────────

class TestAntiLookahead:
    @pytest.mark.asyncio
    async def test_prediction_as_of_is_before_event_at_each_cutoff(self):
        event_date = date(2026, 7, 24)
        seen_as_of = []

        async def fake_predict(symbol, ev, as_of, session):
            seen_as_of.append(as_of)
            return SimpleNamespace(decision=PreEventDecision.LONG, pre_event_score=70.0)

        engine = SimpleNamespace(predict=fake_predict)
        with patch.object(replay, "_close_near", AsyncMock(return_value=100.0)), \
             patch.object(replay, "evaluate_outcome", AsyncMock(return_value={})):
            recs = await replay_event(engine, "MARUTI.NS", event_date,
                                      PreEventType.QUARTERLY_RESULT, AsyncMock())

        # one prediction per cutoff, each strictly before the event, at the right offset
        assert len(seen_as_of) == len(CUTOFF_OFFSETS)
        for as_of, offset in zip(seen_as_of, CUTOFF_OFFSETS):
            assert as_of.date() == event_date - timedelta(days=offset)
            assert as_of.date() < event_date
        assert {r.cutoff_offset for r in recs} == set(CUTOFF_OFFSETS)


# ── Outcome math (cost + adjustment + MFE/MAE) ───────────────────────────────

class TestEvaluateOutcome:
    @pytest.mark.asyncio
    async def test_net_applies_cost_and_nifty_adjustment(self):
        event_date = date(2026, 7, 24)
        as_of = datetime(2026, 7, 23)

        # stock: entry 100 → +10% ; nifty: entry 100 → +2%  (so nifty_adj ≈ 10% - cost - 2%)
        async def close_near(symbol, target, session, tol=4):
            if symbol == replay.NIFTY_SYMBOL:
                return 102.0 if target > event_date else 100.0
            return 110.0 if target > event_date else 100.0

        async def candles_between(symbol, start, end, session):
            return [SimpleNamespace(high=115.0, low=98.0)]

        with patch.object(replay, "_close_near", side_effect=close_near), \
             patch.object(replay, "_candles_between", side_effect=candles_between):
            out = await evaluate_outcome("MARUTI.NS", event_date, as_of, 100.0, None, AsyncMock())

        rec = out["t+3"]
        assert rec["gross"] == pytest.approx(0.10)
        assert rec["net"] == pytest.approx(0.10 - _ROUND_TRIP_COST)
        assert rec["nifty_adj"] == pytest.approx((0.10 - _ROUND_TRIP_COST) - 0.02)
        assert rec["mfe"] == pytest.approx(0.15)   # (115-100)/100
        assert rec["mae"] == pytest.approx(-0.02)  # (98-100)/100

    @pytest.mark.asyncio
    async def test_no_entry_price_returns_empty(self):
        out = await evaluate_outcome("X.NS", date(2026, 7, 24), datetime(2026, 7, 23), None, None, AsyncMock())
        assert out == {}


# ── Summarize + verdict ──────────────────────────────────────────────────────

def _rec(offset, decision, t3_net=0.05, t3_nifty=0.03):
    return OutcomeRecord(
        cutoff_offset=offset, as_of=datetime(2026, 7, 1), decision=decision,
        entry_price=100.0, pre_event_score=70.0,
        by_window={"t+3": {"gross": t3_net + _ROUND_TRIP_COST, "net": t3_net, "nifty_adj": t3_nifty}},
    )


class TestSummarize:
    def test_filters_by_decision_and_aggregates(self):
        recs = [_rec(1, "LONG", t3_nifty=0.04), _rec(1, "LONG", t3_nifty=-0.01), _rec(1, "WAIT")]
        summ = _summarize(recs, decision_filter="LONG")
        w = summ["by_cutoff"]["T-1"]["windows"]["t+3"]
        assert w["n"] == 2
        assert w["hit_rate_nifty_adj"] == 0.5   # 1 of 2 positive


class TestVerdict:
    def _report(self, n_matched, hit, mean_adj, n_primary):
        return {"long_summary": {
            "n_matched": n_matched,
            "by_cutoff": {"T-1": {"windows": {"t+3": {
                "n": n_primary, "hit_rate_nifty_adj": hit, "mean_nifty_adj": mean_adj,
            }}}},
        }}

    def test_insufficient_sample(self):
        v = compute_replay_verdict(self._report(5, 0.9, 0.05, 5))
        assert v["edge_status"] == "INSUFFICIENT SAMPLE"

    def test_edge_confirmed(self):
        v = compute_replay_verdict(self._report(40, 0.60, 0.02, 30))
        assert v["edge_status"] == "EDGE CONFIRMED"
        assert "caveats" in v

    def test_no_edge(self):
        v = compute_replay_verdict(self._report(40, 0.35, -0.01, 30))
        assert v["edge_status"] == "NO EDGE"

    def test_uncertain(self):
        v = compute_replay_verdict(self._report(40, 0.52, 0.0, 30))
        assert v["edge_status"] == "EDGE UNCERTAIN"

    def test_verdict_always_has_do_not_use_real_money_unless_confirmed(self):
        for rep in (self._report(5, 0.9, 0.9, 5), self._report(40, 0.35, -0.1, 30), self._report(40, 0.52, 0.0, 30)):
            v = compute_replay_verdict(rep)
            assert "Do NOT use real money" in v["recommendation"]
