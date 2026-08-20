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
from dataclasses import dataclass
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


@dataclass
class ScanResult:
    sub_pipeline: str
    scanned: int = 0
    raw_signals: int = 0
    kept: int = 0
    persisted: int = 0
    skipped: int = 0
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "sub_pipeline": self.sub_pipeline,
            "scanned": self.scanned,
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

            scored = await score_and_filter(
                signals,
                session,
                min_score=float(_cfg("TACTICAL_MIN_COMPOSITE_SCORE", 50.0)),
                top_n=int(_cfg("TACTICAL_MAX_SIGNALS_PER_CYCLE", 15)),
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
                f"[tactical:{pipeline}] scanned={result.scanned} raw={result.raw_signals} "
                f"kept={result.kept} persisted={result.persisted} skipped={result.skipped}"
            )
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

        for symbol in universe:
            try:
                price = prices.get(symbol)
                if not price:
                    continue

                if pipeline == "F1":
                    df_1m = await get_candles_df(symbol, "1m", 200, session)
                    if df_1m is None:
                        continue
                    df_d = await get_candles_df(symbol, "1d", 30, session)
                    result.scanned += 1

                    out += rules.orb(symbol, df_1m, price, orb_start, orb_end)
                    out += rules.vwap_trend(symbol, df_1m, price)
                    out += rules.scalp_engulfing(symbol, df_1m, price)
                    if df_d is not None:
                        out += rules.gap_and_go(symbol, df_1m, df_d, price)
                        out += rules.pivot_bounce_breakout(symbol, df_1m, df_d, price)
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
                product="MIS" if signal.sub_pipeline == "F1" else "CNC",
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
                },
            )
        )
        result.persisted += 1
