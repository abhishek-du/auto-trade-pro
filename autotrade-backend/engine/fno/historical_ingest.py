"""Persist historical F&O bhavcopy rows into OptionContractSnapshot + IVHistory.

Phase 2 of the backfill. Takes the normalised `BhavContract` rows from
`crawler.bhavcopy_fno` for a single trading date and writes them as point-in-time
option snapshots — computing IV + Greeks *as of that date* (not today), which is
the key difference from the live writer in `crawler.options_chain`.

Design notes
------------
* snapshot_at is set to a canonical instant for the day — `trade_date` 10:00 UTC
  (≈ 15:30 IST close) — so the engine's "latest snapshot batch" (max snapshot_at)
  groups cleanly by day, and the unique key (uq_option_snapshot) makes the insert
  idempotent / re-runnable.
* Time-to-expiry uses `(expiry - trade_date)`, so theta/IV reflect the real DTE on
  that historical bar.
* Spot: UDiFF rows carry it; legacy rows don't, so we derive it from the index 1d
  candle for that date, falling back to the near-month future's settlement price.
* Premium = settlement price (stable for illiquid strikes, matches daily MTM).

Public API
----------
persist_bhavcopy(session, rows, trade_date, *, commit=True) -> IngestSummary
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from crawler.bhavcopy_fno import BhavContract
from db.models import Candle, IVHistory, OptionContractSnapshot
from engine.fno import options_pricing as _bs
from utils.config import settings
from utils.logger import logger

# Underlying → index spot candle. FINNIFTY has no clean candle series, so it
# falls back to its near-month future settlement (handled in _derive_spot).
_SPOT_CANDLE = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}

# Canonical intra-day instant for an EOD snapshot: 15:30 IST = 10:00 UTC.
_SNAPSHOT_UTC_TIME = datetime.time(10, 0, 0)

_INSERT_CHUNK = 1000


@dataclass(slots=True)
class IngestSummary:
    trade_date:   datetime.date
    rows_written: int
    iv_recorded:  int          # underlyings with an ATM-IV history row written
    skipped_no_spot: list[str]


def _snapshot_at(trade_date: datetime.date) -> datetime.datetime:
    return datetime.datetime.combine(trade_date, _SNAPSHOT_UTC_TIME)


async def _candle_close(symbol: str, trade_date: datetime.date,
                        session: AsyncSession) -> float | None:
    """Daily close for `symbol` on (or the latest before) `trade_date`."""
    row = (await session.execute(
        select(Candle.close).where(
            Candle.symbol == symbol,
            Candle.timeframe == "1d",
            func.date(Candle.timestamp) <= trade_date,
        ).order_by(Candle.timestamp.desc()).limit(1)
    )).scalar_one_or_none()
    return float(row) if row else None


async def _derive_spot(underlying: str, rows: list[BhavContract],
                       trade_date: datetime.date, session: AsyncSession) -> float | None:
    """Resolve the underlying spot for one index on a historical date.

    Order: UDiFF per-row spot → index 1d candle close → near-month future settle.
    """
    # 1. UDiFF carries the underlying price directly.
    for r in rows:
        if r.spot and r.spot > 0:
            return float(r.spot)
    # 2. Index candle close for that date.
    csym = _SPOT_CANDLE.get(underlying)
    if csym:
        close = await _candle_close(csym, trade_date, session)
        if close:
            return close
    # 3. Fallback: nearest-expiry future settlement (always present in the file).
    futs = [r for r in rows if r.instrument_type == "FUT" and r.settle > 0
            and r.expiry >= trade_date]
    if futs:
        return float(min(futs, key=lambda r: r.expiry).settle)
    return None


def _greeks(settle: float, spot: float, strike: float, dte: int, flag: str):
    """IV + Greeks from a settlement premium, or None when not derivable."""
    if settle <= 0 or spot <= 0 or strike <= 0:
        return None
    T = _bs.years_to_expiry(dte)
    if T <= 0:
        return None
    return _bs.greeks_from_price(settle, spot, strike, T, settings.RISK_FREE_RATE, flag)


async def persist_bhavcopy(
    session: AsyncSession,
    rows: list[BhavContract],
    trade_date: datetime.date,
    *,
    commit: bool = True,
) -> IngestSummary:
    """Write one day's bhavcopy option rows + ATM-IV history. Idempotent.

    Futures rows are used only for spot derivation, not persisted (the snapshot
    table is options-only). Re-running the same date is a no-op via ON CONFLICT.
    """
    snapshot_at = _snapshot_at(trade_date)
    # Group by underlying so spot is derived once per index.
    by_under: dict[str, list[BhavContract]] = {}
    for r in rows:
        by_under.setdefault(r.underlying, []).append(r)

    values: list[dict] = []
    iv_values: list[dict] = []
    skipped_no_spot: list[str] = []

    for under, urows in by_under.items():
        spot = await _derive_spot(under, urows, trade_date, session)
        if spot is None:
            skipped_no_spot.append(under)
            logger.warning(f"[bhav/persist] {trade_date} {under}: no spot — skipped")
            continue

        # Drop rows expiring on/before the bar: NSE writes the underlying
        # settlement value (≈ spot) into SETTLE_PR for the expiring series, so
        # their premium is corrupt — and the engine never enters < 2-DTE options
        # anyway. Keeping only future expiries also guarantees T > 0.
        opts = [r for r in urows
                if r.instrument_type in ("CE", "PE") and r.expiry > trade_date]
        if not opts:
            continue

        # ATM strike for the IV-history series.
        atm_strike = min((r.strike for r in opts), key=lambda k: abs(k - spot))
        atm_ce_iv = atm_pe_iv = None

        for r in opts:
            flag = "c" if r.instrument_type == "CE" else "p"
            dte = (r.expiry - trade_date).days
            g = _greeks(r.settle, spot, r.strike, dte, flag)
            values.append({
                "underlying": under, "expiry_date": r.expiry, "strike": r.strike,
                "option_type": r.instrument_type, "spot": spot, "ltp": r.settle,
                "oi": r.oi, "oi_change": r.oi_change, "volume": r.volume,
                "iv": round(g.iv, 4) if g else None,
                "delta": g.delta if g else None, "gamma": g.gamma if g else None,
                "theta": g.theta if g else None, "vega": g.vega if g else None,
                "rho": g.rho if g else None,
                "snapshot_at": snapshot_at,
            })
            if g and r.strike == atm_strike:
                if r.instrument_type == "CE":
                    atm_ce_iv = g.iv
                else:
                    atm_pe_iv = g.iv

        ivs = [v for v in (atm_ce_iv, atm_pe_iv) if v is not None]
        if ivs:
            iv_values.append({
                "underlying": under, "trade_date": trade_date,
                "atm_iv": round(sum(ivs) / len(ivs), 4),
            })

    # Bulk insert snapshots, ON CONFLICT DO NOTHING (idempotent re-runs).
    for i in range(0, len(values), _INSERT_CHUNK):
        chunk = values[i:i + _INSERT_CHUNK]
        await session.execute(
            pg_insert(OptionContractSnapshot)
            .values(chunk)
            .on_conflict_do_nothing(constraint="uq_option_snapshot")
        )

    # Upsert ATM-IV history (latest value for the day wins).
    for iv in iv_values:
        await session.execute(
            pg_insert(IVHistory)
            .values(iv)
            .on_conflict_do_update(
                constraint="uq_iv_history_under_date",
                set_={"atm_iv": iv["atm_iv"]},
            )
        )

    if commit:
        await session.commit()
    else:
        await session.flush()

    logger.info(f"[bhav/persist] {trade_date}: {len(values)} option rows, "
                f"{len(iv_values)} IV-history rows"
                + (f", skipped {skipped_no_spot}" if skipped_no_spot else ""))
    return IngestSummary(
        trade_date=trade_date, rows_written=len(values),
        iv_recorded=len(iv_values), skipped_no_spot=skipped_no_spot,
    )
