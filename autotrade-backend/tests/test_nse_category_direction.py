"""The exchange's own announcement category decides direction, not the LLM.

Measured over 4,309 NSE announcements (Phase 3 study): for the SAME category,
NSE's label gives ORDER_WIN a +1.053% mean excess return and a 65.5% win rate,
while our classifier's label gives it -0.245% and 37.3%. The model was replacing
a good label with a bad one, so for NSE-sourced items the category now wins.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from engine.event_classifier import classify_event, resolve_nse_direction

pytestmark = pytest.mark.asyncio


# ── the pure resolver ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "category,text,expect_dir,expect_cat",
    [
        ("Bagging/Receiving of orders/contracts", "", "LONG", "ORDER_WIN"),
        ("Awarding of order(s)/contract(s)", "", "LONG", "ORDER_WIN"),
        ("Acquisition", "", "LONG", "ACQUISITION"),
        ("Buyback", "", "LONG", "BUYBACK"),
        ("Resignation of Director/KMP/SMP", "", "SHORT", "MANAGEMENT_RESIGNATION"),
        ("Resignation of Statutory Auditor", "", "SHORT", "AUDITOR_RESIGNATION"),
        ("Outcome of Board Meeting", "", "NEUTRAL", "ROUTINE_DISCLOSURE"),
        ("Press Release", "", "NEUTRAL", "ROUTINE_DISCLOSURE"),
        ("Dividend", "", "NEUTRAL", "ROUTINE_DISCLOSURE"),
    ],
)
async def test_category_maps_to_the_measured_direction(category, text, expect_dir, expect_cat):
    assert resolve_nse_direction(category, text) == (expect_dir, expect_cat)


@pytest.mark.parametrize(
    "text,expect",
    [
        ("CRISIL has upgraded the long-term rating", "LONG"),
        ("rating revised upward to AA-", "LONG"),
        ("ICRA downgraded the instrument to BBB", "SHORT"),
        ("outlook revised downward, negative outlook assigned", "SHORT"),
        ("Rating reaffirmed at A+ with no change", "NEUTRAL"),
    ],
)
async def test_credit_rating_direction_comes_from_the_text(text, expect):
    """'Credit Rating' alone does not say which way — only the text does.

    Reading direction off the bare category would make every rating action
    bullish, including the downgrades.
    """
    got = resolve_nse_direction("Credit Rating", text)
    assert got is not None and got[0] == expect


async def test_unknown_category_yields_no_opinion():
    """Outside the table we must return None, not guess.

    None means "the exchange gave us nothing here", which tells the caller to
    keep the LLM's answer. Returning NEUTRAL instead would silently suppress
    every event from a category we simply have not mapped yet.
    """
    assert resolve_nse_direction("Some Category We Have Never Seen", "") is None
    assert resolve_nse_direction(None, "") is None
    assert resolve_nse_direction("", "") is None


# ── the override inside classify_event ───────────────────────────────────────

def _llm_reply(bullish: bool, category: str = "SOMETHING_ELSE") -> str:
    return json.dumps({
        "category": category, "impact": "HIGH", "confidence": 0.9,
        "bullish": bullish,
        "entities": {"companies": ["ACME"], "sectors": ["Infra"], "countries": ["India"]},
        "surprise_score": 70, "expected_half_life_hours": 24,
    })


async def test_nse_category_overrides_a_wrong_llm_direction():
    """This is the defect the change exists to fix.

    The LLM calls an order win BEARISH; NSE filed it under
    'Bagging/Receiving of orders/contracts'. The exchange wins.
    """
    with patch("engine.event_classifier.call_llm_chat",
               new=AsyncMock(return_value=_llm_reply(bullish=False))):
        cls = await classify_event(
            "ACME bags Rs 500 cr order", "",
            nse_category="Bagging/Receiving of orders/contracts",
            source="NSE-Announcements",
        )
    assert cls is not None
    assert cls.bullish is True, "NSE's ORDER_WIN category must force BULLISH"
    assert cls.category == "ORDER_WIN"
    assert cls.source_reliability == 1.0


async def test_nse_category_overrides_a_wrong_llm_direction_the_other_way():
    with patch("engine.event_classifier.call_llm_chat",
               new=AsyncMock(return_value=_llm_reply(bullish=True))):
        cls = await classify_event(
            "ACME CFO steps down", "", nse_category="Resignation of Director/KMP/SMP", source="NSE-Announcements",
        )
    assert cls is not None
    assert cls.bullish is False, "NSE's resignation category must force BEARISH"
    assert cls.category == "MANAGEMENT_RESIGNATION"


async def test_neutral_category_emits_no_event_however_confident_the_llm_is():
    """'Outcome of Board Meeting' is NSE's results-declaration category.

    It says results were declared, not whether they beat. Measured mean excess
    -0.737% with a 36.3% win rate over 1,169 observations — acting on it loses
    money. NO EVENT -> NO TRADE is the correct outcome, so classify_event must
    return None even though the model was sure.
    """
    with patch("engine.event_classifier.call_llm_chat",
               new=AsyncMock(return_value=_llm_reply(bullish=True))):
        cls = await classify_event(
            "ACME: Outcome of Board Meeting — Q1 results", "",
            nse_category="Outcome of Board Meeting", source="NSE-Announcements",
        )
    assert cls is None


async def test_non_nse_sources_are_untouched():
    """Every other source must behave exactly as before.

    Without this, the change would silently rewrite Reuters/RSS classification
    too, and the measurement behind it only covers NSE filings.
    """
    with patch("engine.event_classifier.call_llm_chat",
               new=AsyncMock(return_value=_llm_reply(bullish=True, category="RUMOR"))):
        cls = await classify_event("Some Reuters headline", "", nse_category=None, source="Reuters")
    assert cls is not None
    assert cls.bullish is True
    assert cls.category == "RUMOR", "the LLM's category must survive when NSE has no opinion"


async def test_sentiment_veto_cannot_overturn_the_exchange():
    """The FinBERT contradiction guard must not fire on an NSE-decided direction.

    That guard exists to catch a model whose direction nothing corroborates.
    NSE's filing category is not the model's opinion, so letting a sentiment
    score veto it would reintroduce exactly the label we measured as worse.
    """
    with patch("engine.event_classifier.call_llm_chat",
               new=AsyncMock(return_value=_llm_reply(bullish=True))):
        cls = await classify_event(
            "ACME bags Rs 500 cr order", "",
            sentiment_score=-0.96,                       # strongly bearish text
            nse_category="Bagging/Receiving of orders/contracts",
            source="NSE-Announcements",
        )
    assert cls is not None, "an NSE-decided direction must survive the sentiment guard"
    assert cls.bullish is True


async def test_a_non_exchange_source_cannot_use_the_exchange_table():
    """The source gate, not just the category string.

    An RSS feed's own `category` field could collide with an NSE label. Gating
    on source means such a collision cannot flip a direction. Without this the
    caller could hand any feed's category to a table built for filings and the
    unit tests would still pass.
    """
    with patch("engine.event_classifier.call_llm_chat",
               new=AsyncMock(return_value=_llm_reply(bullish=False, category="RUMOR"))):
        cls = await classify_event(
            "Some blog post about an order win", "",
            nse_category="Bagging/Receiving of orders/contracts",   # collides on purpose
            source="Economic Times",                                # but not an exchange
        )
    assert cls is not None
    assert cls.bullish is False, "a non-exchange source must not reach the NSE table"
    assert cls.category == "RUMOR"


async def test_neutral_suppression_also_requires_an_exchange_source():
    with patch("engine.event_classifier.call_llm_chat",
               new=AsyncMock(return_value=_llm_reply(bullish=True))):
        cls = await classify_event(
            "Blog: board met yesterday", "",
            nse_category="Outcome of Board Meeting", source="mint - markets",
        )
    assert cls is not None, "a blog must not be suppressed by NSE's neutral list"


def test_the_crawler_call_site_still_passes_both_arguments():
    """Guards a silent regression the unit tests above cannot see.

    Every test in this file calls classify_event directly. If the production
    call site in news_crawler.py stopped passing `source=` or `nse_category=`,
    the override would quietly stop applying and all of them would still pass —
    the exchange's label would go back to being ignored with nothing failing.

    An AST check rather than a substring search, so a mention inside a comment
    or docstring cannot satisfy it.
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "crawler" / "news_crawler.py"
    tree = ast.parse(src.read_text())

    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name) and n.func.id == "classify_event"
    ]
    assert calls, "no classify_event() call found in news_crawler.py"

    for call in calls:
        kw = {k.arg for k in call.keywords}
        assert "nse_category" in kw, (
            "news_crawler.py calls classify_event() without nse_category= — "
            "NSE's own category would be ignored"
        )
        assert "source" in kw, (
            "news_crawler.py calls classify_event() without source= — the "
            "exchange-source gate cannot fire, so the override never applies"
        )


