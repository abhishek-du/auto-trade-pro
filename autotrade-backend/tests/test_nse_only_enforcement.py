"""ACTIVE TRADING SYSTEM = NSE ONLY. Proven, not asserted.

THE INVARIANT: no BSE instrument may enter an ACTIVE path -- price request,
candidate, signal, order or position. HISTORICAL and audit rows are untouched;
5 closed .BO paper_trades and 7 .BO tactical_signals remain queryable, and
deleting them would rewrite forensic history.

WHY IT WAS NEEDED. BSE was purged from kite_instruments and hub_universe on
2026-08-27 and NSE_ONLY_UNIVERSE=True was added. Neither held:

  * crawler/india_price_feed.py forced 45 hardcoded .BO symbols into every
    crawl as "mandatory" -- 4,659 fresh .BO candle rows in the two days AFTER
    the purge.
  * tasks/market_scanner.py's fallback scanned '%.BO' candles into
    market_shortlist, which india_price_feed then crawls: a feedback loop.
  * A "full-bse-candles-daily" beat entry refreshed every BSE EQ symbol.
  * 474 .BO rows sat in premarket_news_queue, 109 inside the drain window.
  * decision_router, risk_manager and execution ALL merely stripped the suffix.
    None rejected on exchange, and 5 .BO trades had already executed.

SCOPE BOUNDARY. This is EXCHANGE safety. It deliberately does NOT decide
whether an NSE symbol is a tradeable equity -- hub_universe still admits InvITs
(-IV), rights entitlements (-RR), SME (-SM) and trade-to-trade (-BE/-BZ/-ST).
That is the Step 2B master-equity redesign. A -RR symbol passes this gate, and
a test below pins that boundary so it is a documented decision, not an oversight.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import textwrap

import pytest

from utils.symbols import (ExchangeRejection, filter_nse_only, is_nse_tradeable,
                           nse_gate)

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def _code(fn) -> str:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = n.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                n.body = b[1:] or [ast.Pass()]
    return ast.unparse(tree)


# ── 6/7/8: symbol forms ─────────────────────────────────────────────────────
class TestGateSymbolForms:
    @pytest.mark.parametrize("sym,reason", [
        ("RELIANCE.BO", ExchangeRejection.BSE_SYMBOL),
        ("reliance.bo", ExchangeRejection.BSE_SYMBOL),
        ("BSE:RELIANCE", ExchangeRejection.BSE_SYMBOL),
        ("VIPULORG.BO", ExchangeRejection.BSE_SYMBOL),
        ("RELIANCE", ExchangeRejection.UNRESOLVED_BARE_SYMBOL),
        ("X.BS", ExchangeRejection.UNKNOWN_EXCHANGE),
        ("X.NSE", ExchangeRejection.UNKNOWN_EXCHANGE),
        ("^NSEI", ExchangeRejection.NON_EQUITY_INSTRUMENT),
        ("^BSESN", ExchangeRejection.NON_EQUITY_INSTRUMENT),
        ("USDINR=X", ExchangeRejection.NON_EQUITY_INSTRUMENT),
        ("", ExchangeRejection.EMPTY_SYMBOL),
        (None, ExchangeRejection.EMPTY_SYMBOL),
        ("   ", ExchangeRejection.EMPTY_SYMBOL),
    ])
    def test_rejected_with_the_right_reason(self, sym, reason):
        ok, why = nse_gate(sym)
        assert ok is False
        assert why == reason

    @pytest.mark.parametrize("sym", ["RELIANCE.NS", "reliance.ns", "NSE:RELIANCE",
                                     "GKENERGY.NS.NS", "  TCS.NS  "])
    def test_nse_forms_allowed(self, sym):
        assert nse_gate(sym) == (True, None)

    def test_bare_symbol_is_fail_closed_by_default(self):
        """'RELIANCE' does not say which exchange it means."""
        assert not is_nse_tradeable("RELIANCE")

    def test_bare_allowed_only_when_caller_already_resolved(self):
        assert is_nse_tradeable("RELIANCE", allow_bare=True)


# ── 20: mixed input ─────────────────────────────────────────────────────────
class TestMixedInput:
    def test_only_nse_survives_filtering(self):
        kept, rejected = filter_nse_only(
            ["TCS.NS", "RELIANCE.BO", "INFY.NS", "^NSEI", "BARE", "X.BS"])
        assert kept == ["TCS.NS", "INFY.NS"]
        assert rejected == {
            ExchangeRejection.BSE_SYMBOL: 1,
            ExchangeRejection.NON_EQUITY_INSTRUMENT: 1,
            ExchangeRejection.UNRESOLVED_BARE_SYMBOL: 1,
            ExchangeRejection.UNKNOWN_EXCHANGE: 1,
        }

    def test_counters_not_per_symbol_logs(self):
        """The price feed runs 30-secondly over ~1,500 symbols."""
        _, rejected = filter_nse_only(["A.BO"] * 500)
        assert rejected == {ExchangeRejection.BSE_SYMBOL: 500}

    def test_empty_input_is_safe(self):
        assert filter_nse_only([]) == ([], {})
        assert filter_nse_only(None) == ([], {})


# ── 15/19: the price-feed configuration path ────────────────────────────────
class TestPriceFeedNoLongerCrawlsBSE:
    def _src(self):
        return (BACKEND / "crawler" / "india_price_feed.py").read_text()

    def test_bse_watchlists_are_not_in_the_mandatory_list(self):
        src = self._src()
        i = src.index("mandatory: list[str] =")
        line = src[i:i + 200]
        assert "bse_symbols" not in line
        assert "bse_mid_symbols" not in line

    def test_indices_are_still_crawled(self):
        """market_regime.py and intelligence_hub read them."""
        src = self._src()
        i = src.index("mandatory: list[str] =")
        assert "WATCHLIST_NIFTY_INDICES" in src[i:i + 200]

    def test_bootstrap_branch_builds_no_bo(self):
        src = self._src()
        i = src.index("equity_syms = [f\"{r.tradingsymbol}.NS\"")
        assert '.BO"' not in src[i:i + 300]

    def test_a_final_gate_filters_the_assembled_list(self):
        src = self._src()
        assert "filter_nse_only" in src
        i = src.index("all_symbols: list[str]")
        assert "_equity_ok" in src[i:i + 120]

    def test_the_config_properties_still_exist_but_are_unused_by_the_feed(self):
        """WATCHLIST_BSE_* is left in config -- removing it is a wider change
        and other (non-active) code may reference it. What matters is that the
        ACTIVE feed no longer consumes it."""
        from utils.config import settings
        assert isinstance(settings.bse_symbols, list)
        assert "bse_symbols" not in _code_of_feed()


def _code_of_feed() -> str:
    src = (BACKEND / "crawler" / "india_price_feed.py").read_text()
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))


# ── 14/19: the scanner feedback loop ────────────────────────────────────────
class TestScannerFallbackIsNseOnly:
    def test_fallback_query_excludes_bo(self):
        src = (BACKEND / "tasks" / "market_scanner.py").read_text()
        i = src.index("SELECT DISTINCT symbol FROM candles")
        stmt = src[i:i + 160]
        assert "%.NS" in stmt
        assert "%.BO" not in stmt, (
            "the scanner writes market_shortlist, which india_price_feed then "
            "crawls -- including .BO here is a feedback loop"
        )

    def test_the_bse_candle_beat_entry_is_disabled(self):
        from tasks.celery_app import celery_app
        names = " ".join(celery_app.conf.beat_schedule.keys()).lower()
        assert "bse" not in names
        tasks = " ".join(v["task"] for v in celery_app.conf.beat_schedule.values())
        assert "refresh_full_bse_candles" not in tasks


# ── 2: premarket ────────────────────────────────────────────────────────────
class TestPremarketDrainGate:
    def _cycle(self):
        import news_discovery_engine as nde
        return _code(nde._news_discovery_cycles)

    def test_drain_gates_each_item(self):
        src = self._cycle()
        assert "_resolve_nse_tradeable" in src

    def test_gate_runs_before_process_ticker(self):
        src = self._cycle()
        i_gate = src.index("_resolve_nse_tradeable")
        i_proc = src.index("process_ticker(symbol")
        assert i_gate < i_proc

    def test_rejections_are_counted_not_logged_per_item(self):
        src = self._cycle()
        assert "_pm_rejected" in src

    def test_no_queue_row_is_deleted(self):
        src = self._cycle()
        for verb in ("delete(PreMarketNewsQueue", "DELETE FROM premarket"):
            assert verb not in src, "historical queue rows must survive"


# ── 1/3: every news candidate ───────────────────────────────────────────────
class TestProcessTickerGate:
    def _src(self):
        import news_discovery_engine as nde
        return _code(nde.process_ticker)

    def test_gate_is_the_first_thing_process_ticker_does(self):
        src = self._src()
        i_gate = src.index("_resolve_nse_tradeable")
        i_llm = src.index("Multi-Agent LLM Debate")
        assert i_gate < i_llm, "the gate must precede any LLM spend"

    def test_rejection_returns_false_not_raises(self):
        src = self._src()
        i = src.index("_resolve_nse_tradeable")
        assert "return False" in src[i:i + 400]

    def test_rejections_are_aggregated(self):
        import news_discovery_engine as nde
        assert isinstance(nde.nse_gate_reject_counts(), dict)

    def test_it_covers_all_four_news_entry_points(self):
        """RSS, announcement consumer, premarket drain and anomaly catalyst all
        funnel through process_ticker."""
        src = (BACKEND / "news_discovery_engine.py").read_text()
        assert src.count("await process_ticker(") >= 3


# ── 5/16/17/18: execution backstop ──────────────────────────────────────────
class TestExecutionBackstop:
    def _src(self):
        import engine.decision_router as dr
        return _code(dr.authorize_trade_intent)

    def test_the_gate_exists_in_authorize_trade_intent(self):
        assert "_nse_gate(intent.symbol" in self._src()

    def test_it_runs_before_risk_validation(self):
        src = self._src()
        i_gate = src.index("_nse_gate(intent.symbol")
        i_risk = src.index("validate_signal")
        assert i_gate < i_risk

    def test_it_runs_before_market_hours(self):
        src = self._src()
        assert src.index("_nse_gate(intent.symbol") < src.index("is_nse_market_open")

    def test_bare_symbols_are_not_allowed_at_execution(self):
        src = self._src()
        i = src.index("_nse_gate(intent.symbol")
        assert "allow_bare=False" in src[i:i + 80]

    def test_a_dedicated_rejection_outcome_exists(self):
        from engine.decision_router import RoutingOutcome
        assert RoutingOutcome.BLOCKED_NON_NSE.value == "BLOCKED_NON_NSE_EXCHANGE"

    def test_the_rejection_is_audit_logged(self):
        src = self._src()
        i = src.index("BLOCKED_NON_NSE")
        assert "_log_intent_audit" in src[i:i + 600]


# ── 9/10: NSE keeps working ─────────────────────────────────────────────────
class TestNseStillWorks:
    @pytest.mark.parametrize("sym", ["RELIANCE.NS", "TCS.NS", "MAHABANK.NS",
                                     "BEL.NS", "GKENERGY.NS"])
    def test_valid_nse_passes(self, sym):
        assert nse_gate(sym) == (True, None)

    def test_a_name_resolved_to_nse_passes(self):
        """The identity resolver's output is always .NS-suffixed."""
        from utils.identity import IdentityIndex, resolve_identity
        ix = IdentityIndex()
        ix.add("MAHABANK", "BANK OF MAHARASHTRA")
        ix.finalise()
        r = resolve_identity("BANK OF MAHARASHTRA", ix)
        assert r.ok
        assert nse_gate(r.symbol) == (True, None)


