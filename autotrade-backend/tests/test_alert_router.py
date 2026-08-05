"""Tests for integrations/alerts/ — the event bus that replaced 29 scattered
telegram_service.send()/fire() call sites (Phase 1), and the real
decision-first/reason/detail template engine that replaced the old
fmt_entry/fmt_exit/fmt_shortlist_alert renderers (Phase 2).

Run:
    cd autotrade-backend
    .venv/bin/python -m pytest tests/test_alert_router.py -v --tb=short
"""
from __future__ import annotations

import re
import uuid
from unittest.mock import AsyncMock, patch

import pytest


def _uniq(prefix: str) -> str:
    """Unique-per-run key/symbol so dedup-state tests (backed by real Redis
    with a multi-day TTL) never collide with leftover state from a previous
    test run."""
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


_ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a", "blockquote"}
_TAG_RE = re.compile(r"</?([a-zA-Z]+)[^>]*>")


def assert_balanced_html(text: str) -> None:
    """Every tag Telegram's HTML subset supports must open/close in a
    properly nested stack -- a real risk once templates build multi-section
    messages with string concatenation instead of one static f-string."""
    stack: list[str] = []
    for m in _TAG_RE.finditer(text):
        tag = m.group(1).lower()
        is_close = m.group(0).startswith("</")
        assert tag in _ALLOWED_TAGS, f"unsupported Telegram HTML tag <{tag}> in: {text!r}"
        if is_close:
            assert stack and stack[-1] == tag, f"unbalanced </{tag}> in: {text!r}"
            stack.pop()
        else:
            stack.append(tag)
    assert not stack, f"unclosed tag(s) {stack} in: {text!r}"


def assert_no_markdown_leakage(text: str) -> None:
    """No literal *bold*/_italic_/`code` markdown syntax should reach the
    chat -- Telegram is always sent with parse_mode=HTML, so markdown-style
    asterisks/underscores render as literal characters, not formatting."""
    # Strip anything already inside a <code>/<pre> block (raw text there is fine).
    stripped = re.sub(r"<(code|pre)>.*?</\1>", "", text, flags=re.DOTALL)
    assert not re.search(r"(?<!\w)\*[^*\n]+\*(?!\w)", stripped), f"literal *markdown* found in: {text!r}"
    assert not re.search(r"(?<!\w)`[^`\n]+`(?!\w)", stripped), f"literal `markdown` found in: {text!r}"


from integrations.alerts import (
    AlertAction,
    AlertCategory,
    AlertEvent,
    RawTextPayload,
    Severity,
    ShortlistPayload,
    TradeEntryPayload,
    TradeExitPayload,
    publish,
)
from integrations.alerts.dedup import shortlist_gate, shortlist_would_alert
from integrations.alerts.templates import render


class _FakeDecision:
    """Minimal duck-typed stand-in for the Signal/Decision object fmt_entry expects."""
    def __init__(self, symbol="TCS.NS", action="BUY", entry=100.0, stop=95.0, target=110.0):
        self.symbol = symbol
        self.action = action
        self.entry = entry
        self.stop = stop
        self.target = target
        self.confidence = 70.0
        self.master_score = 42.0
        self.regime = "BULL_TRENDING"
        self.strategy = "TEST_STRATEGY"
        self.reasons = []
        self.hub_subscores = {}
        self.target_2 = 0.0
        self.atr = 0.0
        self.qty = 5


class _FakeCandidate(_FakeDecision):
    pass


@pytest.fixture(autouse=True)
def _telegram_available():
    with patch("integrations.alerts.router.settings") as mock_settings:
        mock_settings.telegram_available = True
        mock_settings.TELEGRAM_MIN_SEVERITY = "INFO"
        yield mock_settings


@pytest.fixture
def mock_post():
    with patch("integrations.telegram_service._post", new_callable=AsyncMock) as m:
        yield m


# ── render() structure: decision-first, valid HTML, no markdown leakage ─────

@pytest.mark.asyncio
async def test_trade_entry_renders_decision_first(mock_post):
    decision = _FakeDecision()
    decision.hub_subscores = {"technical": 30.0, "news": 12.0}
    await publish(AlertEvent(
        category=AlertCategory.TRADE, action=AlertAction.ENTRY, severity=Severity.SUCCESS,
        symbol=decision.symbol, payload=TradeEntryPayload(decision=decision, qty=5),
    ))
    mock_post.assert_awaited_once()
    text = mock_post.call_args[0][0]
    assert_balanced_html(text)
    assert_no_markdown_leakage(text)
    # Decision-first: symbol, entry/stop/target, and confidence all appear
    # before the 7-factor breakdown section.
    decision_end = text.index("Confidence")
    assert "TCS" in text[:decision_end]
    assert "Entry" in text[:decision_end] and "Stop" in text[:decision_end]
    assert "7-Factor Breakdown" in text
    assert text.index("7-Factor Breakdown") > decision_end


