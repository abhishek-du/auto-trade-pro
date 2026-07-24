"""Phase 5: Replay / validation harness.

For each historical scheduled event, freeze a prediction at T-30/15/5/1 trading
days before it (the engine already only sees data at that `as_of`), then measure
the REALIZED outcome at T+1/3/5/10/20 after it. This is the evidence that must
exist before any paper/live wiring — "do not claim success from post-event
analysis; every prediction is frozen before the event."

Anti-lookahead is structural, not incidental:
  * The PREDICTION at a cutoff is produced by PreEventExpectationGapEngine.predict
    with as_of = cutoff — the point-in-time snapshot forbids reading any candle
    at/after the cutoff, and the sector adapter only uses quarters whose results
    were public by the cutoff.
  * The OUTCOME reads (entry at cutoff, exits after the event) are a SEPARATE
    pass that never feeds back into the prediction.

KNOWN, DOCUMENTED CAVEATS (surfaced in the report, never hidden):
  * The nowcast's fundamentals come from Upstox's CURRENT statements filtered by
    quarter-availability — this excludes quarters not yet reported at the cutoff
    (good) but cannot detect later RESTATEMENTS of older quarters (a small
    residual look-ahead risk on the fundamental leg only; price/discount/RS legs
    are fully point-in-time).
  * Historical MarketEvent coverage for company results may be thin — sample
    size is reported transparently and gates the verdict.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from utils.logger import logger
from db.models import Candle
from engine.pre_event_expectation_gap.types import (
    ScheduledEvent, PreEventType, PreEventDecision,
)
from engine.pre_event_expectation_gap.point_in_time import NIFTY_SYMBOL
from engine.pre_event_expectation_gap.engine import PreEventExpectationGapEngine

CUTOFF_OFFSETS   = (30, 15, 5, 1)          # trading-ish days before the event
REACTION_WINDOWS = (1, 3, 5, 10, 20)       # calendar days after the event
_MIN_SAMPLE_FOR_VERDICT = 20

# Approximate round-trip cost for delivery equity (fractional): slippage +
# brokerage both sides + STT (sell) + exchange/stamp. ~0.28%.
_ROUND_TRIP_COST = 0.0028


@dataclass
class OutcomeRecord:
    cutoff_offset:  int
    as_of:          datetime
    decision:       str
    entry_price:    float | None
    pre_event_score: float
    by_window:      dict = field(default_factory=dict)   # "t+3" -> {gross, net, nifty_adj, sector_adj, mfe, mae}


async def _close_near(symbol: str, target: date, session: AsyncSession, tol: int = 4) -> float | None:
    lo = datetime.combine(target - timedelta(days=tol), datetime.min.time())
    hi = datetime.combine(target + timedelta(days=tol), datetime.max.time())
    rows = (await session.execute(
        select(Candle).where(
            Candle.symbol == symbol, Candle.timeframe == "1d",
            Candle.timestamp >= lo, Candle.timestamp <= hi,
        ).order_by(Candle.timestamp.asc())
    )).scalars().all()
    if not rows:
        return None
    return min(rows, key=lambda r: abs((r.timestamp.date() - target).days)).close


async def _candles_between(symbol: str, start: date, end: date, session: AsyncSession) -> list:
    lo = datetime.combine(start, datetime.min.time())
    hi = datetime.combine(end, datetime.max.time())
    return (await session.execute(
        select(Candle).where(
            Candle.symbol == symbol, Candle.timeframe == "1d",
            Candle.timestamp >= lo, Candle.timestamp <= hi,
        ).order_by(Candle.timestamp.asc())
    )).scalars().all()


def _window_return(entry: float, exit_close: float | None) -> float | None:
    if entry is None or exit_close is None or entry <= 0:
        return None
    return (exit_close - entry) / entry


async def evaluate_outcome(
    symbol: str, event_date: date, as_of: datetime, entry_price: float | None,
    sector_index: str | None, session: AsyncSession,
) -> dict:
    """Realized returns (gross, net-of-cost, Nifty-adjusted, sector-adjusted) plus
    MFE/MAE for each reaction window, holding from the cutoff entry THROUGH the
    event. Returns {} silently when prices are unavailable."""
    if entry_price is None or entry_price <= 0:
        return {}
    nifty_entry  = await _close_near(NIFTY_SYMBOL, as_of.date(), session)
    sector_entry = await _close_near(sector_index, as_of.date(), session) if sector_index else None

    out: dict = {}
    for w in REACTION_WINDOWS:
        exit_date = event_date + timedelta(days=w)
        exit_close = await _close_near(symbol, exit_date, session)
        gross = _window_return(entry_price, exit_close)
        if gross is None:
            continue
        net = gross - _ROUND_TRIP_COST
        rec = {"gross": round(gross, 4), "net": round(net, 4)}

        nifty_exit = await _close_near(NIFTY_SYMBOL, exit_date, session)
        nifty_ret = _window_return(nifty_entry, nifty_exit) if nifty_entry else None
        if nifty_ret is not None:
            rec["nifty_adj"] = round(net - nifty_ret, 4)
        sector_exit = await _close_near(sector_index, exit_date, session) if sector_index else None
        sector_ret = _window_return(sector_entry, sector_exit) if sector_entry else None
        if sector_ret is not None:
            rec["sector_adj"] = round(net - sector_ret, 4)

        # MFE/MAE over the hold (entry → exit), from daily highs/lows.
        bars = await _candles_between(symbol, as_of.date(), exit_date, session)
        if bars:
            highs = [b.high for b in bars if getattr(b, "high", None)]
            lows = [b.low for b in bars if getattr(b, "low", None)]
            if highs:
                rec["mfe"] = round((max(highs) - entry_price) / entry_price, 4)
            if lows:
                rec["mae"] = round((min(lows) - entry_price) / entry_price, 4)
        out[f"t+{w}"] = rec
    return out


async def replay_event(
    engine: PreEventExpectationGapEngine, symbol: str, event_date: date,
    event_type: PreEventType, session: AsyncSession, *, sector_index: str | None = None,
) -> list[OutcomeRecord]:
    """Freeze a prediction at each cutoff and measure its realized outcome."""
    records: list[OutcomeRecord] = []
    for offset in CUTOFF_OFFSETS:
        as_of = datetime.combine(event_date - timedelta(days=offset), datetime.min.time())
        ev = ScheduledEvent(symbol=symbol, event_type=event_type, event_date=event_date,
                            event_confidence=0.9, source="replay")
        try:
            pred = await engine.predict(symbol, ev, as_of, session)
        except Exception as exc:
            logger.debug(f"[pre_event_gap/replay] predict failed {symbol}@{event_date}-{offset}d: {exc}")
            continue
        entry = await _close_near(symbol, as_of.date(), session)
        by_window = await evaluate_outcome(symbol, event_date, as_of, entry, sector_index, session)
        records.append(OutcomeRecord(
            cutoff_offset=offset, as_of=as_of, decision=pred.decision.value,
            entry_price=entry, pre_event_score=pred.pre_event_score, by_window=by_window,
        ))
    return records


def _summarize(records: list[OutcomeRecord], *, decision_filter: str = PreEventDecision.LONG.value) -> dict:
    """Aggregate outcomes for records matching `decision_filter`, sliced by
    cutoff and reaction window."""
    matched = [r for r in records if r.decision == decision_filter and r.by_window]
    by_cutoff: dict[str, dict] = {}
    for offset in CUTOFF_OFFSETS:
        recs = [r for r in matched if r.cutoff_offset == offset]
        windows: dict[str, dict] = {}
        for w in REACTION_WINDOWS:
            key = f"t+{w}"
            nifty_adj = [r.by_window[key]["nifty_adj"] for r in recs
                         if key in r.by_window and "nifty_adj" in r.by_window[key]]
            nets = [r.by_window[key]["net"] for r in recs if key in r.by_window]
            if not nets:
                continue
            windows[key] = {
                "n": len(nets),
                "hit_rate_nifty_adj": (round(sum(1 for x in nifty_adj if x > 0) / len(nifty_adj), 3)
                                       if nifty_adj else None),
                "mean_net": round(statistics.mean(nets), 4),
                "mean_nifty_adj": round(statistics.mean(nifty_adj), 4) if nifty_adj else None,
            }
        by_cutoff[f"T-{offset}"] = {"n_long": len(recs), "windows": windows}
    return {"decision": decision_filter, "n_matched": len(matched), "by_cutoff": by_cutoff}


def compute_replay_verdict(report: dict) -> dict:
    """Edge verdict in the project's established style (mirrors
    scripts/validate_edge.py::compute_verdict). Primary bucket: LONG predictions
    at T-1 cutoff, T+3 reaction window, Nifty-adjusted."""
    long_summary = report.get("long_summary", {})
    n_total = long_summary.get("n_matched", 0)
    if n_total < _MIN_SAMPLE_FOR_VERDICT:
        return {
            "edge_status": "INSUFFICIENT SAMPLE",
            "statement": (f"Only {n_total} LONG predictions across history — need "
                          f"{_MIN_SAMPLE_FOR_VERDICT}+ for a meaningful verdict. Backfill more "
                          f"historical events/candles, or widen the universe/date range."),
            "recommendation": "Do NOT use real money. Gather more evidence before drawing conclusions.",
        }

    primary = (long_summary.get("by_cutoff", {}).get("T-1", {}).get("windows", {}).get("t+3", {}))
    hit = primary.get("hit_rate_nifty_adj")
    mean_adj = primary.get("mean_nifty_adj")
    n_primary = primary.get("n", 0)

    if hit is not None and n_primary >= _MIN_SAMPLE_FOR_VERDICT and hit >= 0.55 and (mean_adj or 0) > 0:
        status = "EDGE CONFIRMED"
        rec = "Proceed to extended paper-mode observation (still no live capital). Re-validate quarterly."
    elif hit is not None and hit >= 0.50:
        status = "EDGE UNCERTAIN"
        rec = "Do NOT use real money. Extend the sample/window and re-run before reconsidering."
    else:
        status = "NO EDGE"
        rec = "Do NOT use real money. LONG predictions do not beat Nifty on the primary bucket."

    return {
        "edge_status": status,
        "statement": (f"T-1 LONG predictions, T+3 reaction, Nifty-adjusted: hit_rate={hit}, "
                      f"mean={mean_adj}, n={n_primary} (total LONG={n_total})."),
        "recommendation": rec,
        "caveats": [
            "Fundamental leg is not fully point-in-time — cannot detect later restatements of old quarters.",
            "Price/discount/relative-strength legs ARE point-in-time-safe.",
            "Verdict is only as good as historical event + candle coverage (see sample sizes).",
        ],
    }


async def replay_events(
    engine: PreEventExpectationGapEngine,
    events: list[tuple[str, date, PreEventType]],
    session: AsyncSession,
) -> dict:
    """Replay a list of (symbol, event_date, event_type) and build the report.
    `events` is supplied by the caller (e.g. from historical MarketEvent rows)."""
    from engine.pre_event_expectation_gap.point_in_time import build_snapshot
    all_records: list[OutcomeRecord] = []
    for symbol, ev_date, ev_type in events:
        sector_index = build_snapshot(symbol, datetime.utcnow(), session).sector_index_symbol()
        try:
            all_records.extend(await replay_event(engine, symbol, ev_date, ev_type, session,
                                                   sector_index=sector_index))
        except Exception as exc:
            logger.warning(f"[pre_event_gap/replay] event {symbol}@{ev_date} failed: {exc}")

    report = {
        "generated_at": datetime.utcnow().isoformat(),
        "events_replayed": len(events),
        "records": len(all_records),
        "decision_distribution": _decision_distribution(all_records),
        "long_summary": _summarize(all_records, decision_filter=PreEventDecision.LONG.value),
        "wait_summary": _summarize(all_records, decision_filter=PreEventDecision.WAIT.value),
    }
    report["verdict"] = compute_replay_verdict(report)
    return report


def _decision_distribution(records: list[OutcomeRecord]) -> dict:
    dist: dict[str, int] = {}
    for r in records:
        dist[r.decision] = dist.get(r.decision, 0) + 1
    return dist
