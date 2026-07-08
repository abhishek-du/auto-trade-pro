"""Tests for the shared high-impact news classifier (engine/news_impact.py)
and its wiring into the /news API item serializer.
"""
import types

from engine.news_impact import is_high_impact_news, matches_shock_keyword


class TestKeywordMatch:
    def test_geopolitical(self):
        assert matches_shock_keyword("Trump says Iran ceasefire is over")

    def test_market_panic(self):
        assert matches_shock_keyword("Sensex, Nifty tumble over 2% on oil spike")

    def test_routine(self):
        assert not matches_shock_keyword("Reliance Q1 profit rises 8% on retail growth")

    def test_none_safe(self):
        assert not matches_shock_keyword(None)


class TestHighImpact:
    def test_shock_plus_strong_negative_is_high(self):
        assert is_high_impact_news("US stock futures tumble as Trump says Iran deal is over",
                                   "negative", -0.91)

    def test_shock_but_weak_sentiment_is_not(self):
        # keyword present but |score| below the 0.6 balanced threshold
        assert not is_high_impact_news("Markets tumble slightly on mild profit-taking",
                                       "negative", -0.4)

    def test_shock_but_positive_is_not(self):
        assert not is_high_impact_news("Oil jumps 6% as Trump declares Iran ceasefire",
                                       "positive", 0.8)

    def test_strong_negative_but_no_keyword_is_not(self):
        assert not is_high_impact_news("Reliance Q1 profit misses estimates badly",
                                       "negative", -0.95)

    def test_missing_score_is_not(self):
        assert not is_high_impact_news("war escalates in the region", "negative", None)

    def test_threshold_boundary(self):
        # default threshold is the strict 0.75
        assert is_high_impact_news("Nifty crash deepens", "negative", -0.75)
        assert not is_high_impact_news("Nifty crash deepens", "negative", -0.74)

    def test_explicit_threshold_override(self):
        assert is_high_impact_news("Nifty crash deepens", "negative", -0.65, 0.6)
        assert not is_high_impact_news("Nifty crash deepens", "negative", -0.65, 0.75)


def test_api_item_out_sets_high_impact_flag():
    from api.news import _item_out
    import datetime as dt
    item = types.SimpleNamespace(
        id=1, headline="Sensex, Nifty tumble over 2% as Middle East tensions spook markets",
        source="Reuters", url=None, sentiment="negative", score=-0.86,
        tickers_affected=None, published_at=None, crawled_at=dt.datetime.utcnow(),
    )
    out = _item_out(item)
    assert out.high_impact is True

    routine = types.SimpleNamespace(
        id=2, headline="Penny stock rallies 8% for second straight session",
        source="Mint", url=None, sentiment="positive", score=0.86,
        tickers_affected=None, published_at=None, crawled_at=dt.datetime.utcnow(),
    )
    assert _item_out(routine).high_impact is False
