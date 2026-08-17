"""Regression tests for engine/event_classifier.py::classify_event()'s
observability fix (2026-07-23).

Root cause: classify_event() silently returned None whenever call_llm_chat()
returned falsy -- no logging at all. This is why a Bedrock circuit-breaker
cascade (utils/llm.py, triggered by a transient 500) that killed 45-50 news
candidates in a row was completely invisible in the logs: every one of them
just looked like an ordinary "no canonical event" classification miss, with
zero trace that anything was actually wrong upstream.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from engine.event_classifier import EventClassification, classify_event


class TestClassifyEventLogsOnFalsyResponse:
    @pytest.mark.asyncio
    async def test_none_response_is_logged_not_silent(self):
        with patch("engine.event_classifier.call_llm_chat", AsyncMock(return_value=None)), \
             patch("engine.event_classifier.logger") as mock_logger:
            result = await classify_event("Some headline", "some summary")
        assert result is None
        assert mock_logger.warning.called
        logged_text = str(mock_logger.warning.call_args)
        assert "no response" in logged_text.lower()

    @pytest.mark.asyncio
    async def test_empty_string_response_is_logged_not_silent(self):
        with patch("engine.event_classifier.call_llm_chat", AsyncMock(return_value="")), \
             patch("engine.event_classifier.logger") as mock_logger:
            result = await classify_event("Another headline", "another summary")
        assert result is None
        assert mock_logger.warning.called

    @pytest.mark.asyncio
    async def test_headline_is_included_in_the_log_message(self):
        with patch("engine.event_classifier.call_llm_chat", AsyncMock(return_value=None)), \
             patch("engine.event_classifier.logger") as mock_logger:
            await classify_event("XYZCORP wins large government order", "summary")
        logged_text = str(mock_logger.warning.call_args)
        assert "XYZCORP" in logged_text

    @pytest.mark.asyncio
    async def test_successful_response_does_not_log_a_warning(self):
        valid_json = '''{
            "category": "ORDER_WIN", "subcategories": [], "impact": "HIGH",
            "confidence": 0.9, "bullish": true, "time_horizon": "WEEKS",
            "expected_half_life_hours": 48, "entities": {"companies": [], "sectors": [], "countries": []},
            "reasoning": "test", "surprise_score": 80, "is_new_information": true,
            "market_priced_in": 0.1, "source_reliability": 0.9
        }'''
        with patch("engine.event_classifier.call_llm_chat", AsyncMock(return_value=valid_json)), \
             patch("engine.event_classifier.logger") as mock_logger:
            result = await classify_event("headline", "summary")
        assert result is not None
        assert result.category == "ORDER_WIN"
        mock_logger.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_malformed_json_still_logs_via_the_exception_path(self):
        # A response that isn't falsy but fails parsing goes through the
        # separate `except Exception` branch (pre-existing behavior,
        # unaffected by this fix) -- must still log, just with the original
        # "Failed to classify" message rather than the new "no response" one.
        with patch("engine.event_classifier.call_llm_chat", AsyncMock(return_value="not valid json {{{")), \
             patch("engine.event_classifier.logger") as mock_logger:
            result = await classify_event("headline", "summary")
        assert result is None
        assert mock_logger.error.called


class TestNullFieldsUseDefaults:
    """An explicit JSON `null` must behave exactly like an absent key
    (2026-08-17).

    A pydantic default only applies when the key is MISSING. When the model
    emitted the key with a null value the default was bypassed and validation
    failed -- observed live as `{"bullish": null}` -> "Input should be a valid
    boolean [input_value=None]". classify_event() classifies that as malformed
    JSON and retries the WHOLE LLM call up to 4 times before returning None, so
    a single null field burned four round-trips and still dropped a genuine
    catalyst ("no event, no trade").
    """

    def test_null_bullish_falls_back_to_default(self):
        c = EventClassification(**{"category": "MACRO_EVENT", "impact": "LOW",
                                   "confidence": 0.4, "bullish": None, "entities": {}})
        assert c.bullish is False

    def test_every_required_field_null_still_constructs(self):
        c = EventClassification(**{"category": None, "impact": None, "confidence": None,
                                   "bullish": None, "entities": None})
        assert (c.category, c.impact, c.confidence, c.bullish, c.entities) == \
               ("UNKNOWN", "LOW", 0.0, False, {})

    def test_null_optional_fields_fall_back_to_defaults(self):
        c = EventClassification(**{"category": "X", "bullish": True, "reasoning": None,
                                   "surprise_score": None, "source_reliability": None})
        assert c.reasoning == "" and c.surprise_score == 50 and c.source_reliability == 0.7

    def test_real_values_pass_through_untouched(self):
        """The null-stripping must not clobber legitimate falsy values or
        rewrite anything the model actually supplied."""
        c = EventClassification(**{"category": "EARNINGS_BEAT", "impact": "HIGH",
                                   "confidence": 0.9, "bullish": True,
                                   "entities": {"companies": ["TCS"]},
                                   "surprise_score": 88, "source_reliability": 1.0,
                                   "market_priced_in": 0.0})
        assert c.bullish is True and c.confidence == 0.9 and c.surprise_score == 88
        assert c.source_reliability == 1.0
        assert c.market_priced_in == 0.0          # legitimate falsy, not a null
        assert c.entities == {"companies": ["TCS"]}

    def test_genuinely_wrong_types_are_still_rejected(self):
        """This fix must not turn into blanket permissiveness -- a response
        that is actually wrong should still fail so the retry path can run."""
        with pytest.raises(Exception):
            EventClassification(**{"confidence": "not-a-number"})

    def test_non_dict_payload_is_passed_through(self):
        """The validator must not assume a dict; a list/str payload should
        reach pydantic's normal error handling rather than crash the validator."""
        with pytest.raises(Exception):
            EventClassification.model_validate(["not", "a", "dict"])
