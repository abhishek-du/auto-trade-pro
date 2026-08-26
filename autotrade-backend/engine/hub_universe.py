"""Hub universe management — the configurable set of symbols the Master
Intelligence Hub deep-scores (7-factor) each cycle.

Resolution priority (get_hub_universe):
  1. settings.HUB_SYMBOLS env (comma-separated) — manual override
  2. hub_universe DB table (top-N by turnover, rebuilt daily)
  3. settings.nse_symbols — legacy hardcoded fallback (cold start)

rebuild_hub_universe() ranks all NSE equities by average daily turnover
(₹ volume × close over the last 30 days), excludes bonds/SME/illiquid names,
and writes the top-N to the hub_universe table.
"""
from __future__ import annotations

from sqlalchemy import select, delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from utils.config import settings
from utils.logger import logger


async def rebuild_hub_universe(
    session: AsyncSession,
    *,
    top_n: int = 20000,
    min_turnover_cr: float = 0.0,
    fast_lane_min_turnover_cr: float = 5.0,
) -> dict:
    """Rebuild the hub_universe table: top-N NSE equities by 30-day avg turnover.

    Threshold lowered from ₹5Cr → ₹1Cr to include small-caps:
    JTEKTINDIA (~₹4Cr), SAKSOFT (~₹4.5Cr), SIGNPOST (~₹3Cr) now qualify.
    Universe expanded from 1,500 → 3,000 to cover the wider small-cap space.

    Excludes government bonds / debt (numeric or -SG names) and anything below
    `min_turnover_cr` (₹ Cr/day). Returns a summary dict.
    """
    from db.models import HubUniverse

    min_turnover = min_turnover_cr * 1e7  # ₹ Cr → ₹

    # Include both NSE (.NS) and BSE (.BO); exclude bonds/SME/illiquid.
    _exclude = """
        AND (symbol LIKE '%.NS' OR symbol LIKE '%.BO')
        AND symbol !~ '[0-9]'
        AND symbol NOT LIKE '%-SG.NS'
        AND symbol NOT LIKE '%-SM.NS'
        AND symbol NOT LIKE '%-ST.NS'
        AND symbol NOT LIKE '%-BE.NS'
        AND symbol NOT LIKE '%-BZ.NS'
        AND symbol NOT LIKE '%-SG.BO'
        AND symbol NOT LIKE '%-SM.BO'
    """

    # Primary: 1d candles (most accurate for daily turnover).
    #
    # The HAVING clause's second condition excludes NaN turnover (corrupted
    # candle data — e.g. a bad close/volume write during a corporate action)
    # explicitly: PostgreSQL's float sort/comparison semantics treat NaN as
    # EQUAL to itself and GREATER than every other value (unlike IEEE-754/
    # most languages), so a plain `ORDER BY turnover DESC` puts a NaN-turnover
    # symbol at rank #1 ahead of even the largest genuine mega-cap turnover —
    # confirmed live (THELEELA.NS/LTFOODS.NS outranking HDFCBANK.NS). Because
    # NaN = NaN is TRUE in Postgres, `<> 'NaN'` correctly evaluates to FALSE
    # for a NaN value and excludes it — this is the standard Postgres-idiomatic
    # NaN guard, not a generic float-equality check.
    rows = (await session.execute(text(f"""
        SELECT symbol, AVG(volume * close) AS turnover
        FROM candles
        WHERE timeframe = '1d'
          AND timestamp > NOW() - INTERVAL '30 days'
          {_exclude}
        GROUP BY symbol
        HAVING AVG(volume * close) >= :min_t
           AND AVG(volume * close) <> 'NaN'
        ORDER BY turnover DESC
        LIMIT :n
    """), {"min_t": min_turnover, "n": top_n})).all()

    # Fallback: when 1d candles are absent (pre-backfill cold start), use 1h
    # bars aggregated to a daily-equivalent turnover estimate.  A trading day
    # has ~6.25 NSE hours, so summing 1h volume*close gives a comparable figure.
    if not rows:
        logger.info("[hub_universe] no 1d candles — falling back to 1h for turnover ranking")
        rows = (await session.execute(text(f"""
            SELECT symbol,
                   AVG(daily_turnover) AS turnover
            FROM (
                SELECT symbol,
                       DATE(timestamp) AS day,
                       SUM(volume * close) AS daily_turnover
                FROM candles
                WHERE timeframe = '1h'
                  AND timestamp > NOW() - INTERVAL '30 days'
                  {_exclude}
                GROUP BY symbol, DATE(timestamp)
            ) daily
            GROUP BY symbol
            HAVING AVG(daily_turnover) >= :min_t
               AND AVG(daily_turnover) <> 'NaN'
            ORDER BY turnover DESC
            LIMIT :n
        """), {"min_t": min_turnover, "n": top_n})).all()

    # ── Fast lane: yesterday's movers ────────────────────────────────────────
    # A 30-day average is structurally blind to the stock that wakes up. On
    # 24 Aug MARATHON traded Rs 135cr and MAXESTATES Rs 108cr — 170x and 123x
    # their own 30-day averages of Rs 0.79cr and Rs 0.88cr, both far under the
    # Rs 5cr bar, so neither was in the universe and neither could be scanned.
    # 22 of the day's 107 big movers were main-board names in exactly this
    # position.
    #
    # The average is the right primary ranking — it is what "normally liquid"
    # means — but it must not be the only door. A symbol that actually traded
    # real money in the most recent session is liquid enough to scan today,
    # whatever its trailing mean says. Measured against 24 Aug's tape, a Rs 5cr
    # last-session floor admits 58 extra symbols (~2% more universe) and
    # recovers 15 of the 38 missed movers.
    #
    # This is inherently one session behind — nothing can know today's turnover
    # before today. That is acceptable because these moves are thematic and run
    # for days (the sugar and rice packs ran all week), so being in from day two
    # still catches most of the move.
    #
    # Per symbol, the best single session in the recent window — NOT a single
    # global "latest timestamp". A global MAX(timestamp) picks whichever symbol
    # was written most recently (here the 33-name Kite watchlist, whose bars
    # land at 10:00 UTC while the full universe lands the next morning), so the
    # fast lane would only ever consider those 33. Asking each symbol for its
    # own best recent day is what actually finds the stock that woke up.
    fast_rows = (await session.execute(text(f"""
        SELECT symbol, MAX(volume * close) AS turnover
        FROM candles
        WHERE timeframe = '1d'
          AND timestamp > NOW() - INTERVAL '4 days'
          {_exclude}
        GROUP BY symbol
        HAVING MAX(volume * close) >= :fast_t
           AND MAX(volume * close) <> 'NaN'
    """), {"fast_t": fast_lane_min_turnover_cr * 1e7})).all()

    # NOTE — there is deliberately no "new listing" clause here.
    #
    # LALITHAA listed on 24 Aug and did Rs 2,459cr, the largest turnover on the
    # exchange, yet was absent from the universe. The tempting fix is to admit
    # symbols whose first bar is recent. It is redundant: a fresh listing has
    # only a handful of bars, so its 30-day AVERAGE is simply its own recent
    # turnover, and a name trading that kind of money ranks at the top on the
    # ordinary path the moment it has a daily bar at all. A first draft of that
    # clause was measured to admit nothing the ranking did not already admit.
    #
    # LALITHAA's real problem is upstream and is NOT solved here: on listing day
    # it had zero 1d rows (and zero 1m rows), so there was nothing to rank. Full
    # NSE daily coverage is refreshed weekly (`full-nse-candles-weekly`, Sunday
    # 01:00), which is what a newly listed symbol has to wait for. Making new
    # listings tradeable sooner means fixing that cadence, not adding a second
    # door into this query.
    ranked = {r.symbol for r in rows}
    extra = [r for r in fast_rows if r.symbol not in ranked]

    await session.execute(delete(HubUniverse))
    for rank, r in enumerate(rows, start=1):
        # Swing mode: stocks ranked 50-1500 are swing candidates.
        # Top-49 are index heavyweights (intraday/positional dominated).
        # Breakout-injected stocks are also set is_swing=True by the screener.
        # Zerodha Varsity: swing works best on liquid mid/large-caps → rank 50-1500.
        session.add(HubUniverse(
            symbol=r.symbol,
            turnover_cr=round(float(r.turnover) / 1e7, 2),
            rank=rank,
            is_swing=True,
        ))

    # Fast-lane and new-listing entries rank after the turnover-ranked block.
    # They are in the universe so they get scanned, not because they are more
    # liquid than the names above them.
    for offset, r in enumerate(extra, start=1):
        session.add(HubUniverse(
            symbol=r.symbol,
            turnover_cr=round(float(r.turnover) / 1e7, 2),
            rank=len(rows) + offset,
            is_swing=True,
        ))
    await session.commit()

    # Snapshot the universe before it is next destroyed (2026-08-26, phase 21).
    #
    # `delete(HubUniverse)` above rewrites this table wholesale on every
    # rebuild, so only the CURRENT universe is ever knowable. The 2026-08-26
    # opportunity audit hit exactly this wall: asked "was symbol X in the
    # universe on 2026-08-14", the answer was unrecoverable, which made a
    # historical opportunity-conversion funnel impossible to build for any day
    # but today.
    #
    # Written into simulation_logs rather than a new table so this needs no
    # schema change and no migration: one row per rebuild, symbol -> rank only.
    # Failure here must never break the rebuild — the universe is already
    # committed above, and a missing snapshot is a lost measurement, not a
    # trading fault.
    try:
        from db.models import SimulationLog as _SimLog
        _snap = {r.symbol: i for i, r in enumerate(rows, start=1)}
        _snap.update({r.symbol: len(rows) + i for i, r in enumerate(extra, start=1)})
        session.add(_SimLog(
            event_type="HUB_UNIVERSE_SNAPSHOT",
            symbol="—",
            message=f"universe rebuilt: {len(_snap)} symbols",
            data={
                "universe_size": len(_snap),
                "ranked": len(rows),
                "fast_lane": len(extra),
                "min_turnover_cr": min_turnover_cr,
                "ranks": _snap,
            },
        ))
        await session.commit()
    except Exception as exc:
        await session.rollback()
        logger.warning(f"[hub_universe] snapshot failed (universe itself is committed): {exc}")

    summary = {
        "universe_size": len(rows) + len(extra),
        "ranked": len(rows),
        "fast_lane": len(extra),
        "min_turnover_cr": min_turnover_cr,
        "top": [r.symbol.replace(".NS", "").replace(".BO", "") for r in rows[:5]],
    }
    logger.info(f"[hub_universe] rebuilt → {summary}")
    return summary


async def get_hub_universe(session: AsyncSession) -> list[str]:
    """Resolve the active Hub universe (list of '.NS' / '.BO' symbols)."""
    # 1. Manual env override
    env_syms = (getattr(settings, "HUB_SYMBOLS", "") or "").strip()
    if env_syms:
        syms = [s.strip() for s in env_syms.split(",") if s.strip()]
        # Preserve explicit suffix; default bare names to .NS
        return [s if (s.endswith(".NS") or s.endswith(".BO")) else f"{s}.NS" for s in syms]

    # 2. hub_universe DB table (top-N by turnover)
    from db.models import HubUniverse
    rows = (await session.execute(
        select(HubUniverse.symbol).order_by(HubUniverse.rank)
    )).scalars().all()
    if rows:
        return list(rows)

    # 3. Legacy fallback — include BSE watchlist alongside NSE
    logger.warning("[hub_universe] empty — falling back to settings watchlists")
    return settings.nse_symbols + settings.bse_symbols
