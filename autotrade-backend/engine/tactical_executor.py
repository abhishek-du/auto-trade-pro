"""Path F — orchestrator.

SHADOW MODE — READ THIS BEFORE ADDING AN IMPORT
==============================================
This module deliberately contains **no path to execution**. It does not import
`execute_trade_intent`, `open_paper_trade`, `place_real_order`, or
`StrategyFamily`, and it must not — not even behind a feature flag.

That is not caution for its own sake. `docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md`
§6 defines a FORBIDDEN component as one that calls those functions *"directly or
indirectly, under any code path, **regardless of feature flags**"*, and states
that a flag-disabled strategy still containing a live call is not safely
disabled — it is "disabled by configuration, which is reversible by anyone who
flips the flag without knowing this contract exists."

Path F originates from technical conditions with no news event, which the
contract forbids (§1 line 49, §6 line 281, §10 line 347). Shadow mode is how
Path F earns its evidence without violating that: it produces exactly the
signals it would trade, sizes them exactly as it would, records both — and
stops. `tests/test_tactical_shadow_mode.py` enforces the no-execution property
by AST-scanning this package, so it cannot regress silently.

Wiring execution is Phase 2, and lands together with a written amendment to §6
and §10. Until then, every row this writes has `executed=False`.

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
        self.mode = str(_cfg("TACTICAL_EXECUTION_MODE", "shadow")).lower()
        if self.mode != "shadow":
            # Fail loudly rather than silently shadow-running when someone
            # expects execution — Phase 1 simply has no execution path.
            raise NotImplementedError(
                f"TACTICAL_EXECUTION_MODE={self.mode!r} is not implemented. "
                "Phase 1 is shadow-only; execution wiring is Phase 2 and lands "
                "with the contract amendment."
            )

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
                universe = await get_universe(session, int(_cfg("TACTICAL_F1_UNIVERSE_SIZE", 50)))
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

            except SoftTimeLimitExceeded:
                raise
            except Exception as exc:
                logger.debug(f"[tactical:{pipeline}] {symbol} skipped: {exc}")
                continue

            # Yield to the loop so a long scan cannot starve the process.
            await asyncio.sleep(0)

        return out

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

        sizing = self.risk.size(signal, ml_prob=ml_prob, vix=ctx.vix)
        if not sizing.approved:
            reasons.append(sizing.reason)
        elif not reasons:
            # Only book risk against the bucket when nothing else blocked it,
            # so the running total reflects trades that would actually be taken.
            self.risk.commit(sizing)

        would_trade = not reasons
        if not would_trade:
            result.skipped += 1

        # Shadow mode: the reason column always records the shadow notice, so a
        # row can never be misread as "this executed".
        reason_text = SHADOW_REASON if would_trade else f"{SHADOW_REASON} | blocked: {'; '.join(reasons)}"

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
                executed=False,          # invariant in Phase 1
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