# ── SCOPE BOUNDARY, documented deliberately ─────────────────────────────────
class TestScopeBoundary:
    @pytest.mark.parametrize("sym", ["EMBASSY-RR.NS", "SEITINVIT-IV.NS",
                                     "ALUWIND-SM.NS", "BIRLACABLE-BE.NS"])
    def test_non_equity_nse_series_still_pass_this_gate(self, sym):
        """DELIBERATE. This gate is EXCHANGE safety only. Excluding rights
        entitlements, InvITs, SME and trade-to-trade series is the Step 2B
        master-equity redesign. Pinned so the boundary is a decision on record,
        not an oversight."""
        assert nse_gate(sym) == (True, None)


# ── 13: idempotency ─────────────────────────────────────────────────────────
class TestIdempotency:
    def test_gate_is_pure(self):
        for _ in range(3):
            assert nse_gate("RELIANCE.BO") == (False, ExchangeRejection.BSE_SYMBOL)
            assert nse_gate("TCS.NS") == (True, None)

    def test_filter_does_not_mutate_input(self):
        src = ["A.NS", "B.BO"]
        filter_nse_only(src)
        assert src == ["A.NS", "B.BO"]


# ── 11/12: historical audit data must survive ───────────────────────────────
class TestHistoricalDataPreserved:
    """Deleting these would rewrite forensic history. The distinction this
    whole step rests on is ACTIVE vs HISTORICAL, not 'no .BO anywhere'."""

    @staticmethod
    async def _counts(table):
        """(.BO rows, total rows). A table that is EMPTY overall means this
        pytest context is not pointed at the production database -- the same
        condition that made build_index() return 0 symbols in Step 1A. Skip
        then, rather than reporting a false deletion alarm."""
        from sqlalchemy import text
        from db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as s:
            bo = (await s.execute(text(
                f"SELECT count(*) FROM {table} WHERE symbol LIKE '%.BO'"))).scalar()
            tot = (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
        return bo, tot

    @pytest.mark.asyncio
    @pytest.mark.parametrize("table,minimum", [
        ("paper_trades", 5),
        ("tactical_signals", 7),
        ("premarket_news_queue", 400),
    ])
    async def test_historical_bo_rows_remain_queryable(self, table, minimum):
        try:
            bo, tot = await self._counts(table)
        except Exception as exc:
            pytest.skip(f"database unavailable: {type(exc).__name__}")
        if not tot:
            pytest.skip(f"{table} is empty in this context -- not the production DB")
        assert bo >= minimum, (
            f"historical .BO rows in {table} were deleted (found {bo}, "
            f"expected >={minimum}); audit history must survive"
        )

    def test_no_delete_statement_was_added_to_the_changed_files(self):
        for rel in ("crawler/india_price_feed.py", "news_discovery_engine.py",
                    "tasks/market_scanner.py", "engine/decision_router.py"):
            body = (BACKEND / rel).read_text().lower()
            for verb in ("delete from paper_trades", "delete from tactical_signals",
                         "delete from premarket_news_queue"):
                assert verb not in body, f"{rel} deletes audit history"


class TestNoStrategyParameterChanged:
    """This step is exchange safety. Nothing else may move."""

    def test_thresholds_untouched(self):
        from utils.config import settings
        assert settings.TACTICAL_TOP_N == 15
        assert settings.V2_MIN_HOLD_MINUTES == 120
        assert settings.TRADING_STRATEGY_MODE == "V2"
        assert settings.NSE_ONLY_UNIVERSE is True
        assert settings.PAPER_MODE is True
