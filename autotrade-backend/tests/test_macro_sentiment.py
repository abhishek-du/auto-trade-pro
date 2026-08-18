"""Tests for the Tier 1 MACRO scorer (2026-08-18).

The LLM is mocked throughout — these pin the contract around it (parsing,
sign enforcement, the confirmation pass, failure behaviour), not the model's
judgement. The model's judgement was checked separately against live calls on
real headlines, which is what produced the confirmation pass: scoring one
gold/silver headline six times gave NEUTRAL five times and once gave
BULLISH +0.80 at confidence 0.90 with fabricated reasoning.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from engine.macro_sentiment import (
    MIN_ACTIONABLE_CONFIDENCE, MacroSentiment, score_macro_headline,
)


def _resp(direction="BEARISH", score=-0.6, confidence=0.8, theme="OIL", **kw):
    return json.dumps({
        "direction": direction, "score": score, "confidence": confidence,
        "theme": theme, "sectors_hit": kw.get("sectors_hit", ["OIL_GAS"]),
        "reasoning": kw.get("reasoning", "test"),
    })


def _patch(*responses):
    """Patch the LLM to return the given responses in order.

    Uses `new_callable` rather than `new=` so the context manager yields the
    mock itself — with `new=`, `patch` yields the object unchanged and
    `m.call_count` silently reads an auto-created child mock instead of the
    real count, which makes call-count assertions pass or fail meaninglessly.
    """
    return patch("engine.macro_sentiment.call_llm_chat", new_callable=AsyncMock,
                 side_effect=list(responses))


class TestParsing:
    @pytest.mark.asyncio
    async def test_plain_json(self):
        with _patch(_resp()):
            r = await score_macro_headline("Oil spikes on Hormuz closure")
        assert r.direction == "BEARISH"
        assert r.score == pytest.approx(-0.6)

    @pytest.mark.asyncio
    async def test_markdown_fenced_json(self):
        """nemotron intermittently wraps output in a fence — the same slippage
        classify_event already retries around."""
        with _patch(f"```json\n{_resp()}\n```"):
            r = await score_macro_headline("Oil spikes")
        assert r is not None and r.direction == "BEARISH"

    @pytest.mark.asyncio
    async def test_json_embedded_in_prose(self):
        with _patch(f"Here is my analysis: {_resp()} Hope that helps."):
            r = await score_macro_headline("Oil spikes")
        assert r is not None and r.direction == "BEARISH"

    @pytest.mark.asyncio
    async def test_retries_once_on_garbage_then_succeeds(self):
        """Low confidence keeps this to the retry alone — an actionable result
        would add the confirmation call and confuse what is being measured."""
        with _patch("not json at all", _resp(confidence=0.2)) as m:
            r = await score_macro_headline("Oil spikes")
        assert r is not None
        assert m.call_count == 2

    @pytest.mark.asyncio
    async def test_gives_up_after_two_bad_responses(self):
        with _patch("garbage", "still garbage"):
            assert await score_macro_headline("Oil spikes") is None


class TestFailureIsSilentNotNeutral:
    """A failed macro read must be None, never a confident neutral. None
    degrades to the old macro-blind behaviour; a fabricated neutral would tell
    the regime engine 'no macro risk' during an actual crisis."""

    @pytest.mark.asyncio
    async def test_llm_unavailable_returns_none(self):
        with _patch(None, None):
            assert await score_macro_headline("Oil spikes") is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        with patch("engine.macro_sentiment.call_llm_chat",
                   new=AsyncMock(side_effect=RuntimeError("bedrock down"))):
            assert await score_macro_headline("Oil spikes") is None

    @pytest.mark.asyncio
    async def test_empty_headline_returns_none(self):
        assert await score_macro_headline("") is None
        assert await score_macro_headline("   ") is None


class TestSignEnforcement:
    """Downstream code keys off the sign, so a self-contradicting response
    must be corrected rather than trusted."""

    @pytest.mark.asyncio
    async def test_bearish_with_positive_score_is_flipped(self):
        with _patch(_resp(direction="BEARISH", score=0.7, confidence=0.4)):
            r = await score_macro_headline("Oil spikes")
        assert r.score == pytest.approx(-0.7)

    @pytest.mark.asyncio
    async def test_bullish_with_negative_score_is_flipped(self):
        with _patch(_resp(direction="BULLISH", score=-0.7, confidence=0.4)):
            r = await score_macro_headline("Rate cut delivered")
        assert r.score == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_neutral_forces_zero(self):
        with _patch(_resp(direction="NEUTRAL", score=-0.9, confidence=0.4)):
            r = await score_macro_headline("Rupee opens 5 paise lower")
        assert r.score == 0.0


class TestConfirmationPass:
    """The guard against the observed 1-in-6 fabrication."""

    @pytest.mark.asyncio
    async def test_neutral_reading_costs_only_one_call(self):
        """Macro news is mostly ambient; confirming every neutral would double
        the budget for nothing."""
        with _patch(_resp(direction="NEUTRAL", score=0.0, confidence=0.4)) as m:
            r = await score_macro_headline("Rupee opens 5 paise lower")
        assert m.call_count == 1
        assert r.is_actionable is False

    @pytest.mark.asyncio
    async def test_low_confidence_reading_costs_only_one_call(self):
        with _patch(_resp(direction="BEARISH", score=-0.5, confidence=0.2)) as m:
            await score_macro_headline("Minor oil wobble")
        assert m.call_count == 1

    @pytest.mark.asyncio
    async def test_actionable_reading_is_confirmed(self):
        with _patch(_resp(direction="BEARISH", score=-0.6, confidence=0.8),
                    _resp(direction="BEARISH", score=-0.5, confidence=0.7)) as m:
            r = await score_macro_headline("Hormuz closed")
        assert m.call_count == 2
        assert r.direction == "BEARISH"
        # Conservative on both axes: smaller magnitude, lower confidence.
        assert r.score == pytest.approx(-0.5)
        assert r.confidence == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_disagreement_downgrades_to_neutral(self):
        """The live failure, reproduced: one sample fabricates a confident
        BULLISH read on a headline the model otherwise calls NEUTRAL."""
        with _patch(_resp(direction="BULLISH", score=0.8, confidence=0.9,
                          reasoning="positive sentiment around AI and tech innovation"),
                    _resp(direction="NEUTRAL", score=0.0, confidence=0.4)):
            r = await score_macro_headline("Gold and silver prices rise on MCX")
        assert r.direction == "NEUTRAL"
        assert r.score == 0.0
        assert r.is_actionable is False

    @pytest.mark.asyncio
    async def test_unconfirmable_reading_loses_actionability_but_survives(self):
        """If the confirming call fails outright, the signal is weak evidence
        rather than zero — but it must not stay actionable."""
        with _patch(_resp(direction="BEARISH", score=-0.6, confidence=0.9),
                    None, None):
            r = await score_macro_headline("Hormuz closed")
        assert r is not None
        assert r.direction == "BEARISH"
        assert r.is_actionable is False
        assert r.confidence < MIN_ACTIONABLE_CONFIDENCE


class TestSchemaNormalisation:
    def test_unknown_direction_becomes_neutral(self):
        assert MacroSentiment(direction="VERY_BULLISH", score=0.5,
                              confidence=0.5).direction == "NEUTRAL"

    def test_unknown_theme_becomes_other(self):
        assert MacroSentiment(direction="BULLISH", score=0.5, confidence=0.5,
                              theme="CRYPTO_MOON").theme == "OTHER"

    def test_sectors_are_uppercased_and_capped(self):
        m = MacroSentiment(direction="BULLISH", score=0.5, confidence=0.5,
                           sectors_hit=[f"s{i}" for i in range(20)])
        assert len(m.sectors_hit) == 6
        assert all(s.isupper() for s in m.sectors_hit)

    def test_sectors_reject_non_list(self):
        assert MacroSentiment(direction="BULLISH", score=0.5, confidence=0.5,
                              sectors_hit="OIL_GAS").sectors_hit == []

    @pytest.mark.parametrize("score", [1.5, -1.5])
    def test_out_of_range_score_rejected(self, score):
        with pytest.raises(Exception):
            MacroSentiment(direction="BULLISH", score=score, confidence=0.5)

    def test_actionable_threshold(self):
        below = MacroSentiment(direction="BEARISH", score=-0.6,
                               confidence=MIN_ACTIONABLE_CONFIDENCE - 0.01)
        at    = MacroSentiment(direction="BEARISH", score=-0.6,
                               confidence=MIN_ACTIONABLE_CONFIDENCE)
        assert below.is_actionable is False
        assert at.is_actionable is True