@pytest.mark.asyncio
async def test_trade_exit_renders_decision_first(mock_post):
    trade_id = int(uuid.uuid4().int % 1_000_000_000)  # unique per run -- see _uniq()
    await publish(AlertEvent(
        category=AlertCategory.TRADE, action=AlertAction.EXIT, severity=Severity.SUCCESS,
        symbol="TCS.NS", trade_id=trade_id,
        payload=TradeExitPayload(symbol="TCS.NS", side="BUY", entry=100.0, exit_price=105.0,
                                  qty=10, pnl=50.0, reason="TAKE_PROFIT"),
    ))
    mock_post.assert_awaited_once()
    text = mock_post.call_args[0][0]
    assert_balanced_html(text)
    assert_no_markdown_leakage(text)
    assert "POSITION CLOSED" in text
    assert "TCS" in text
    assert "+₹50" in text or "50" in text


@pytest.mark.asyncio
async def test_shortlist_renders_decision_first(mock_post):
    candidate = _FakeCandidate()
    candidate.hub_subscores = {"signal": "STRONG_BUY", "technical": 20.0}
    await publish(AlertEvent(
        category=AlertCategory.SHORTLIST, action=AlertAction.ALERT, severity=Severity.INFO,
        symbol="TCS", payload=ShortlistPayload(candidate=candidate, score=42.0, news_subscore=0.0, executed=True),
    ))
    mock_post.assert_awaited_once()
    text = mock_post.call_args[0][0]
    assert_balanced_html(text)
    assert_no_markdown_leakage(text)
    assert "SHORTLIST" in text
    assert "EXECUTED" in text  # executed=True must be visible in the decision line


@pytest.mark.asyncio
async def test_raw_text_gets_consistent_severity_header(mock_post):
    await publish(AlertEvent(
        category=AlertCategory.OPERATIONS, action=AlertAction.ERROR, severity=Severity.CRITICAL,
        dedup_key=_uniq("raw-text"), payload=RawTextPayload(text="body line one\nbody line two"),
    ))
    mock_post.assert_awaited_once()
    text = mock_post.call_args[0][0]
    assert_balanced_html(text)
    # Severity badge + category label prepended, original body preserved verbatim after it.
    assert text.startswith("🔴 <b>System</b>")
    assert "body line one\nbody line two" in text


def test_render_all_categories_produce_valid_html():
    """Every payload type, rendered directly (no dedup/network involved),
    must produce balanced HTML with no markdown leakage -- a cheap,
    exhaustive sweep across every category the router can emit."""
    decision = _FakeDecision()
    candidate = _FakeCandidate()
    cases = [
        AlertEvent(category=AlertCategory.TRADE, action=AlertAction.ENTRY, severity=Severity.SUCCESS,
                   payload=TradeEntryPayload(decision=decision, qty=1)),
        AlertEvent(category=AlertCategory.TRADE, action=AlertAction.EXIT, severity=Severity.SUCCESS,
                   payload=TradeExitPayload(symbol="X.NS", side="SELL", entry=1.0, exit_price=0.9,
                                             qty=1, pnl=-1.0, reason="STOP_LOSS")),
        AlertEvent(category=AlertCategory.SHORTLIST, action=AlertAction.ALERT, severity=Severity.INFO,
                   payload=ShortlistPayload(candidate=candidate, score=-10.0, news_subscore=0.0, executed=False)),
        AlertEvent(category=AlertCategory.OPERATIONS, action=AlertAction.ERROR, severity=Severity.EMERGENCY,
                   payload=RawTextPayload(text="x")),
        AlertEvent(category=AlertCategory.WEEKLY_REPORT, action=AlertAction.REPORT, severity=Severity.INFO,
                   payload=RawTextPayload(text="y")),
    ]
    for event in cases:
        text = render(event)
        assert_balanced_html(text)
        assert_no_markdown_leakage(text)


# ── availability + severity gating ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_suppressed_when_telegram_unavailable(mock_post, _telegram_available):
    _telegram_available.telegram_available = False
    await publish(AlertEvent(
        category=AlertCategory.OPERATIONS, action=AlertAction.ERROR, severity=Severity.CRITICAL,
        payload=RawTextPayload(text="x"),
    ))
    mock_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_suppressed_below_min_severity(mock_post, _telegram_available):
    _telegram_available.TELEGRAM_MIN_SEVERITY = "WARNING"
    await publish(AlertEvent(
        category=AlertCategory.DISCOVERY, action=AlertAction.ALERT, severity=Severity.INFO,
        payload=RawTextPayload(text="x"),
    ))
    mock_post.assert_not_awaited()


