"""P0 (2026-08-20): the news trade side must come from the CLASSIFIER.

THE BUG
-------
`side` reaches process_ticker from a keyword heuristic that defaults to BUY:

    side = "SELL" if any(w in headline.lower()
                         for w in ['plunge','crash','loss','down']) else "BUY"

Three sites share that shape. Meanwhile `_build_evidence` produces a real LLM
classification (BULLISH/BEARISH), and `direct_news_strategy:198` fails CLOSED
when the two disagree.

Measured on the 2026-08-20 session: 106 of 428 Direct-News evaluations (24.8%)
died on that disagreement, and ZERO SELL trades ever reached the execution gate
— every bearish headline lacking one of those four words became a BUY and then
contradicted its own classification.
"""
from __future__ import annotations

import pytest

# The exact expressions from news_discovery_engine.py, kept in sync deliberately
# so a change to either list makes these tests speak up.
_MAIN_KW = ['plunge', 'crash', 'loss', 'down']
_ANNOUNCE_KW = ["resign", "downgrade", "default", "loss",
                "decline", "disqualif", "suspend"]


def keyword_side(headline: str, kws=_MAIN_KW) -> str:
    return "SELL" if any(w in headline.lower() for w in kws) else "BUY"


class TestTheHeuristicIsWrong:
    """Documents WHY the fix is needed, using real headline shapes."""

    @pytest.mark.parametrize("headline", [
        "Company reports 40% decline in Q1 profit",
        "Firm misses estimates as margins contract sharply",
        "Board approves demerger after weak quarter",
        "Auditor flags going-concern doubt",
    ])
    def test_bearish_headlines_default_to_buy(self, headline):
        """None of these contain plunge/crash/loss/down, so all become BUY —
        then contradict a BEARISH classification and get discarded."""
        assert keyword_side(headline) == "BUY"

    @pytest.mark.parametrize("headline", [
        "Q1 net loss narrowed sharply on cost control",
        "Company turns profitable as loss shrinks 80%",
    ])
    def test_bullish_headlines_can_become_sell(self, headline):
        """'loss' appears in an improving-results context — the word is present
        but the direction is the opposite."""
        assert keyword_side(headline) == "SELL"

    def test_announcement_list_has_the_same_flaw(self):
        assert keyword_side("Net loss narrowed to Rs 38 cr", _ANNOUNCE_KW) == "SELL"


class TestFixIsWired:

    def test_correction_exists_after_evidence_is_built(self):
        """Must sit AFTER _build_evidence — that is the first point where the
        true direction is known — and before the direct_news / LLM calls."""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "news_discovery_engine.py").read_text(
            encoding="utf-8")
        i_ev = src.index("cand.evidence, event_id = await _build_evidence")
        i_fix = src.index("NEWS_SIDE_FROM_CLASSIFIER")
        i_dn = src.index("await maybe_direct_trade(")
        # NOTE: llm_tooluse_candidate is also called from an unrelated function
        # earlier in the file, so search from the direct_news call onward.
        i_llm = src.index("await llm_tooluse_candidate(", i_dn)
        assert i_ev < i_fix < i_dn < i_llm, "side correction is in the wrong place"

    def test_it_repoints_cand_and_dec_too(self):
        """dec.action is what the LLM argues for and what the intent inherits.
        Correcting only the local `side` would leave the debate defending the
        wrong trade."""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "news_discovery_engine.py").read_text(
            encoding="utf-8")
        seg = src[src.index("NEWS_SIDE_FROM_CLASSIFIER"):][:1200]
        assert "cand.side" in seg and "dec.action" in seg

    def test_flag_defaults_on(self):
        from utils.config import Settings

        assert Settings.model_fields["NEWS_SIDE_FROM_CLASSIFIER"].default is True


class TestCorrectionLogic:
    """The mapping itself, isolated from the engine's I/O."""

    @staticmethod
    def correct(side: str, direction: str) -> str:
        d = (direction or "").upper()
        if d in ("BULLISH", "BEARISH"):
            return "BUY" if d == "BULLISH" else "SELL"
        return side

    @pytest.mark.parametrize("side,direction,expected", [
        ("BUY",  "BEARISH", "SELL"),   # the 77-case bug: bearish news defaulted to BUY
        ("SELL", "BULLISH", "BUY"),    # the 29-case bug: 'loss' in a bullish context
        ("BUY",  "BULLISH", "BUY"),    # already correct — must not churn
        ("SELL", "BEARISH", "SELL"),
    ])
    def test_direction_wins(self, side, direction, expected):
        assert self.correct(side, direction) == expected

    @pytest.mark.parametrize("direction", ["NEUTRAL", "", None, "UNKNOWN"])
    def test_unknown_direction_leaves_side_untouched(self, direction):
        """No direction to act on — do not invent one."""
        assert self.correct("BUY", direction) == "BUY"


class TestSellIsActuallySupportedDownstream:
    """The fix is pointless if a SELL cannot survive the rest of the path."""

    def test_shorts_are_routed_as_intraday(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "news_discovery_engine.py").read_text(
            encoding="utf-8")
        assert 'product = "MIS" if side == "SELL" else "CNC"' in src, (
            "NSE equity shorts are intraday-only; CNC would be rejected"
        )

    def test_risk_manager_sizes_shorts(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "engine" / "risk_manager.py").read_text(
            encoding="utf-8")
        assert '_is_short' in src

    def test_shorting_is_not_globally_disabled(self):
        from utils.config import settings

        assert settings.EQUITY_SHORT_ENABLED is True
