"""Exit taxonomy and the V2 minimum-hold gate.

WHY THIS EXISTS
---------------
Phase 24 measured, on 5,488 t0 opportunities across five sessions, that the
signalled subset is net -0.052% at 60 minutes, +0.054% at 120 minutes and
+0.342% held to the session close, and that the direction replicated on a
held-out session (-0.032% / +0.109% / +0.342%). The signal is not the problem;
the horizon may be. This module makes that testable without touching a single
signal rule.

WHAT IT IS NOT
--------------
It is NOT "disable exits". Hard risk protection and setup invalidation run in
both modes, unchanged, at every hold duration. The ONLY thing V2 changes is
that PROFIT_MANAGEMENT exits — the ones that close a position because it moved
favourably and then wobbled — are suppressed until the position has had
V2_MIN_HOLD_MINUTES to work.

THE TWO MODES
-------------
CONTROL  every exit fires exactly as it did before this module existed. This is
         the code default, so a process that cannot read .env gets the old
         behaviour, never the experiment.
V2       PROFIT_MANAGEMENT exits are suppressed before the minimum hold.
         Everything else is identical.

Rollback is one line of .env plus a restart. See docs/PHASE_25_IMPLEMENTATION_REPORT.md.

FAIL-SAFE DIRECTION
-------------------
Every ambiguity resolves toward *letting the exit happen*:
  * an unrecognised mode string      -> CONTROL
  * an unrecognised exit reason      -> CONTROL_EXISTING, never suppressed
  * a missing or unusable opened_at  -> allowed
  * any internal error               -> allowed (see exit_allowed)
Blocking an exit is the dangerous direction, so nothing but an explicit,
well-formed V2 + PROFIT_MANAGEMENT + inside-window decision can do it.
"""
from __future__ import annotations

import datetime as _dt

MODE_CONTROL = "CONTROL"
MODE_V2 = "V2"
_VALID_MODES = (MODE_CONTROL, MODE_V2)


class ExitFamily:
    """The six categories every exit is attributed to.

    Recorded in BOTH modes. The taxonomy is measurement; the gate is strategy.
    """

    HARD_STOP = "HARD_STOP"
    SETUP_INVALIDATION = "SETUP_INVALIDATION"
    PROFIT_MANAGEMENT = "PROFIT_MANAGEMENT"
    TIME_EXIT = "TIME_EXIT"
    MARKET_SQUAREOFF = "MARKET_SQUAREOFF"
    CONTROL_EXISTING = "CONTROL_EXISTING"


# Every exit reason the codebase actually emits, mapped to its family.
# Grepped from the ten close_paper_trade / scale_out_paper_trade call sites,
# not invented. A reason missing here is CONTROL_EXISTING and is never gated,
# so adding a new exit reason cannot accidentally be suppressed by V2.
_FAMILY_BY_REASON: dict[str, str] = {
    # ── Layer 1: the position is losing and the risk limit is reached ────────
    "STOP_LOSS": ExitFamily.HARD_STOP,
    "MARKET_SHOCK_FLATTEN": ExitFamily.HARD_STOP,
    # ── Layer 2: the reason for being in the trade stopped being true ────────
    "CONFIRMATION_LOST": ExitFamily.SETUP_INVALIDATION,
    "SECTOR_REVERSAL": ExitFamily.SETUP_INVALIDATION,
    "POST_EVENT_REVERSAL": ExitFamily.SETUP_INVALIDATION,
    "LLM_DYNAMIC_EXIT": ExitFamily.SETUP_INVALIDATION,
    # ── Layer 3: the position is winning and we are deciding when to bank it ─
    "TAKE_PROFIT": ExitFamily.PROFIT_MANAGEMENT,
    "TRAIL_STOP": ExitFamily.PROFIT_MANAGEMENT,
    "EXHAUSTION": ExitFamily.PROFIT_MANAGEMENT,
    "T1_REVERSAL_EXIT": ExitFamily.PROFIT_MANAGEMENT,
    "T1_HIT": ExitFamily.PROFIT_MANAGEMENT,
    "T2_HIT": ExitFamily.PROFIT_MANAGEMENT,
    # ── Layer 4 and the clock ────────────────────────────────────────────────
    "STALE_EXIT": ExitFamily.TIME_EXIT,
    "POST_EVENT_TIME_EXIT": ExitFamily.TIME_EXIT,
    "MIS_SQUAREOFF": ExitFamily.MARKET_SQUAREOFF,
    # ── Operator and housekeeping actions — outside the experiment ───────────
    "MANUAL": ExitFamily.CONTROL_EXISTING,
    "KILL_SWITCH": ExitFamily.CONTROL_EXISTING,
    "REALLOCATED": ExitFamily.CONTROL_EXISTING,
    "SIGNAL_REVERSAL": ExitFamily.CONTROL_EXISTING,
}

# The only family V2 defers. Named as a constant so the gate below reads as a
# statement of intent rather than a string comparison.
_GATED_FAMILY = ExitFamily.PROFIT_MANAGEMENT


def classify(reason: str | None) -> str:
    """Map an exit reason to its family. Unknown reasons are never gated."""
    if not reason:
        return ExitFamily.CONTROL_EXISTING
    return _FAMILY_BY_REASON.get(str(reason).strip().upper(), ExitFamily.CONTROL_EXISTING)


def strategy_mode() -> str:
    """The active mode, validated. Anything unrecognised reads as CONTROL."""
    try:
        from utils.config import settings

        raw = str(getattr(settings, "TRADING_STRATEGY_MODE", MODE_CONTROL) or "").strip().upper()
    except Exception:
        return MODE_CONTROL
    return raw if raw in _VALID_MODES else MODE_CONTROL