# ── dedup / cooldown ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_exit_dedup_by_trade_id(mock_post):
    trade_id = int(uuid.uuid4().int % 1_000_000_000)  # unique per run
    ev = AlertEvent(
        category=AlertCategory.TRADE, action=AlertAction.EXIT, severity=Severity.SUCCESS,
        symbol="INFY.NS", trade_id=trade_id,
        payload=TradeExitPayload(symbol="INFY.NS", side="BUY", entry=1.0, exit_price=2.0,
                                  qty=1, pnl=1.0, reason="TAKE_PROFIT"),
    )
    await publish(ev)
    await publish(ev)
    assert mock_post.await_count == 1


@pytest.mark.asyncio
async def test_explicit_dedup_key_overrides_default(mock_post):
    key_a, key_b = _uniq("watchdog"), _uniq("watchdog")
    ev1 = AlertEvent(
        category=AlertCategory.OPERATIONS, action=AlertAction.ERROR, severity=Severity.CRITICAL,
        dedup_key=key_a, payload=RawTextPayload(text="a"),
    )
    ev2 = AlertEvent(
        category=AlertCategory.OPERATIONS, action=AlertAction.ERROR, severity=Severity.CRITICAL,
        dedup_key=key_b, payload=RawTextPayload(text="b"),
    )
    await publish(ev1)
    await publish(ev2)
    assert mock_post.await_count == 2  # distinct dedup keys never collide


@pytest.mark.asyncio
async def test_dedup_fails_open_when_redis_unreachable(mock_post):
    trade_id = int(uuid.uuid4().int % 1_000_000_000)
    ev = AlertEvent(
        category=AlertCategory.TRADE, action=AlertAction.EXIT, severity=Severity.SUCCESS,
        symbol="WIPRO.NS", trade_id=trade_id,
        payload=TradeExitPayload(symbol="WIPRO.NS", side="BUY", entry=1.0, exit_price=2.0,
                                  qty=1, pnl=1.0, reason="STOP_LOSS"),
    )
    with patch("utils.cache.get_redis", side_effect=RuntimeError("redis down")):
        await publish(ev)
        await publish(ev)
    # Fails open: never suppresses, so both sends go through despite "duplicate" trade_id.
    assert mock_post.await_count == 2


# ── shortlist content-delta gate ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_shortlist_gate_executed_always_alerts():
    sym = _uniq("TCS")
    assert await shortlist_gate(sym, score=10.0, news_subscore=0.0, executed=True) is True
    # Immediately again -- executed always bypasses, even for the same symbol/score.
    assert await shortlist_gate(sym, score=10.0, news_subscore=0.0, executed=True) is True


@pytest.mark.asyncio
async def test_shortlist_gate_suppresses_unchanged_content():
    sym = _uniq("RELIANCE")
    assert await shortlist_gate(sym, score=50.0, news_subscore=5.0, executed=False) is True
    # Same score/news, called again immediately -- should suppress (within anti-spam floor).
    assert await shortlist_gate(sym, score=50.0, news_subscore=5.0, executed=False) is False


@pytest.mark.asyncio
async def test_shortlist_gate_alerts_on_score_delta():
    """The anti-spam floor is checked before the score-delta comparison (in
    both the original agent_loop.py and india_tasks.py implementations this
    consolidates) -- a big score move within the floor is still suppressed.
    Passing min_interval_sec=0 here isolates the score-delta logic itself
    from that floor, rather than waiting 30 real minutes."""
    sym = _uniq("HDFCBANK")
    assert await shortlist_gate(sym, score=20.0, news_subscore=0.0, executed=False, min_interval_sec=0) is True
    assert await shortlist_gate(sym, score=26.0, news_subscore=0.0, executed=False, min_interval_sec=0) is True


@pytest.mark.asyncio
async def test_shortlist_would_alert_does_not_mutate_state():
    """The read-only peek must not write state -- otherwise a call site that
    peeks before doing expensive research, then calls publish() (which
    mutates via shortlist_gate), would see its own peek as prior state."""
    sym = _uniq("PEEK")
    first = await shortlist_would_alert(sym, score=30.0, news_subscore=0.0, executed=False)
    assert first is True
    second = await shortlist_would_alert(sym, score=30.0, news_subscore=0.0, executed=False)
    assert second is True  # unchanged by the peek -- no state was written
