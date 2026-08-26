"""Transaction cost must depend on the product. It did not.

Until 2026-08-26 `estimate_trade_cost` had no `product` argument, its docstring
said "delivery", and it charged delivery STT — 0.1% on BOTH legs — to every
trade including intraday. NSE equity intraday STT is 0.025% on the SELL leg
only.

Measured on the 72 closed trades on record: MIS and CNC were both charged a
median 0.294% round trip, when their real costs differ by roughly 3x. Across 44
MIS trades that is ~Rs 3,787 of cost a broker would not have charged, against
Rs 806 of total recorded P&L — the overcharge was 4.7x the entire book.

The same defect existed in a duplicate copy in engine/agent/backtester.py, which
would have made the backtester and the live simulator disagree about the cost of
an identical trade.
"""
from __future__ import annotations

import inspect

import pytest

from paper_trading.trade_simulator import estimate_trade_cost
from engine.agent.backtester import estimate_trade_cost as bt_cost


def _round_trip(fn, qty, price, product):
    """Buy then sell — the full round trip a closed long pays."""
    return fn(qty, price, "BUY", product) + fn(qty, price, "SELL", product)


class TestProductChangesTheCost:
    def test_mis_and_cnc_differ_for_identical_notional(self):
        qty, px = 44, 1136.30          # SHRIRAMFIN.NS, a real trade on record
        mis = _round_trip(estimate_trade_cost, qty, px, "MIS")
        cnc = _round_trip(estimate_trade_cost, qty, px, "CNC")
        assert mis < cnc, "intraday must be cheaper than delivery"
        assert cnc - mis > 50, (
            f"the difference is only Rs {cnc - mis:.2f}; delivery STT is 4x the "
            f"intraday rate and applies to both legs, so it should be large"
        )

    def test_zero_move_mis_round_trip_is_near_the_intraday_rate(self):
        """A trade that closes at its entry price pays only friction."""
        qty, px = 44, 1136.30
        notional = qty * px
        pct = 100 * _round_trip(estimate_trade_cost, qty, px, "MIS") / notional
        assert 0.08 <= pct <= 0.16, (
            f"intraday round trip came to {pct:.3f}%, outside the expected band"
        )

    def test_zero_move_cnc_round_trip_matches_the_previous_delivery_cost(self):
        """The delivery path must be unchanged — this is the regression guard.

        0.294% is what production actually charged, reproduced from three trades
        whose exit price equalled their entry price exactly (SHRIRAMFIN,
        WELCORP, INDOMIM — all -Rs 146 to -Rs 147 on ~Rs 50,000).
        """
        qty, px = 44, 1136.30
        notional = qty * px
        pct = 100 * _round_trip(estimate_trade_cost, qty, px, "CNC") / notional
        assert 0.28 <= pct <= 0.31, (
            f"delivery round trip is now {pct:.3f}%, but production charged "
            f"0.294% and that behaviour must be preserved"
        )


class TestDefaultPreservesOldBehaviour:
    def test_product_argument_is_optional(self):
        sig = inspect.signature(estimate_trade_cost)
        assert "product" in sig.parameters
        assert sig.parameters["product"].default == "CNC", (
            "the default must be delivery so an un-updated caller keeps exactly "
            "its previous behaviour"
        )

    def test_omitting_product_equals_asking_for_cnc(self):
        qty, px = 100, 500.0
        assert estimate_trade_cost(qty, px, "BUY") == estimate_trade_cost(qty, px, "BUY", "CNC")
        assert estimate_trade_cost(qty, px, "SELL") == estimate_trade_cost(qty, px, "SELL", "CNC")

    def test_unknown_product_falls_back_to_delivery(self):
        """Fail expensive, not cheap: an unrecognised product must not underpay."""
        qty, px = 100, 500.0
        for junk in ("", None, "NRML", "whatever"):
            assert estimate_trade_cost(qty, px, "BUY", junk) == \
                   estimate_trade_cost(qty, px, "BUY", "CNC")


class TestIntradaySttTreatment:
    def test_mis_charges_stt_on_the_sell_leg_only(self):
        qty, px = 100, 500.0
        buy = estimate_trade_cost(qty, px, "BUY", "MIS")
        sell = estimate_trade_cost(qty, px, "SELL", "MIS")
        assert sell > buy, "intraday STT applies to the sell leg, so it must cost more"

    def test_cnc_charges_stt_on_both_legs(self):
        qty, px = 100, 500.0
        notional = qty * px
        buy = estimate_trade_cost(qty, px, "BUY", "CNC")
        sell = estimate_trade_cost(qty, px, "SELL", "CNC")
        # Both legs carry 0.1% STT; the buy leg additionally carries stamp duty.
        assert buy > notional * 0.001
        assert sell > notional * 0.001


class TestBacktesterCopyAgrees:
    """The duplicate must not drift from the simulator."""

    @pytest.mark.parametrize("product", ["MIS", "CNC"])
    @pytest.mark.parametrize("side", ["BUY", "SELL"])
    def test_identical_output_for_identical_input(self, product, side):
        for qty, px in ((44, 1136.30), (100, 500.0), (7, 11750.30)):
            assert estimate_trade_cost(qty, px, side, product) == \
                   bt_cost(qty, px, side, product), (
                f"backtester disagrees with the simulator on {qty}@{px} "
                f"{side}/{product}"
            )


class TestCallSitesPassTheProduct:
    def test_close_paths_read_trade_product(self):
        """The correction is inert unless the call sites actually pass it."""
        import paper_trading.trade_simulator as ts

        src = inspect.getsource(ts)
        calls = [
            ln for ln in src.splitlines()
            if "estimate_trade_cost(" in ln and "def estimate_trade_cost" not in ln
        ]
        assert calls, "no call sites found"
        for ln in calls:
            assert "_prod" in ln, (
                f"call site does not pass a product, so it silently keeps the "
                f"delivery default: {ln.strip()}"
            )

    def test_product_falls_back_when_the_trade_has_none(self):
        """Historical rows may have product NULL; they must not crash or underpay."""
        import paper_trading.trade_simulator as ts

        src = inspect.getsource(ts)
        assert 'getattr(trade, "product", None) or "CNC"' in src, (
            "the call sites must default a missing product to CNC"
        )
