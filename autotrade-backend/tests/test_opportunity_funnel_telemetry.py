"""The candidate funnel must be reconstructable after the fact.

The 2026-08-26 opportunity audit could not answer two questions, and both
blocked the whole analysis:

  1. "Was symbol X scanned?"  ScanResult.scanned is incremented AFTER the two
     `continue`s in _collect(), so a symbol dropped for a missing price or
     missing candles was counted nowhere. Eight of that day's biggest movers
     sat inside the F1 universe and produced no signal, and there was no way
     to tell whether the rules declined them (a strategy question) or whether
     they never reached the rules (an engineering defect).

  2. "Was symbol X in the universe on date D?"  rebuild_hub_universe() issues
     delete(HubUniverse) and rewrites the table wholesale, so only today's
     universe is ever knowable.

These tests pin the telemetry that closes both. They assert structure and
arithmetic only — no network, no database.
"""
from __future__ import annotations

import ast
import inspect

import engine.hub_universe as hu
from engine.tactical_executor import ScanResult


class TestScanResultAccountsForEverySymbol:
    def test_has_the_drop_reason_counters(self):
        r = ScanResult(sub_pipeline="F1")
        for field in ("universe", "scanned", "no_price", "no_candles"):
            assert hasattr(r, field), f"ScanResult is missing {field}"
            assert getattr(r, field) == 0

    def test_counters_reach_the_logged_dict(self):
        """as_dict() is what gets logged and persisted — the counts must be in it."""
        d = ScanResult(sub_pipeline="F1").as_dict()
        for field in ("universe", "scanned", "no_price", "no_candles"):
            assert field in d, f"as_dict() drops {field}"

    def test_the_counts_reconcile(self):
        """universe = scanned + no_price + no_candles. That identity is the point."""
        r = ScanResult(sub_pipeline="F1")
        r.universe, r.scanned, r.no_price, r.no_candles = 1476, 1200, 176, 100
        assert r.scanned + r.no_price + r.no_candles == r.universe

    def test_collect_increments_a_counter_on_every_continue(self):
        """Every early `continue` before result.scanned must be accounted for.

        This is the actual defect: a bare `continue` loses the symbol silently.
        """
        from engine.tactical_executor import TacticalExecutor

        src = inspect.getsource(TacticalExecutor._collect)
        tree = ast.parse(src.lstrip())

        loop = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.For) and ast.unparse(n.target) == "symbol"),
            None,
        )
        assert loop is not None, "the per-symbol loop was not found"

        # Walk the loop body: any `continue` reached before result.scanned is
        # incremented must be preceded by a counter increment in the same block.
        body = ast.unparse(loop)
        head = body.split("result.scanned")[0]
        for marker in ("result.no_price", "result.no_candles"):
            assert marker in head, (
                f"{marker} is not incremented before result.scanned — a symbol "
                f"dropped there is invisible to the funnel"
            )

    def test_no_per_symbol_logging_was_added(self):
        """1,476 symbols x a scan every 3 min would be ~700k lines a day."""
        from engine.tactical_executor import TacticalExecutor

        src = inspect.getsource(TacticalExecutor._collect)
        tree = ast.parse(src.lstrip())
        loop = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.For) and ast.unparse(n.target) == "symbol"),
            None,
        )
        calls = [
            ast.unparse(n.func)
            for n in ast.walk(loop)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        ]
        assert not any(c in ("logger.info", "logger.warning") for c in calls), (
            "per-symbol INFO/WARNING logging inside the scan loop would flood "
            "the log; the counters exist precisely to avoid it"
        )