# ── the eligibility gate that decides whether a filing is classified at all ──

def _eligibility_source():
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "crawler" / "news_crawler.py"
    return src.read_text()


def test_filings_do_not_compete_on_sentiment():
    """Without this the whole override is inert.

    |FinBERT| > 0.6 is a threshold for news prose. NSE files in dry legalese
    and scores a MEDIAN |sentiment| of 0.000; only 8.7% clear the bar, and
    those lose the top-15 cap to wire copy that medians at 0.911. Measured:
    2,100 NSE announcements in 14 days, ZERO classified.

    The gate must admit a filing on the strength of its exchange category, not
    its emotional register. Asserted on the AST so a comment cannot satisfy it.
    """
    import ast
    tree = ast.parse(_eligibility_source())
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "_filing_with_direction"),
        None,
    )
    assert fn is not None, (
        "_filing_with_direction() is gone — exchange filings are back to "
        "competing with wire copy on FinBERT score, which classified 0 of 2,100"
    )
    body = ast.dump(fn)
    assert "resolve_nse_direction" in body, (
        "the filing gate must key on the exchange category, not on sentiment"
    )


def test_neutral_filings_are_not_sent_to_the_llm():
    """Spending a round-trip on something we will suppress is pure waste.

    80.1% of NSE announcements resolve to NEUTRAL and would be discarded by
    resolve_nse_direction downstream. Admitting them here would burn the cap
    on events that can never trade.
    """
    import ast
    tree = ast.parse(_eligibility_source())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_filing_with_direction")
    consts = {c.value for c in ast.walk(fn) if isinstance(c, ast.Constant)}
    assert "LONG" in consts and "SHORT" in consts, (
        "the filing gate must admit only directional categories"
    )
    assert "NEUTRAL" not in consts, (
        "NEUTRAL categories must not be admitted — they are suppressed later, "
        "so classifying them wastes the cap"
    )
