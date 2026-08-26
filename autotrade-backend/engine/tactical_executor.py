"""Path F — orchestrator.

EXECUTION IS DISABLED BY DEFAULT
================================
Path F originates trades from technical conditions with no news event. Until
2026-08-20 the contract forbade that outright (§1 line 49, §6 line 281,
§10 line 347) and this module deliberately contained no execution path at all.

Phase 2 changed that DELIBERATELY, in one commit that also amended
docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md §6 and §10. If you are reading
this and the contract does not list TACTICAL as an allowed originator, the code
and the contract have drifted and the code is wrong.

To enable execution:
  1. Risk bucket + cooldown must be Redis-backed .......... done (26d1651)
  2. F1 fast candles must be working ..................... done (e14e3ba)
  3. StrategyFamily.TACTICAL + contract amendment ........ done (this commit)
  4. Set TACTICAL_EXECUTION_ENABLED=True in .env
     OR flip RuntimeConfig("tactical_execution_enabled") for an instant,
     cross-process switch that needs no restart.

Three independent brakes remain even with the flag on:
  * PAPER_MODE=True         — no real money
  * TACTICAL_LIVE_TRADING=False — the gate blocks LIVE for this family
  * the Redis risk bucket   — 2%/day, 0.5%/trade, fails closed

The pipeline
------------
    universe -> candles -> rules -> Layer 1 score -> Layer 2 rank (stub)
             -> Layer 3 veto (stub) -> duplicate guard -> risk sizing
             -> persist TacticalSignal
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import TacticalSignal
from engine import tactical_rules as rules
from engine.tactical_data_fetcher import (
    MarketContext,
    get_candles_df,
    get_live_price,
    get_market_context,
    get_prices_batch,
    get_symbols_with_timeframe,
    get_f1_universe,
    get_universe,
    in_entry_window,
    orb_window,
)
from engine.tactical_duplicate_guard import existing_positions, is_duplicate
from engine.tactical_llm_veto import check_veto
from engine.tactical_ml_ranker import rank_signals
from engine.tactical_rules import Signal
from engine.tactical_scoring import score_and_filter
from engine.tactical_risk import TacticalRiskManager
from utils.config import settings
from utils.logger import logger

SHADOW_REASON = (
    "shadow mode — Path F does not execute (see docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md); "
    "signal recorded for evaluation only"
)


def _cfg(name: str, default):
    return getattr(settings, name, default)


def _execution_enabled() -> bool:
    """Static .env view of the master switch.

    The authoritative, restart-free check is the RuntimeConfig lookup inside
    decision_router's TACTICAL branch — this only decides whether we bother
    building an intent at all. Both must be on for a trade to happen, so a
    stale .env cannot force execution past a runtime kill switch.
    """
    return bool(_cfg("TACTICAL_EXECUTION_ENABLED", False))


# Cap on how many dropped symbols one funnel row records. The list exists to
# answer "was symbol X scanned"; it is not a data export, and an unbounded list
# on a 1,476-symbol universe would put a large JSON blob in simulation_logs on
# every scan.
_FUNNEL_SYMBOL_CAP = 250

# How many below-the-cut signals one scan records for research. TACTICAL_TOP_N
# is 5 and TACTICAL_MAX_SIGNALS_PER_CYCLE is 15, so ranks 1-15 already leave a
# TacticalSignal row each and are researchable today. Ranks 16-40 leave nothing
# at all — they are dropped inside score_and_filter. This capture exists to
# answer ONE question: is the cut discarding signals that were worth taking?
#
# It is research-only. These rows are SimulationLog entries. No TacticalSignal
# is created, no intent is built, no sizing is requested and no risk is booked,
# so nothing here can reach an executor. tests/test_rank_overflow_capture.py
# enforces that. TACTICAL_TOP_N is NOT changed by this.
_RANK_OVERFLOW_CAP = 25


@dataclass
class ScanResult:
    sub_pipeline: str
    scanned: int = 0
    raw_signals: int = 0
    kept: int = 0
    persisted: int = 0
    skipped: int = 0
    reason: str = ""
    # Why symbols never reached the rules (2026-08-26, phase 21).
    #
    # `scanned` is incremented AFTER the two `continue`s in _collect(), so a
    # symbol dropped for a missing price or missing candles was counted
    # nowhere at all. That is precisely the gap the 2026-08-26 opportunity
    # audit could not close: for eight of the day's biggest movers, sitting
    # inside the F1 universe and producing no signal, there was no way to tell
    # whether the scanner processed them and the rules declined, or whether
    # they never reached the rules. The first is a strategy question, the
    # second is an engineering defect, and they need opposite responses.
    #
    # Counts only. universe = scanned + no_price + no_candles.
    universe: int = 0
    no_price: int = 0
    no_candles: int = 0
    # Which symbols died at each drop stage, so the funnel is answerable per
    # symbol and not only in aggregate. Truncated to _FUNNEL_SYMBOL_CAP when
    # persisted; the in-memory lists are per-scan and discarded with the object.
    no_price_symbols: list = field(default_factory=list)
    no_candles_symbols: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "sub_pipeline": self.sub_pipeline,
            "universe": self.universe,
            "scanned": self.scanned,
            "no_price": self.no_price,
            "no_candles": self.no_candles,
            "raw_signals": self.raw_signals,
            "kept": self.kept,
            "persisted": self.persisted,
            "skipped": self.skipped,
            "reason": self.reason,
        }


class TacticalExecutor:
    """One instance per scan. Holds no cross-cycle state except the risk bucket."""

    def __init__(self, risk: TacticalRiskManager | None = None) -> None:
        self.risk = risk or TacticalRiskManager()
        # Phase 2 retired TACTICAL_EXECUTION_MODE in favour of a single switch.
        # Two overlapping flags (MODE="shadow" while ENABLED=True) could only
        # ever contradict each other, and the ambiguity would be resolved at
        # runtime by whichever check happened to run first.
        self.mode = "execute" if _execution_enabled() else "shadow"

    # ── entry points ─────────────────────────────────────────────────────────

    async def run_intraday_scan(self, session: AsyncSession | None = None) -> dict:
        return (await self._run("F1", session)).as_dict()

    async def run_mean_reversion_scan(self, session: AsyncSession | None = None) -> dict:
        return (await self._run("F4", session)).as_dict()

    # ── core ─────────────────────────────────────────────────────────────────

    async def _run(self, pipeline: str, session: AsyncSession | None) -> ScanResult:
        result = ScanResult(sub_pipeline=pipeline)

        if not bool(_cfg("TACTICAL_PIPELINE_ENABLED", True)):
            result.reason = "TACTICAL_PIPELINE_ENABLED=False"
            return result

        if not in_entry_window():
            result.reason = "outside tactical entry window (09:15-15:20 IST, weekdays)"
            logger.debug(f"[tactical:{pipeline}] {result.reason}")
            return result

        if session is not None:
            return await self._scan(pipeline, session, result)

        from db.database import AsyncSessionLocal

        async with AsyncSessionLocal() as own_session:
            return await self._scan(pipeline, own_session, result)

    async def _scan(self, pipeline: str, session: AsyncSession, result: ScanResult) -> ScanResult:
        try:
            ctx = await get_market_context()

            if pipeline == "F1":
                # Dynamic liquidity filter, not a fixed top-N (2026-08-20).
                # See get_f1_universe for the coverage/latency measurements.
                universe = await get_f1_universe(session)
            else:
                universe = await get_symbols_with_timeframe(
                    session, "5m", int(_cfg("TACTICAL_F4_UNIVERSE_SIZE", 150))
                )

            if not universe:
                result.reason = "empty universe"
                return result

            try:
                open_map = await existing_positions(session)
            except Exception:
                # The guard raises rather than returning {} so we cannot mistake
                # "lookup failed" for "no open positions". Abort the cycle.
                result.reason = "position lookup failed — skipping cycle rather than risk duplicates"
                logger.warning(f"[tactical:{pipeline}] {result.reason}")
                return result

            signals = await self._collect(pipeline, universe, session, ctx, result)

            # One aggregate line when the feed is unusable, instead of a
            # per-symbol warning from the staleness guard.
            if result.scanned == 0 and universe:
                logger.warning(
                    f"[tactical:{pipeline}] scanned 0 of {len(universe)} symbols — "
                    f"no usable candles (feed stale or missing for this timeframe)"
                )
            result.raw_signals = len(signals)
            if not signals:
                result.reason = "no rule triggered"
                return result

            overflow: list = []
            scored = await score_and_filter(
                signals,
                session,
                min_score=float(_cfg("TACTICAL_MIN_COMPOSITE_SCORE", 50.0)),
                top_n=int(_cfg("TACTICAL_MAX_SIGNALS_PER_CYCLE", 15)),
                overflow_out=overflow,
            )
            result.kept = len(scored)
            if not scored:
                result.reason = "all signals below composite threshold"
                return result

            ranked = rank_signals(scored, top_n=int(_cfg("TACTICAL_TOP_N", 5)))

            for signal, composite, ml_prob in ranked:
                await self._persist(signal, composite, ml_prob, open_map, ctx, session, result)

            await session.commit()
            logger.info(
                f"[tactical:{pipeline}] universe={result.universe} scanned={result.scanned} "
                f"no_price={result.no_price} no_candles={result.no_candles} "
                f"raw={result.raw_signals} "
                f"kept={result.kept} persisted={result.persisted} skipped={result.skipped}"
            )

            # Per-scan funnel row (2026-08-26, phase 21). The counters above say
            # HOW MANY symbols died at each stage; this says WHICH, so a question
            # like "was TVSSRICHAK scanned on 2026-08-27" becomes answerable.
            #
            # Bounded by construction: only the two drop lists are stored, each
            # truncated to _FUNNEL_SYMBOL_CAP with a flag recording that it was.
            # Per-symbol LOG lines were rejected outright — 1,476 symbols against
            # a scan every 3 minutes is ~700k lines a day.
            #
            # Symbols and counts only. No prices, no payloads, no credentials.
            # Failure here cannot affect the scan: the signals are already
            # committed above, and this rolls back only itself.
            # Written on its OWN session, never the scan's. The scan session is
            # expected to contain nothing but TacticalSignal rows — an existing
            # test asserts exactly that — and mixing a telemetry row into it
            # would change what the trading path commits. A separate session
            # also makes the failure isolation real rather than nominal.
            await self._capture_rank_overflow(pipeline, overflow, len(scored), ctx)

            try:
                from db.database import AsyncSessionLocal as _ASL
                from db.models import SimulationLog as _SimLog
                _np = result.no_price_symbols[:_FUNNEL_SYMBOL_CAP]
                _nc = result.no_candles_symbols[:_FUNNEL_SYMBOL_CAP]
                async with _ASL() as _fsess:
                    _fsess.add(_SimLog(
                        event_type="TACTICAL_SCAN_FUNNEL",
                        symbol=pipeline,
                        message=(
                            f"{pipeline}: universe={result.universe} "
                            f"scanned={result.scanned} raw={result.raw_signals} "
                            f"kept={result.kept} persisted={result.persisted}"
                        ),
                        data={
                            **result.as_dict(),
                            "no_price_symbols": _np,
                            "no_candles_symbols": _nc,
                            # Compare against the UNCAPPED counters. The lists
                            # stop growing at the cap, so their own length can
                            # never exceed it and would report False forever.
                            "truncated": (
                                result.no_price > _FUNNEL_SYMBOL_CAP
                                or result.no_candles > _FUNNEL_SYMBOL_CAP
                            ),
                        },
                    ))
                    await _fsess.commit()
            except Exception as _exc:
                # Nothing to roll back on the scan's session — this never
                # touched it. The telemetry session is closed by its own
                # context manager.
                logger.warning(f"[tactical:{pipeline}] funnel row failed: {type(_exc).__name__}")
            return result

        except SoftTimeLimitExceeded:
            # The worker shares a 2-slot queue with the 5s stop-loss loop; give
            # the slot back immediately rather than being killed mid-transaction.
            result.reason = "soft time limit exceeded — cycle abandoned"
            logger.warning(f"[tactical:{pipeline}] {result.reason}")
            try:
                await session.rollback()
            except Exception:
                pass
            return result
        except Exception as exc:
            result.reason = f"error: {type(exc).__name__}: {exc}"
            logger.error(f"[tactical:{pipeline}] scan failed: {exc}")
            try:
                await session.rollback()
            except Exception:
                pass
            return result

    async def _capture_rank_overflow(
        self, pipeline: str, overflow: list, kept_n: int, ctx: MarketContext
    ) -> None:
        """Record the signals that fell below the persist cut. NEVER executes.

        Writes at most _RANK_OVERFLOW_CAP SimulationLog rows' worth of data as a
        single row, on its OWN session — the scan's session is expected to
        contain nothing but TacticalSignal rows and an existing test asserts
        exactly that.

        `entry_eligible` records whether the signal would have been allowed to
        trade on its own merits (a real entry price, stop and target), which is
        what makes the post-close return study meaningful: a signal with no
        usable stop was never a candidate regardless of its rank.
        """
        if not overflow:
            return
        try:
            from db.database import AsyncSessionLocal as _ASL
            from db.models import SimulationLog as _SimLog

            rows = []
            for offset, (sig, score) in enumerate(overflow[:_RANK_OVERFLOW_CAP]):
                eligible = bool(
                    sig.entry_price and sig.entry_price > 0
                    and sig.stop_loss and sig.stop_loss > 0
                    and sig.target and sig.target > 0
                    and sig.stop_loss != sig.entry_price
                )
                rows.append({
                    "symbol": sig.symbol,
                    "rank": kept_n + offset + 1,
                    "score": round(float(score), 2),
                    "signal_type": sig.side,
                    "strategy": sig.strategy_name,
                    "reference_price": round(float(sig.entry_price or 0), 2),
                    "stop_loss": round(float(sig.stop_loss or 0), 2),
                    "target": round(float(sig.target or 0), 2),
                    "entry_eligible": eligible,
                    "not_persisted_reason": (
                        f"rank {kept_n + offset + 1} > TACTICAL_MAX_SIGNALS_PER_CYCLE "
                        f"({kept_n}) — dropped inside score_and_filter"
                    ),
                })

            async with _ASL() as _sess:
                _sess.add(_SimLog(
                    event_type="TACTICAL_RANK_OVERFLOW",
                    symbol=pipeline,
                    message=(
                        f"{pipeline}: {len(overflow)} signals below the persist cut "
                        f"of {kept_n}; recorded {len(rows)}"
                    ),
                    data={
                        "sub_pipeline": pipeline,
                        "timestamp": datetime.now().isoformat(),
                        "kept_n": kept_n,
                        "overflow_total": len(overflow),
                        "recorded": len(rows),
                        "truncated": len(overflow) > _RANK_OVERFLOW_CAP,
                        "vix": ctx.vix,
                        "signals": rows,
                    },
                ))
                await _sess.commit()
        except Exception as exc:
            # Research capture. Losing it loses a measurement and nothing else.
            logger.warning(f"[tactical:{pipeline}] rank overflow capture failed: {type(exc).__name__}")

    async def _collect(
        self,
        pipeline: str,
        universe: list[str],
        session: AsyncSession,
        ctx: MarketContext,
        result: ScanResult,
    ) -> list[Signal]:
        out: list[Signal] = []
        orb_start, orb_end = orb_window()

        # One batched LTP call for the whole universe rather than a network
        # round-trip per symbol — see get_prices_batch for the measurement that
        # forced this.
        prices = await get_prices_batch(universe)
        if not prices:
            logger.warning(f"[tactical:{pipeline}] no prices returned for {len(universe)} symbols")
            return out

        result.universe = len(universe)
        for symbol in universe:
            try:
                price = prices.get(symbol)
                if not price:
                    result.no_price += 1
                    if len(result.no_price_symbols) < _FUNNEL_SYMBOL_CAP:
                        result.no_price_symbols.append(symbol)
                    continue

                if pipeline == "F1":
                    df_1m = await get_candles_df(symbol, "1m", 200, session)
                    if df_1m is None:
                        result.no_candles += 1
                        if len(result.no_candles_symbols) < _FUNNEL_SYMBOL_CAP:
                            result.no_candles_symbols.append(symbol)
                        continue
                    df_d = await get_candles_df(symbol, "1d", 30, session)
                    result.scanned += 1

                    out += rules.orb(symbol, df_1m, price, orb_start, orb_end)
                    out += rules.vwap_trend(symbol, df_1m, price)
                    out += rules.scalp_engulfing(symbol, df_1m, price)
                    if df_d is not None:
                        out += rules.gap_and_go(symbol, df_1m, df_d, price)
                        out += rules.pivot_bounce_breakout(symbol, df_1m, df_d, price)
                        # Pure trend capture -- needs df_d for the 20-day volume
                        # baseline. See rules.day_momentum for what it fixes.
                        out += rules.day_momentum(symbol, df_1m, df_d, price)
                        # Short mirror. F1 emitted 251 BUY vs 17 SELL signals on
                        # 2026-08-21 and produced nothing on 14 of the 15 biggest
                        # losers — the short side had no pattern-free rule.
                        out += rules.day_weakness(symbol, df_1m, df_d, price)
                else:
                    df_5m = await get_candles_df(symbol, "5m", 100, session)
                    if df_5m is None:
                        continue
                    result.scanned += 1
                    out += rules.overbought_fade(symbol, df_5m, price)
                    out += rules.oversold_rebound(symbol, df_5m, price)
                    # Trend rules added 2026-08-20: F4 was fade-only, so a
                    # sector trending hard all session produced nothing here.
                    out += rules.volume_breakout_5m(symbol, df_5m, price)
                    out += rules.vwap_crossover_5m(symbol, df_5m, price)

            except SoftTimeLimitExceeded:
                raise
            except Exception as exc:
                logger.debug(f"[tactical:{pipeline}] {symbol} skipped: {exc}")
                continue

            # Yield to the loop so a long scan cannot starve the process.
            await asyncio.sleep(0)

        return out

    async def _execute(
        self, signal: Signal, composite: float, sizing, session: AsyncSession
    ) -> tuple[bool, str | None, str | None, str]:
        """Offer one approved signal to the central execution gate.

        Returns (executed, order_ref, routing_outcome, note). Never raises — a
        routing failure must not abort the rest of the cycle or lose the audit
        row for the signals already collected.

        Imports are local and deliberate: at module scope they would make this
        file reference execution symbols unconditionally, and the point of the
        guard is that a disabled pipeline never touches them.
        """
        # Sector-breadth veto (2026-08-21): refuse a long into a sector that is
        # broadly falling. F1 reads price patterns and has no view on news; on
        # the duty-free-import day it bought DHAMPURSUG while all 13 sugar peers
        # were red. Deliberately measures what the sector is DOING, because the
        # classifier had marked that news BULLISH -- a veto keyed on the event
        # direction would have passed the trade straight through.
        from engine.sector_breadth_veto import sector_breadth_veto

        _veto, _vreason = await sector_breadth_veto(signal.symbol, signal.side, session)
        if _veto:
            logger.info(f"[TACTICAL] SECTOR VETO {signal.symbol}: {_vreason}")
            return False, None, None, f"sector breadth veto: {_vreason}"

        # Admin toggle (Path F), checked alongside TACTICAL_EXECUTION_ENABLED.
        # Signals are still scanned, scored and PERSISTED when this is off --
        # only execution stops -- so the tactical_signals audit trail stays
        # continuous and a later review can see what the pipeline would have done.
        from utils.runtime_config import strategy_enabled

        if not await strategy_enabled("tactical", session):
            return False, None, None, "tactical strategy disabled by toggle"

        try:
            from engine.decision_router import (
                ConfidenceSource,
                EventDirectness,
                RoutingOutcome,
                StrategyFamily,
                TradeIntent,
                execute_trade_intent,
            )
        except Exception as exc:
            return False, None, None, f"router import failed: {exc}"

        try:
            intent = TradeIntent(
                strategy=f"TACTICAL_{signal.strategy_name}",
                symbol=signal.symbol,
                action=signal.side,
                instrument_type="EQUITY",
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.target,
                # The composite score IS the confidence, and it is genuinely
                # computed — the gate rejects anything not marked CALCULATED,
                # and a hardcoded number would be a lie in the audit trail.
                confidence=float(composite),
                confidence_source=ConfidenceSource.CALCULATED,
                strategy_family=StrategyFamily.TACTICAL,
                # No event to be direct or second-order about.
                event_directness=EventDirectness.NOT_APPLICABLE,
                # Pass our own sizing through so the gate does not re-derive a
                # size that ignores the tactical bucket.
                position_size_hint={
                    "units": sizing.quantity,
                    "usd_value": sizing.notional,
                },
                # Every tactical pipeline is intraday: F1 and F4 both scan only
                # inside the 09:15-15:20 entry window. CNC here was not a design
                # choice — it was an unexplained one-liner that made F4 signals
                # delivery trades, and delivery has three consequences none of
                # the F4 rules want:
                #   1. trade_simulator maps CNC -> trade_style="SWING" and sets
                #      swing_min_hold = +48h, and india_tasks suppresses the
                #      stop loss for that whole window (fast_sl_check sets
                #      sl_hit = False). An intraday mean-reversion trade was
                #      held for two sessions with no stop.
                #   2. that same 48h hold pinned capital, so later signals were
                #      refused by the cash buffer.
                #   3. a CNC SELL is a delivery short, which the cash segment
                #      does not permit at all. engine/agent/execution.py blocks
                #      it on the live path; paper mode did not, so F4 opened
                #      three shorts that could never have been placed for real.
                # Tactical signals are intraday, so they are MIS.
                product="MIS",
            )
        except Exception as exc:
            return False, None, None, f"intent build failed: {exc}"

        try:
            outcome = await execute_trade_intent(intent, session)
        except Exception as exc:
            logger.error(f"[TACTICAL] routing error for {signal.symbol}: {exc}")
            return False, None, None, f"routing error: {exc}"

        ok = outcome.outcome in (RoutingOutcome.EXECUTED_PAPER, RoutingOutcome.EXECUTED_LIVE)
        if ok:
            logger.info(
                f"[TACTICAL] EXECUTED {signal.symbol} {signal.side} qty={sizing.quantity} "
                f"strategy={signal.strategy_name} outcome={outcome.outcome.value} "
                f"order={outcome.order_id}"
            )
        else:
            logger.info(
                f"[TACTICAL] REJECTED {signal.symbol} {signal.side} "
                f"outcome={outcome.outcome.value} reason={outcome.reason}"
            )
        return ok, outcome.order_id, outcome.outcome.value, outcome.reason or ""

    async def _persist(
        self,
        signal: Signal,
        composite: float,
        ml_prob: float,
        open_map: dict[str, str],
        ctx: MarketContext,
        session: AsyncSession,
        result: ScanResult,
    ) -> None:
        """Record the signal and why it was (not) taken. Never executes."""
        reasons: list[str] = []

        dupe, dupe_reason = is_duplicate(signal.symbol, open_map)
        if dupe:
            reasons.append(dupe_reason)

        veto = await check_veto(signal)
        if veto.vetoed:
            reasons.append(f"llm veto: {veto.reason}")

        sizing = await self.risk.size(signal, ml_prob=ml_prob, vix=ctx.vix)
        if not sizing.approved:
            reasons.append(sizing.reason)
        elif not reasons:
            # Only book risk against the bucket when nothing else blocked it,
            # so the running total reflects trades that would actually be taken.
            await self.risk.commit(sizing)

        would_trade = not reasons
        if not would_trade:
            result.skipped += 1

        # ── Execution (Phase 2) ─────────────────────────────────────────────
        # Only a signal that cleared EVERY local check is offered to the central
        # gate, which then applies the same market-hours / confidence-provenance
        # / 12-check risk validation every other family gets. Path F's own risk
        # bucket is an ADDITIONAL cap, not a replacement for that.
        executed = False
        executed_at = None
        order_ref = None
        routing_outcome = None
        exec_note = ""

        if would_trade and _execution_enabled():
            executed, order_ref, routing_outcome, exec_note = await self._execute(
                signal, composite, sizing, session
            )
            if not executed:
                result.skipped += 1
                reasons.append(exec_note)
            else:
                executed_at = datetime.now()

        # The reason column must never let a row be misread. In shadow mode it
        # carries the shadow notice; with execution on it carries the router's
        # verdict instead.
        if not _execution_enabled():
            reason_text = SHADOW_REASON if would_trade else f"{SHADOW_REASON} | blocked: {'; '.join(reasons)}"
        elif executed:
            reason_text = f"executed via central gate ({routing_outcome})"
        else:
            reason_text = f"not executed | {'; '.join(reasons) or exec_note}"

        session.add(
            TacticalSignal(
                symbol=signal.symbol,
                timestamp=signal.timestamp or datetime.now(),
                strategy=signal.strategy_name,
                sub_pipeline=signal.sub_pipeline,
                signal_type=signal.side,
                entry_price=round(signal.entry_price, 2),
                stop_loss=round(signal.stop_loss, 2),
                target=round(signal.target, 2),
                composite_score=composite,
                ml_prob=ml_prob,
                quantity=sizing.quantity if sizing.approved else 0,
                risk_amount=sizing.risk_amount,
                executed=executed,
                executed_at=executed_at,
                order_ref=order_ref,
                routing_outcome=routing_outcome,
                reason=reason_text,
                meta_json={
                    "meta": signal.meta,
                    "vix": ctx.vix,
                    "would_trade": would_trade,
                    "rule_confidence": signal.confidence,
                    # Entry-quality telemetry (Phase 25). Observational only —
                    # nothing reads these back to make a decision.
                    #
                    # signal_ts is when the rule fired; entry_ts is when the
                    # gate accepted the intent. The fill price is NOT known
                    # here (the router owns it), so signal_to_entry slippage is
                    # derived after the session by joining paper_trades on
                    # order_ref. opportunity_ts — the moment the move began — has
                    # no live definition and is reconstructed from candles by the
                    # Phase-24 method; it is deliberately absent rather than
                    # guessed at.
                    "entry_quality": {
                        "signal_ts": (signal.timestamp or datetime.now()).isoformat(),
                        "entry_ts": executed_at.isoformat() if executed_at else None,
                        "signal_price": round(float(signal.entry_price or 0), 4),
                        "signal_to_entry_minutes": (
                            round((executed_at - (signal.timestamp or executed_at)).total_seconds() / 60.0, 3)
                            if executed_at else None
                        ),
                        "order_ref": order_ref,
                    },
                },
            )
        )
        result.persisted += 1