class TestUniverseSnapshot:
    def test_snapshot_is_written_after_the_rebuild_commits(self):
        """The universe must be committed before the snapshot is attempted."""
        src = inspect.getsource(hu.rebuild_hub_universe)
        snap = src.find("HUB_UNIVERSE_SNAPSHOT")
        assert snap > 0, "no universe snapshot is written"
        first_commit = src.find("await session.commit()")
        assert 0 < first_commit < snap, (
            "the snapshot must come after the universe itself is committed"
        )

    def test_snapshot_records_symbol_to_rank(self):
        src = inspect.getsource(hu.rebuild_hub_universe)
        window = src[src.find("HUB_UNIVERSE_SNAPSHOT"):]
        assert "ranks" in window, "the snapshot must carry the symbol -> rank map"
        assert "universe_size" in window

    def test_snapshot_failure_cannot_break_the_rebuild(self):
        """A lost measurement must never become a trading fault."""
        src = inspect.getsource(hu.rebuild_hub_universe)
        idx = src.find("HUB_UNIVERSE_SNAPSHOT")
        window = src[max(0, idx - 900): idx + 900]
        assert "try:" in window and "except Exception" in window, (
            "the snapshot must be wrapped so a failure cannot propagate"
        )
        assert "rollback" in window, (
            "a failed snapshot must roll back its own transaction, leaving the "
            "already-committed universe intact"
        )

    def test_snapshot_needs_no_schema_change(self):
        """It rides simulation_logs, which already exists and takes JSON."""
        src = inspect.getsource(hu.rebuild_hub_universe)
        window = src[src.find("HUB_UNIVERSE_SNAPSHOT") - 900:]
        assert "SimulationLog" in window, (
            "the snapshot should reuse simulation_logs rather than introduce a "
            "new table and a migration"
        )


class TestFunnelRowIsBounded:
    """The per-scan funnel row must not grow with the universe."""

    def test_symbol_lists_are_capped_at_the_append_site(self):
        from engine.tactical_executor import _FUNNEL_SYMBOL_CAP, ScanResult
        r = ScanResult(sub_pipeline="F1")
        for i in range(_FUNNEL_SYMBOL_CAP + 500):
            r.no_price += 1
            if len(r.no_price_symbols) < _FUNNEL_SYMBOL_CAP:
                r.no_price_symbols.append(f"SYM{i}.NS")
        assert len(r.no_price_symbols) == _FUNNEL_SYMBOL_CAP
        assert r.no_price == _FUNNEL_SYMBOL_CAP + 500, "the counter must stay uncapped"

    def test_truncation_flag_uses_the_uncapped_counter(self):
        """The lists stop growing at the cap, so their length can never exceed it.

        Comparing list length against the cap would report False forever and
        silently hide that data was dropped.
        """
        import inspect
        from engine.tactical_executor import TacticalExecutor

        src = inspect.getsource(TacticalExecutor._scan)
        idx = src.find('"truncated"')
        assert idx > 0, "no truncation flag is recorded"
        window = src[idx: idx + 260]
        assert "result.no_price >" in window and "result.no_candles >" in window, (
            "the truncation flag must compare the uncapped counters, not the "
            "already-capped lists"
        )
        assert "len(result.no_price_symbols)" not in window

    def test_funnel_row_failure_cannot_break_the_scan(self):
        import inspect
        from engine.tactical_executor import TacticalExecutor

        src = inspect.getsource(TacticalExecutor._scan)
        idx = src.find("TACTICAL_SCAN_FUNNEL")
        assert idx > 0
        window = src[max(0, idx - 900): idx + 1400]
        assert "except Exception" in window

    def test_funnel_row_uses_its_own_session(self):
        """The scan session must contain nothing but TacticalSignal rows.

        An existing test (test_tactical_executor.py) asserts that invariant, and
        mixing a telemetry row into the trading path's session would change what
        that path commits. A separate session also makes the isolation real.
        """
        import inspect
        from engine.tactical_executor import TacticalExecutor

        src = inspect.getsource(TacticalExecutor._scan)
        idx = src.find("TACTICAL_SCAN_FUNNEL")
        window = src[max(0, idx - 900): idx + 1400]
        assert "AsyncSessionLocal" in window, (
            "the funnel row must open its own session, not reuse the scan's"
        )
        assert "session.add(_SimLog" not in window, (
            "the telemetry row must never be added to the scan's session"
        )
