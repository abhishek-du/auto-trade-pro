"""Tests for the fast market-shock guard (engine/agent/shock_guard.py).

Covers the escalation matrix (index drop × news burst → shock level), the news
keyword filter, and the tighten/flatten actions on open longs. Signal inputs
(yfinance index bars, DB news) are monkeypatched so the tests are hermetic.
"""
import asyncio
import types

import pytest

import engine.agent.shock_guard as sg
from utils.config import settings


def _assess(monkeypatch, drop_pct: float, news_hits: int) -> sg.ShockAssessment:
    """Run assess_market_shock with the two raw signals stubbed to fixed values."""
    monkeypatch.setattr(sg, "_sync_worst_index_drop",
                        lambda syms, win: (drop_pct, f"stub {drop_pct}%"))

    async def _fake_news(session, window_min):
        return news_hits, ["stub headline"] * min(news_hits, 5)
    monkeypatch.setattr(sg, "_news_shock_hits", _fake_news)

    return asyncio.run(sg.assess_market_shock(session=None))


class TestEscalationMatrix:
    def test_calm_is_none(self, monkeypatch):
        assert _assess(monkeypatch, -0.3, 0).level == sg.SHOCK_NONE

    def test_index_tighten_band(self, monkeypatch):
        # −1.2% is past TIGHTEN (1.0) but not FLATTEN (2.0)
        assert _assess(monkeypatch, -1.2, 0).level == sg.SHOCK_TIGHTEN

    def test_index_flatten_band(self, monkeypatch):
        assert _assess(monkeypatch, -2.5, 0).level == sg.SHOCK_FLATTEN

    def test_news_alone_escalates_none_to_tighten(self, monkeypatch):
        a = _assess(monkeypatch, -0.3, settings.SHOCK_NEWS_MIN_HITS)
        assert a.level == sg.SHOCK_TIGHTEN

    def test_news_plus_index_tighten_becomes_flatten(self, monkeypatch):
        a = _assess(monkeypatch, -1.2, settings.SHOCK_NEWS_MIN_HITS)
        assert a.level == sg.SHOCK_FLATTEN

    def test_flatten_is_capped(self, monkeypatch):
        # already FLATTEN from the index, a news burst can't exceed the top level
        a = _assess(monkeypatch, -2.5, 10)
        assert a.level == sg.SHOCK_FLATTEN

    def test_below_news_threshold_does_not_escalate(self, monkeypatch):
        a = _assess(monkeypatch, -0.3, settings.SHOCK_NEWS_MIN_HITS - 1)
        assert a.level == sg.SHOCK_NONE


class TestNewsKeywordFilter:
    def test_market_panic_headline_matches(self):
        low = "sensex plunges 1600 points as war fears grip dalal street".lower()
        assert any(kw in low for kw in sg._SHOCK_KEYWORDS)

    def test_geopolitical_headline_matches(self):
        low = "trump says iran ceasefire is over, launches airstrike".lower()
        assert any(kw in low for kw in sg._SHOCK_KEYWORDS)

    def test_routine_headline_does_not_match(self):
        low = "reliance q1 profit rises 8% on strong retail growth".lower()
        assert not any(kw in low for kw in sg._SHOCK_KEYWORDS)


# ── Action tests (fake positions + session) ──────────────────────────────────

class _FakePos:
    def __init__(self, symbol, entry, stop, direction="BUY", itype="EQUITY"):
        self.symbol = symbol
        self.entry_price = entry
        self.stop_loss = stop
        self.direction = types.SimpleNamespace(name=direction)
        self.instrument_type = itype
        self.trade_id = 1


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows
    def scalars(self):
        return types.SimpleNamespace(all=lambda: self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.commits = 0
    async def execute(self, *_a, **_k):
        return _FakeResult(self._rows)
    async def commit(self):
        self.commits += 1
    async def rollback(self):
        pass


def _patch_common(monkeypatch, prices):
    # TradeDirection.BUY comparison: _FakePos.direction has .name == "BUY";
    # patch the model import target so `p.direction == TradeDirection.BUY` holds.
    import db.models as models
    monkeypatch.setattr(models, "TradeDirection",
                        types.SimpleNamespace(BUY=types.SimpleNamespace(name="BUY")))

    async def _fake_prices(symbols):
        return {s: prices.get(s, 0.0) for s in symbols}
    monkeypatch.setattr(sg, "_live_prices_for", _fake_prices)


def test_tighten_raises_stop_only_upward(monkeypatch):
    pos = _FakePos("TBZ.NS", entry=200.0, stop=189.0)
    sess = _FakeSession([pos])
    _patch_common(monkeypatch, {"TBZ.NS": 210.0})

    a = sg.ShockAssessment(level=sg.SHOCK_TIGHTEN, reason="stub")
    res = asyncio.run(sg.apply_shock_action(a, sess))

    # 210 * (1 - 0.5%) = 208.95 > old 189 → stop lifted to lock the gain
    assert pos.stop_loss == pytest.approx(208.95, abs=0.01)
    assert res["tightened"] and res["tightened"][0]["new_stop"] == pytest.approx(208.95, abs=0.01)


def test_tighten_never_loosens_a_tighter_stop(monkeypatch):
    pos = _FakePos("TBZ.NS", entry=200.0, stop=209.5)   # already tighter than 208.95
    sess = _FakeSession([pos])
    _patch_common(monkeypatch, {"TBZ.NS": 210.0})

    a = sg.ShockAssessment(level=sg.SHOCK_TIGHTEN, reason="stub")
    res = asyncio.run(sg.apply_shock_action(a, sess))

    assert pos.stop_loss == 209.5           # unchanged — never pulled down
    assert not res["tightened"]


def test_flatten_closes_and_sets_cooldown(monkeypatch):
    pos = _FakePos("TBZ.NS", entry=200.0, stop=189.0)
    sess = _FakeSession([pos])
    _patch_common(monkeypatch, {"TBZ.NS": 205.0})

    closed = {}
    async def _fake_close(position, price, reason, session):
        closed.update(symbol=position.symbol, price=price, reason=reason)
        return types.SimpleNamespace(pnl=(price - position.entry_price))
    monkeypatch.setattr("paper_trading.trade_simulator.close_paper_trade", _fake_close)

    cooldown = {}
    async def _fake_set(session, key, value):
        cooldown[key] = value
    monkeypatch.setattr("utils.runtime_config.RuntimeConfig.set", staticmethod(_fake_set))

    a = sg.ShockAssessment(level=sg.SHOCK_FLATTEN, reason="stub")
    res = asyncio.run(sg.apply_shock_action(a, sess))

    assert closed["reason"] == "MARKET_SHOCK_FLATTEN"
    assert closed["price"] == 205.0
    assert res["closed"] and res["closed"][0]["symbol"] == "TBZ.NS"
    assert "shock_cooldown_until" in cooldown     # entry cooldown armed
