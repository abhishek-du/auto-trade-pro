"""Regression tests for API timestamp timezone handling (2026-08-17).

Two separate bugs, one symptom ("times are 5.5 hours off"):

1. WIRE FORMAT. Every timestamp in this DB is stored NAIVE UTC (62 columns
   default to func.now() on a UTC Postgres; app writers use utcnow()).
   Pydantic emitted them without an offset -- "2026-08-17T13:26:02.926" -- so a
   browser parsed them as LOCAL time and rendered UTC values as if they were
   IST. Everything in the UI read 5h30m early. Fixed by TAGGING the offset the
   values already carry, not by converting stored data.

2. NSE INGESTION. crawler.news_crawler._parse_nse_announcement_dt parsed NSE's
   IST wall-clock announcements as naive and stored them in a UTC column --
   4,183 rows (15.3% of all news_items with a published_at) sat 5h30m in the
   FUTURE relative to their own crawled_at. It was the only source doing this;
   every other one goes through utcfromtimestamp.

Why storage was NOT converted to IST: the app clock (datetime.utcnow()) is
compared against candle timestamps in the SL/TP freshness gates
(paper_trading/trade_simulator.py, engine/agent/execution.py). Moving either
side by 5.5h makes every candle look stale during market hours and silently
disables stop-loss monitoring. Storage stays uniformly UTC.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import pytest

from api.schemas import NewsItemOut, PaperTradeOut, _UtcAwareTimestamps
from crawler.news_crawler import _parse_nse_announcement_dt

IST = ZoneInfo("Asia/Kolkata")


class TestWireFormatCarriesOffset:
    def test_naive_datetime_is_tagged_utc(self):
        m = NewsItemOut(
            id=1, headline="h", source="s", url=None, sentiment="neutral",
            score=0.0, tickers_affected=None,
            published_at=datetime(2026, 8, 17, 9, 46, 11),
            crawled_at=datetime(2026, 8, 17, 13, 27, 59),
        )
        payload = json.loads(m.model_dump_json())
        assert payload["crawled_at"].endswith("+00:00")
        assert payload["published_at"].endswith("+00:00")

    def test_client_renders_the_correct_ist_wall_clock(self):
        """The whole point: 13:27 UTC must reach the browser as 18:57 IST."""
        m = NewsItemOut(
            id=1, headline="h", source="s", url=None, sentiment="neutral",
            score=0.0, tickers_affected=None, published_at=None,
            crawled_at=datetime(2026, 8, 17, 13, 27, 59),
        )
        wire = json.loads(m.model_dump_json())["crawled_at"]
        rendered = datetime.fromisoformat(wire).astimezone(IST)
        assert (rendered.hour, rendered.minute) == (18, 57)

    def test_optional_none_survives(self):
        m = PaperTradeOut(
            id=1, symbol="X", direction="BUY", status="OPEN", entry_price=1.0,
            exit_price=None, stop_loss=0.9, take_profit=1.2, size_units=1.0,
            size_usd=1.0, pnl=None, pnl_percent=None, ai_reason="",
            signal_confidence=0.0, pattern_name="", news_sentiment_score=0.0,
            slippage_applied=0.0, opened_at=datetime(2026, 8, 13, 4, 4, 8),
            closed_at=None,
        )
        payload = json.loads(m.model_dump_json())
        assert payload["closed_at"] is None
        assert payload["opened_at"].endswith("+00:00")

    def test_non_datetime_fields_untouched(self):
        """The serializer runs on '*', so it must pass everything else through
        unchanged -- including plain dates, which are NOT datetimes."""
        class _M(_UtcAwareTimestamps):
            ts: datetime
            d: date
            name: str
            n: float
            flag: bool
            items: list[str]
            blob: Optional[dict] = None

        payload = json.loads(_M(
            ts=datetime(2026, 8, 17, 1, 2, 3), d=date(2026, 8, 17), name="x",
            n=1.5, flag=True, items=["a"], blob={"k": 1},
        ).model_dump_json())
        assert payload["d"] == "2026-08-17"      # date, not shifted or tagged
        assert payload["name"] == "x"
        assert payload["n"] == 1.5
        assert payload["flag"] is True
        assert payload["items"] == ["a"]
        assert payload["blob"] == {"k": 1}

    def test_already_aware_datetime_is_not_double_tagged(self):
        class _M(_UtcAwareTimestamps):
            ts: datetime

        wire = json.loads(_M(ts=datetime(2026, 8, 17, 1, 2, 3, tzinfo=timezone.utc)
                             ).model_dump_json())["ts"]
        assert wire.count("+") <= 1 and not wire.endswith("+00:00+00:00")
        assert datetime.fromisoformat(wire).utcoffset().total_seconds() == 0

    def test_every_response_model_uses_the_mixin(self):
        """A new model added on plain BaseModel would silently reintroduce the
        naive-timestamp bug for its endpoint."""
        import api.schemas as sch
        from pydantic import BaseModel

        offenders = [
            name for name, obj in vars(sch).items()
            if isinstance(obj, type) and issubclass(obj, BaseModel)
            and obj not in (BaseModel, sch._UtcAwareTimestamps)
            and not issubclass(obj, sch._UtcAwareTimestamps)
            and any(_is_dt(f.annotation) for f in obj.model_fields.values())
        ]
        assert not offenders, (
            f"these response models carry datetime fields but bypass the UTC "
            f"serializer, so their timestamps go out naive: {offenders}")


def _is_dt(annotation) -> bool:
    import typing
    if annotation is datetime:
        return True
    return any(a is datetime for a in typing.get_args(annotation) or ())


class TestNseAnnouncementIngestion:
    def test_ist_input_becomes_utc(self):
        # NSE publishes IST wall-clock; 20:10:07 IST == 14:40:07 UTC
        assert _parse_nse_announcement_dt("14-Jul-2026 20:10:07") == \
               datetime(2026, 7, 14, 14, 40, 7)

    def test_early_morning_ist_rolls_back_a_day(self):
        """03:00 IST is the PREVIOUS day in UTC -- the case a naive parse gets
        most visibly wrong."""
        assert _parse_nse_announcement_dt("15-Jul-2026 03:00:00") == \
               datetime(2026, 7, 14, 21, 30, 0)

    def test_result_is_naive_to_match_the_column(self):
        assert _parse_nse_announcement_dt("14-Jul-2026 20:10:07").tzinfo is None

    @pytest.mark.parametrize("raw", [None, "", "garbage", "32-Xxx-2026 99:99:99"])
    def test_unparseable_input_is_none_not_a_crash(self, raw):
        assert _parse_nse_announcement_dt(raw) is None
