"""Path F — its own risk bucket, deliberately separate from the news paths.

Why separate at all: `engine/risk_manager.validate_signal` is family-blind, and
its per-strategy capital cap keys on the free-text `strategy` name — so a new
strategy string would receive a FULL fresh allocation rather than sharing the
existing families' budget (audit finding while planning Path F). Path F must
therefore cap itself; it cannot inherit a cap it was never counted against.

Phase 1 status: every number here is COMPUTED AND RECORDED, not enforced against
a live book, because the pipeline is in shadow mode and opens no positions. The
maths is live so that the sizing recorded on each `tactical_signals` row is the
size that would have been taken — which is what makes the shadow period
evaluable.

State lives in REDIS, not on the instance (2026-08-20)
------------------------------------------------------
`TacticalExecutor` is constructed fresh by every Celery run, so an instance
attribute resets each cycle. The 2026-08-20 audit measured the consequence: 322
would-trade signals totalling Rs 793,907 of risk against a Rs 10,000 daily
bucket — 79x over — because `open_risk` went back to 0.0 every minute. The cap
was only ever enforced *within* a single scan.

Both the risk bucket and the loss cooldown are therefore keyed per trading day
in Redis, which also makes them correct across multiple workers and across a
worker restart. Keys expire on their own, so there is no cleanup path to forget.

Unlike the Kite rate limiter (which fails OPEN, because a throttling outage must
never wedge the trading loop), this fails CLOSED: a risk cap that stops applying
when Redis blips is not a cap. The cost of failing closed here is a skipped
signal, which is cheap.
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from engine.tactical_rules import Signal
from utils.config import settings
from utils.logger import logger

# Two days: long enough that a key written on Friday survives the weekend for
# inspection, short enough that it never accumulates.
_KEY_TTL_SEC = 86_400 * 2
_COOLDOWN_SEC = 3600


def risk_key(day: date | None = None) -> str:
    return f"tactical:risk:{(day or date.today()).isoformat()}"


def cooldown_key(day: date | None = None) -> str:
    return f"tactical:cooldown:{(day or date.today()).isoformat()}"


@dataclass(frozen=True)
class SizingDecision:
    approved: bool
    quantity: int
    risk_amount: float
    reason: str
    notional: float = 0.0


def _cfg(name: str, default):
    return getattr(settings, name, default)


class TacticalRiskManager:
    """Per-cycle risk arbiter. One instance per scan; state is not persisted.

    Cooldown note: the 3-consecutive-loss pause is in-memory, so it resets when
    the Celery worker restarts. That is acceptable in shadow mode (nothing is at
    risk) but MUST move to Redis before Phase 2 wires execution, or a worker
    restart silently clears the pause.
    """

    def __init__(self, capital: float | None = None) -> None:
        self.capital = float(capital if capital is not None else _cfg("TACTICAL_CAPITAL", 500_000.0))
        self.max_total_risk = float(_cfg("TACTICAL_MAX_TOTAL_RISK", 0.02))
        # False => no daily cap at all (contract SS10c). The budget value above
        # is still computed and reported so the summary keeps showing what the
        # day would have consumed against the old 2% line.
        self.bucket_enabled = bool(_cfg("TACTICAL_RISK_BUCKET_ENABLED", True))
        self.max_per_trade_risk = float(_cfg("TACTICAL_MAX_PER_TRADE_RISK", 0.005))
        self.vix_threshold = float(_cfg("TACTICAL_VIX_THRESHOLD", 25.0))
        self.vix_scale = float(_cfg("TACTICAL_VIX_SIZE_SCALE", 0.5))
        # NOTE: no `open_risk` / `cooldown_until` / `consecutive_losses` here.
        # Per-instance state was the bug — see the module docstring. All of it
        # now lives in Redis, keyed per trading day.

    # ── budget ────────────────────────────────────────────────────────────────

    @property
    def total_risk_budget(self) -> float:
        return self.capital * self.max_total_risk

    # ── Redis-backed daily state ─────────────────────────────────────────────

    async def open_risk(self) -> float:
        """Risk already committed today, across every process. Raises on failure.

        Deliberately raises rather than returning 0.0: a Redis blip must not be
        indistinguishable from "no risk taken yet", which is precisely how the
        cap would silently stop applying.
        """
        from utils.cache import get_redis

        raw = await get_redis().get(risk_key())
        return float(raw or 0.0)

    async def remaining_budget(self) -> float:
        return max(0.0, self.total_risk_budget - await self.open_risk())

    async def in_cooldown(self) -> bool:
        """True while the 3-consecutive-stop pause is active.

        The key carries its own 1-hour TTL, so expiry needs no cleanup path and
        survives a worker restart.
        """
        from utils.cache import get_redis

        raw = await get_redis().get(cooldown_key())
        if not raw:
            return False
        try:
            return int(float(raw)) > int(_time.time())
        except (TypeError, ValueError):
            return False

    async def record_stop_loss(self) -> None:
        """Three stop-outs in a row pause the whole tactical bucket for 60 min.

        The streak counter is itself in Redis, so it is shared across workers
        and cannot be reset by a redeploy mid-streak.
        """
        from utils.cache import get_redis

        r = get_redis()
        streak_key = f"{cooldown_key()}:streak"
        try:
            losses = await r.incrby(streak_key, 1)
            await r.expire(streak_key, _KEY_TTL_SEC)
            if losses >= 3:
                until = int(_time.time()) + _COOLDOWN_SEC
                await r.setex(cooldown_key(), _COOLDOWN_SEC, until)
                logger.warning(
                    f"[tactical_risk] {losses} consecutive stops — tactical paused "
                    f"for {_COOLDOWN_SEC // 60} min"
                )
        except Exception as exc:
            # Losing a cooldown is worse than losing a signal, so make the
            # failure loud rather than silent.
            logger.error(f"[tactical_risk] could not record stop-loss streak: {exc}")

    async def record_win(self) -> None:
        """A win breaks the streak."""
        from utils.cache import get_redis

        try:
            await get_redis().delete(f"{cooldown_key()}:streak")
        except Exception as exc:
            logger.warning(f"[tactical_risk] could not reset loss streak: {exc}")

    @staticmethod
    async def reset_daily_risk(day: date | None = None) -> None:
        """Manual escape hatch — clears the bucket, cooldown and streak."""
        from utils.cache import get_redis

        r = get_redis()
        await r.delete(risk_key(day), cooldown_key(day), f"{cooldown_key(day)}:streak")
        logger.warning(f"[tactical_risk] daily risk state RESET for {day or date.today()}")

    # ── sizing ────────────────────────────────────────────────────────────────

    async def size(
        self,
        signal: Signal,
        *,
        ml_prob: float = 0.5,
        vix: float | None = None,
        now: datetime | None = None,
    ) -> SizingDecision:
        """Position size for one signal, or a rejection with the reason.

        Async because the daily budget and cooldown live in Redis. FAILS CLOSED:
        if Redis cannot be read, no size is approved.
        """
        try:
            if await self.in_cooldown():
                return SizingDecision(False, 0, 0.0, "tactical cooldown active after 3 consecutive stops")
            already_committed = await self.open_risk()
        except Exception as exc:
            # A cap that stops applying during a Redis blip is not a cap.
            logger.error(f"[tactical_risk] risk bucket unavailable ({exc}) — refusing to size")
            return SizingDecision(False, 0, 0.0, f"risk bucket unavailable: {exc}")

        risk_per_unit = signal.risk_per_unit
        if risk_per_unit <= 0:
            return SizingDecision(False, 0, 0.0, "invalid stop distance (entry == stop)")

        risk_amount = self.max_per_trade_risk * self.capital
        quantity = int(risk_amount / risk_per_unit)

        # Conviction scaling off the ML probability.
        #
        # The neutral sentinel means "Layer 2 has no opinion" (no model is
        # loaded — see tactical_ml_ranker). It must NOT be treated as a weak
        # signal: the brief's rule scales down below 0.55, and 0.5 sits under
        # that, so the stub would silently shrink EVERY position by 30% while
        # ranking nothing. Skip scaling entirely unless a real model spoke.
        from engine.tactical_ml_ranker import NEUTRAL_PROBABILITY

        if ml_prob != NEUTRAL_PROBABILITY:
            if ml_prob > 0.7:
                quantity = int(quantity * 1.2)
            elif ml_prob < 0.55:
                quantity = int(quantity * 0.7)

        if vix is not None and vix > self.vix_threshold:
            quantity = int(quantity * self.vix_scale)

        quantity = max(1, quantity)

        # ── Notional cap ────────────────────────────────────────────────────
        # Risk-based sizing alone is not enough: a tight stop buys an enormous
        # position for the same rupee risk. Observed 2026-08-20 — GROWW.NS had a
        # Rs 5.50 stop, so the 0.5% risk budget bought 1,644 shares = Rs 328,422
        # notional, caught only by the trade_simulator hard guard. That guard is
        # meant to be the last line of defence, not the only one.
        #
        # The FULL 10% applies -- not min(10%, global 5%). All three downstream
        # cap sites became family-aware on 2026-08-20, so a 10% tactical size
        # now clears them instead of being rejected by check 5 or raising inside
        # the trade_simulator hard guard.
        effective_pct = float(_cfg("TACTICAL_MAX_POSITION_NOTIONAL_PCT", 0.10))
        max_notional = self.capital * effective_pct
        if signal.entry_price > 0 and quantity * signal.entry_price > max_notional:
            capped = int(max_notional // signal.entry_price)
            if capped < 1:
                return SizingDecision(
                    False, 0, 0.0,
                    f"one share ({signal.entry_price:.0f}) exceeds the notional cap "
                    f"({max_notional:.0f} = {effective_pct:.0%} of {self.capital:.0f})",
                )
            logger.debug(
                f"[tactical_risk] {signal.symbol}: notional cap trimmed {quantity} -> "
                f"{capped} shares (cap {max_notional:.0f})"
            )
            quantity = capped

        actual_risk = quantity * risk_per_unit

        # ── daily bucket ─────────────────────────────────────────────────────
        # DISABLED by owner decision 2026-08-20 (contract SS10c). This was the
        # PRIMARY brake on Path F and the stated basis for SS10a condition 3,
        # which is why turning it off required amending the contract rather than
        # editing a number.
        #
        # With this off, nothing caps how much risk the tactical pipeline
        # commits in a day. The only remaining limits are portfolio-level and
        # per-position: MAX_PORTFOLIO_RISK (15% of equity across all open
        # positions), MAX_OPEN_POSITIONS (125), and the 10% notional cap above.
        # The 3-consecutive-stop cooldown still applies and is now the only
        # loss-reactive control in the path.
        #
        # Risk is still COMMITTED to Redis when enabled=False, so the daily
        # total stays observable in the summary and re-enabling the cap does not
        # start from a blank slate.
        if self.bucket_enabled:
            remaining = max(0.0, self.total_risk_budget - already_committed)
            if actual_risk > remaining:
                return SizingDecision(
                    False, 0, actual_risk,
                    f"would exceed tactical bucket: {actual_risk:.0f} > "
                    f"{remaining:.0f} remaining of {self.total_risk_budget:.0f} "
                    f"(committed today: {already_committed:.0f})",
                )

        return SizingDecision(
            True, quantity, actual_risk, "approved",
            notional=quantity * signal.entry_price,
        )

    async def commit(self, decision: SizingDecision) -> bool:
        """Book an approved trade's risk against today's bucket. Atomic.

        INCRBYFLOAT is atomic, so two workers committing simultaneously cannot
        lose an increment — which a read-modify-write on an instance attribute
        very much could.
        """
        if not decision.approved:
            return False
        from utils.cache import get_redis

        try:
            r = get_redis()
            key = risk_key()
            await r.incrbyfloat(key, decision.risk_amount)
            await r.expire(key, _KEY_TTL_SEC)
            return True
        except Exception as exc:
            # If we cannot record the risk we must not pretend the trade is
            # free — surface it loudly.
            logger.error(f"[tactical_risk] FAILED to commit {decision.risk_amount:.0f} to bucket: {exc}")
            return False
