"""Regression — NSE live-analysis parsing.

Two independent failures, both silent-ish:

1. `_parse_variation_list` had a "try any list-valued key" fallback. NSE's
   variations response has no top-level "data" key, so the fallback matched
   **"legends"** — a list OF LISTS. Every row then hit `row.get(...)` and raised
   `'list' object has no attribute 'get'`, ~28x per 15 min in the live log,
   leaving gainers/losers permanently empty.

2. `_map_variation_row` used camelCase (`openPrice`, `previousPrice`,
   `tradedQuantity`) but this endpoint returns snake_case (`open_price`,
   `prev_price`, `trade_quantity`). Only symbol/ltp/perChange matched, so the
   other fields silently came back 0.0.

Plus: three URLs used retired `index=` values that return HTTP 200 with
{"data": "Missing index or key."} — an invisible failure.
"""
from __future__ import annotations

import pytest

from crawler import market_breadth as mb


# Real shape, captured live 2026-08-20.
NSE_VARIATIONS = {
    "legends": [["NIFTY", "NIFTY 50"], ["BANKNIFTY", "NIFTY BANK"]],
    "NIFTY": {"data": [{"symbol": "ETERNAL", "ltp": 327.35, "perChange": 2.3}], "timestamp": "x"},
    "allSec": {"data": [{
        "symbol": "SAMBANDAM", "series": "EQ", "open_price": 114, "high_price": 126,
        "low_price": 113.99, "ltp": 126, "prev_price": 105, "net_price": 20,
        "trade_quantity": 7512, "perChange": 20,
    }], "timestamp": "x"},
}


class TestParseVariationList:

    def test_does_not_return_the_legends_list_of_lists(self):
        """The exact crash: legends[0] is a list, so row.get() exploded."""
        rows = mb._parse_variation_list(NSE_VARIATIONS)
        assert rows, "no rows parsed"
        assert all(isinstance(r, dict) for r in rows), "returned non-dict rows (the bug)"

    def test_prefers_allsec_market_wide(self):
        rows = mb._parse_variation_list(NSE_VARIATIONS)
        assert rows[0]["symbol"] == "SAMBANDAM"

    def test_falls_back_to_a_section_when_allsec_absent(self):
        payload = {"legends": [["a", "b"]], "NIFTY": NSE_VARIATIONS["NIFTY"]}
        rows = mb._parse_variation_list(payload)
        assert rows and rows[0]["symbol"] == "ETERNAL"

    def test_flat_data_shape_still_works(self):
        assert mb._parse_variation_list({"data": [{"symbol": "X"}]})[0]["symbol"] == "X"

    def test_bare_list_of_dicts_still_works(self):
        assert mb._parse_variation_list([{"symbol": "X"}])[0]["symbol"] == "X"

    @pytest.mark.parametrize("payload", [
        {"data": "Missing index or key."},   # retired endpoint response
        {"legends": [["a", "b"]]},           # only list-of-lists present
        [], {}, None, "nonsense", [[1, 2]],
    ])
    def test_degrades_to_empty_never_raises(self, payload):
        assert mb._parse_variation_list(payload) == []


class TestMapVariationRow:

    def test_snake_case_fields_are_read(self):
        row = mb._map_variation_row(NSE_VARIATIONS["allSec"]["data"][0])
        assert row["open"] == 114.0
        assert row["high"] == 126.0
        assert row["low"] == 113.99
        assert row["prev_close"] == 105.0
        assert row["volume"] == 7512

    def test_change_is_computed_not_taken_from_net_price(self):
        """NSE's net_price carries the PERCENT change (20), not the absolute
        difference (126-105 = 21). Trusting it puts a % in a rupee field."""
        row = mb._map_variation_row(NSE_VARIATIONS["allSec"]["data"][0])
        assert row["change"] == 21.0
        assert row["change_pct"] == 20.0

    def test_most_active_camelcase_shape(self):
        row = mb._map_variation_row({
            "symbol": "BAJAJHIND", "lastPrice": 22.08, "previousClose": 20.38,
            "totalTradedVolume": 160115855, "pChange": 8.34, "open": 20.74,
        })
        assert row["ltp"] == 22.08 and row["prev_close"] == 20.38
        assert row["change"] == pytest.approx(1.70)
        assert row["volume"] == 160115855

    def test_52week_shape_including_nse_typo_companyname(self):
        row = mb._map_variation_row({
            "symbol": "ACCPL", "comapnyName": "Accretion Pharmaceuticals Limited",
            "ltp": 208.8, "prevClose": "205.5", "pChange": 1.6,
        })
        assert row["symbol"] == "ACCPL"
        assert row["name"] == "Accretion Pharmaceuticals Limited"
        assert row["change"] == pytest.approx(3.3)

    def test_missing_fields_do_not_raise(self):
        row = mb._map_variation_row({})
        assert row["symbol"] == "" and row["change"] == 0.0


class TestRetiredUrlsReplaced:
    def test_no_retired_index_params(self):
        for url in (mb._ACTIVE_URL, mb._52H_URL, mb._52L_URL):
            assert "index=active" not in url
            assert "new52weekhigh" not in url or "live-analysis-data" in url
            assert "new52weeklow" not in url or "live-analysis-data" in url