def is_v2() -> bool:
    return strategy_mode() == MODE_V2


def min_hold_minutes() -> float:
    """The V2 profit-management horizon, in minutes.

    Configurable so 60/90/120/150/180 can be tested without touching the exit
    engine. Phase 25 runs 120. A non-positive or unparseable value disables the
    gate entirely rather than defaulting to something arbitrary.
    """
    try:
        from utils.config import settings

        return float(getattr(settings, "V2_MIN_HOLD_MINUTES", 120) or 0)
    except Exception:
        return 0.0


def held_minutes(opened_at, now=None) -> float | None:
    """Minutes a position has been open, or None if it cannot be determined.

    Both opened_at and utcnow() are UTC-naive throughout this codebase
    (paper_trades.opened_at, candles.timestamp). A tz-aware value is normalised
    to naive UTC rather than rejected, because mixing the two raises and the
    fail-safe direction here is to produce a usable number.
    """
    if opened_at is None:
        return None
    now = now or _dt.datetime.utcnow()
    try:
        if getattr(opened_at, "tzinfo", None) is not None:
            opened_at = opened_at.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        if getattr(now, "tzinfo", None) is not None:
            now = now.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        return (now - opened_at).total_seconds() / 60.0
    except Exception:
        return None


def min_completed_bars() -> int:
    """Completed bars required before a PROFIT_MANAGEMENT exit may fire."""
    try:
        from utils.config import settings

        return int(getattr(settings, "MIN_COMPLETED_BARS_BEFORE_PROFIT_EXIT", 1) or 0)
    except Exception:
        return 0


def _bar_minutes() -> int:
    try:
        from utils.config import settings

        return int(getattr(settings, "PROFIT_EXIT_BAR_MINUTES", 5) or 5)
    except Exception:
        return 5


def completed_bars_since(opened_at, now=None) -> int | None:
    """How many bar BOUNDARIES have been crossed since entry.

    Counting boundaries, not elapsed minutes, is the point: a position opened at
    09:19:58 has seen the 09:15 bar complete by 09:20:00 even though only two
    seconds passed. Conversely one opened at 09:15:01 has seen nothing complete
    at 09:19:59. Elapsed time cannot distinguish those; bar alignment can.
    """
    if opened_at is None:
        return None
    now = now or _dt.datetime.utcnow()
    try:
        if getattr(opened_at, "tzinfo", None) is not None:
            opened_at = opened_at.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        if getattr(now, "tzinfo", None) is not None:
            now = now.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        m = _bar_minutes()
        if m <= 0:
            return None
        secs = m * 60
        o = int(opened_at.timestamp()) // secs
        n = int(now.timestamp()) // secs
        return max(0, n - o)
    except Exception:
        return None


def same_bar_block(reason: str | None, opened_at, now=None) -> tuple[bool, str]:
    """(blocked, note) for the same-bar protection. Independent of V2.

    Applies ONLY to PROFIT_MANAGEMENT. Hard stops and shock flattens are never
    delayed; setup invalidation is untouched because no same-bar case was
    measured there.
    """
    try:
        if classify(reason) != _GATED_FAMILY:
            return False, ""
        need = min_completed_bars()
        if need <= 0:
            return False, ""
        bars = completed_bars_since(opened_at, now)
        if bars is None or bars >= need:
            return False, ""
        return True, (
            f"same-bar protection: {bars} completed {_bar_minutes()}m bar(s) "
            f"since entry, need {need}"
        )
    except Exception:
        return False, ""


def exit_allowed(reason: str | None, opened_at, now=None) -> tuple[bool, str, str]:
    """May this exit fire right now?

    Returns (allowed, family, note).

    CONTROL always allows. V2 blocks only a PROFIT_MANAGEMENT exit on a
    position younger than the minimum hold. Every other combination — every
    hard stop, every invalidation, every squareoff, every exit at any age in
    CONTROL — is allowed.

    Never raises. An exception inside this function returns allowed=True,
    because a measurement bug must not be able to trap a position.
    """
    family = classify(reason)
    try:
        # Same-bar protection runs FIRST and in BOTH modes (Phase 27, F4).
        # It is not part of the V2 experiment: a rollback to CONTROL must not
        # reopen the hole that cost -Rs1,608 across eight same-bar EXHAUSTION
        # exits. Only PROFIT_MANAGEMENT is affected.
        blocked, note = same_bar_block(reason, opened_at, now)
        if blocked:
            return False, family, note

        if not is_v2():
            return True, family, ""
        if family != _GATED_FAMILY:
            return True, family, ""
        floor = min_hold_minutes()
        if floor <= 0:
            return True, family, ""
        held = held_minutes(opened_at, now)
        if held is None:
            # Cannot tell how old it is; do not trap it.
            return True, family, ""
        if held >= floor:
            return True, family, f"held {held:.0f}m >= {floor:.0f}m"
        return False, family, (
            f"V2: {family} deferred — held {held:.0f}m of {floor:.0f}m minimum"
        )
    except Exception:
        return True, family, ""


def profit_management_allowed(opened_at, now=None) -> bool:
    """Convenience for call sites deciding whether to even COMPUTE an exit.

    Skipping the computation is the same decision as blocking the exit, and it
    saves the 5-second loop a candle query plus an ATR per position.
    """
    allowed, _, _ = exit_allowed("TAKE_PROFIT", opened_at, now)
    return allowed


def describe() -> dict:
    """One-line state for logs and telemetry."""
    return {
        "mode": strategy_mode(),
        "min_hold_minutes": min_hold_minutes(),
        "gated_family": _GATED_FAMILY,
    }
