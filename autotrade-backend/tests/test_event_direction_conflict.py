"""Direction cross-check — LLM vs FinBERT (2026-08-21).

THE INCIDENT
------------
Exactly two sugar headlines were ingested in the relevant window:
    "Sugar stocks fall over 7% as govt's duty-free import move sparks price woes"
    "Sugar stocks Balrampur Chini, Dhampur Sugar, others tumble up to 5% ..."
Both scored -0.96 on FinBERT. From those two headlines the pipeline produced
SEVEN consecutive BULLISH CausalEvents while the sector fell 3-7%, and the
tactical pipeline bought DHAMPURSUG into it.

Re-running classify_event on the identical headlines afterwards returned
bullish=False both times. So the model is NOT systematically wrong here — it is
NON-DETERMINISTIC, and one bad roll authorised a sector's worth of long signals.
A single unverified read deciding trade direction is the actual defect.
"""
from __future__ import annotations

import pytest

from engine.event_classifier import _direction_contradicts_sentiment as conflicts


class TestCatchesTheProductionFailure:

    def test_bullish_llm_against_strongly_negative_sentiment(self):
        """The exact shape that occurred: LLM bullish, FinBERT -0.96."""
        assert conflicts(True, -0.9615) is True

    def test_bearish_llm_against_strongly_positive_sentiment(self):
        """The mirror case must be caught too."""
        assert conflicts(False, 0.93) is True


class TestDoesNotFireOnAgreement:

    @pytest.mark.parametrize("bullish,score", [
        (False, -0.96),   # both bearish
        (True, 0.89),     # both bullish
    ])
    def test_agreement_is_not_a_conflict(self, bullish, score):
        assert conflicts(bullish, score) is False


class TestOnlyFiresWhenBothAreConfident:
    """A weak score says nothing. This must not veto ordinary events over
    sentiment noise — most headlines score near zero."""

    @pytest.mark.parametrize("score", [-0.20, 0.0, 0.15, -0.69])
    def test_weak_scores_never_conflict(self, score):
        assert conflicts(True, score) is False
        assert conflicts(False, score) is False

    def test_threshold_is_configurable_and_sane(self):
        from utils.config import settings

        assert 0.5 <= settings.EVENT_SENTIMENT_CONFLICT_MIN <= 0.95


class TestFailsOpen:
    """No score, or an unusable one, must never block classification."""

    @pytest.mark.parametrize("score", [None, "", "abc", float("nan")])
    def test_unusable_score_is_not_a_conflict(self, score):
        try:
            assert conflicts(True, score) is False
        except Exception as exc:                       # pragma: no cover
            pytest.fail(f"must not raise on {score!r}: {exc}")


class TestRefusesRatherThanFlips:

    def test_conflict_returns_none_not_an_inverted_event(self):
        """Under NO EVENT -> NO TRADE, no event is a safe outcome. Overriding
        the model with FinBERT would swap one unverified direction for another,
        and FinBERT is documented in this codebase as out-of-domain on some
        text. When two independent reads disagree confidently, the honest answer
        is that we do not know."""
        import inspect

        from engine import event_classifier

        src = inspect.getsource(event_classifier)
        i = src.index("_direction_contradicts_sentiment(_cls.bullish")
        seg = src[i:i + 700]
        assert "return None" in seg
        assert "not _cls.bullish" not in seg, "must not flip the direction"


class TestCallerPassesTheScore:

    def test_news_engine_supplies_a_sentiment_score(self):
        """The guard is inert if the caller never passes a score."""
        import inspect

        import news_discovery_engine

        src = inspect.getsource(news_discovery_engine)
        assert "sentiment_score=_sent_score" in src
        assert "analyse_batch([headline])" in src
