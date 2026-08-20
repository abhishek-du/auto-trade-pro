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
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from engine.tactical_rules import Signal
from utils.config import settings
from utils.logger import logger


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
        self.max_per_trade_risk = float(_cfg("TACTICAL_MAX_PER_TRADE_RISK", 0.005))
        self.vix_threshold = float(_cfg("TACTICAL_VIX_THRESHOLD", 25.0))
        self.vix_scale = float(_cfg("TACTICAL_VIX_SIZE_SCALE", 0.5))
        self.open_risk = 0.0
        self.consecutive_losses = 0
        self.cooldown_until: datetime | None = None

    # ── budget ────────────────────────────────────────────────────────────────

    @property
    def total_risk_budget(self) -> float:
        return self.capital * self.max_total_risk

    @property
    def remaining_budget(self) -> float:
        return max(0.0, self.total_risk_budget - self.open_risk)

    def in_cooldown(self, now: datetime | None = None) -> bool:
        if self.cooldown_until is None:
            return False
        if (now or datetime.now()) >= self.cooldown_until:
            self.cooldown_until = None
            self.consecutive_losses = 0
            return False
        return True

    def record_stop_loss(self, now: datetime | None = None) -> None:
        """Three stop-outs in a row pauses the whole tactical bucket for 60 min."""
        self.consecutive_losses += 1
        if self.consecutive_losses >= 3:
            self.cooldown_until = (now or datetime.now()) + timedelta(hours=1)
            logger.warning(
                f"[tactical_risk] 3 consecutive stops — tactical paused until "
                f"{self.cooldown_until:%H:%M:%S}"
            )

    def record_win(self) -> None:
        self.consecutive_losses = 0

    # ── sizing ────────────────────────────────────────────────────────────────

    def size(
        self,
        signal: Signal,
        *,
        ml_prob: float = 0.5,
        vix: float | None = None,
        now: datetime | None = None,
    ) -> SizingDecision:
        """Position size for one signal, or a rejection with the reason."""
        if self.in_cooldown(now):
            return SizingDecision(False, 0, 0.0, "tactical cooldown active after 3 consecutive stops")

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
        actual_risk = quantity * risk_per_unit

        if actual_risk > self.remaining_budget:
            return SizingDecision(
                False, 0, actual_risk,
                f"would exceed tactical bucket: {actual_risk:.0f} > "
                f"{self.remaining_budget:.0f} remaining of "
                f"{self.total_risk_budget:.0f}",
            )

        return SizingDecision(
            True, quantity, actual_risk, "approved",
            notional=quantity * signal.entry_price,
        )

    def commit(self, decision: SizingDecision) -> None:
        """Book an approved trade's risk against the bucket."""
        if decision.approved:
            self.open_risk += decision.risk_amount
