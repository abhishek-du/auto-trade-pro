"""Per-stock options enrichment for the Master Intelligence Hub.

The hub's options factor (`OptionsContext.score_for`) uses a stock's OWN PCR /
IV-skew when `OptionContractSnapshot` rows exist for it, otherwise it falls back
to the index-wide NIFTY PCR (a flat, market-level nudge). Until now nothing
populated the per-stock tables, so every equity — DIXON included — used the
NIFTY fallback and scored 0 whenever NIFTY PCR sat in the neutral 0.7–1.3 band.

This module fills that gap. For the F&O-eligible subset of the hub universe it:
  1. resolves the nearest-expiry CE/PE strikes from the KiteInstrument NFO master,
  2. fetches OI + LTP for the near-ATM strikes via the Kite quote API,
  3. persists OptionsChainSnapshot (per-stock PCR / max-pain) and, via the shared
     `compute_and_persist_greeks`, OptionContractSnapshot + IVHistory (IV-skew),

after which `_populate_symbol_options` picks the data up automatically and the
hub scores each stock on its own options positioning.

Data source: Kite quote (reliable, batch-friendly). Requires a live Kite token
and the NFO instrument master synced (gated by ENABLE_HUB_OPTIONS).

Public API
----------
get_fno_underlyings(session)            -> set[str]   # bare symbols with NFO options
enrich_equity_options(session, symbols) -> dict       # orchestrator (per run)
"""

from __future__ import annotations

import asyncio
import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crawler.options_chain import (
    calculate_max_pain,
    compute_and_persist_greeks,
    get_support_resistance_from_oi,
)
from crawler.zerodha_client import get_kite_client
from db.models import KiteInstrument, OptionsChainSnapshot
from engine.fno.contracts import _MIN_DTE, _parse_expiry
from utils.config import settings
from utils.logger import logger

# Kite quote allows ~500 instruments/call and ~3 req/s. Throttle between
# per-stock quote calls to stay comfortably under the rate limit.
_THROTTLE_SECS = 0.4
_LTP_CHUNK = 200


def _bare(symbol: str) -> str:
    return symbol.upper().replace(".NS", "").replace(".BO", "").strip()


async def get_fno_underlyings(session: AsyncSession) -> set[str]:
    """Bare symbols that have equity options on NFO (the F&O-eligibility list)."""
    names = (await session.execute(
        select(KiteInstrument.name).where(
            KiteInstrument.exchange == "NFO",
            KiteInstrument.instrument_type.in_(("CE", "PE")),
        ).distinct()
    )).scalars().all()
    return {str(n).upper() for n in names if n}


def _nearest_expiry(rows: list[KiteInstrument]) -> datetime.date | None:
    """Nearest tradeable expiry (≥ _MIN_DTE) across the underlying's option rows."""
    today = datetime.date.today()
    expiries = set()
    for r in rows:
        ex = _parse_expiry(r.expiry)
        if ex and (ex - today).days >= _MIN_DTE:
            expiries.add(ex)
    return min(expiries) if expiries else None


def _window_strikes(strikes: list[float], spot: float, window: int) -> set[float]:
    """The `window` strikes either side of the ATM strike (inclusive of ATM)."""
    uniq = sorted(set(strikes))
    if not uniq:
        return set()
    atm_idx = min(range(len(uniq)), key=lambda i: abs(uniq[i] - spot))
    lo = max(0, atm_idx - window)
    hi = min(len(uniq), atm_idx + window + 1)
    return set(uniq[lo:hi])


async def _build_chain_via_kite(
    bare: str,
    spot: float,
    session: AsyncSession,
    *,
    strike_window: int,
) -> dict | None:
    """Assemble a near-ATM option chain dict (compute_and_persist_greeks shape)."""
    if spot <= 0:
        return None
    kite = get_kite_client()

    rows = (await session.execute(
        select(KiteInstrument).where(
            KiteInstrument.exchange == "NFO",
            KiteInstrument.name == bare,
            KiteInstrument.instrument_type.in_(("CE", "PE")),
        )
    )).scalars().all()
    if not rows:
        return None

    expiry = _nearest_expiry(rows)
    if expiry is None:
        return None
    exp_rows = [r for r in rows if _parse_expiry(r.expiry) == expiry]

    keep = _window_strikes([r.strike for r in exp_rows], spot, strike_window)
    exp_rows = [r for r in exp_rows if r.strike in keep and r.strike > 0]
    if not exp_rows:
        return None

    # Batch quote for OI + LTP across the windowed strikes (CE + PE).
    keys = [f"NFO:{r.tradingsymbol}" for r in exp_rows]
    try:
        quotes = await kite.get_quote(keys)
    except Exception as exc:
        logger.debug(f"[hub_options] {bare}: quote failed: {exc}")
        return None

    rows_by_strike: dict[float, dict] = {}
    for r in exp_rows:
        q = quotes.get(f"NFO:{r.tradingsymbol}")
        if not q:
            continue
        rec = rows_by_strike.setdefault(r.strike, {
            "strike_price": r.strike, "expiry_date": expiry,
            "call_oi": 0, "put_oi": 0, "call_oi_change": 0, "put_oi_change": 0,
            "call_ltp": 0.0, "put_ltp": 0.0,
        })
        oi = int(q.get("oi") or 0)
        ltp = float(q.get("last_price") or 0.0)
        if r.instrument_type == "CE":
            rec["call_oi"], rec["call_ltp"] = oi, ltp
        else:
            rec["put_oi"], rec["put_ltp"] = oi, ltp

    options_data = list(rows_by_strike.values())
    if not options_data:
        return None

    total_call_oi = sum(rec["call_oi"] for rec in options_data)
    total_put_oi = sum(rec["put_oi"] for rec in options_data)
    return {
        "symbol": bare,
        "expiry_date": expiry,
        "spot_price": spot,
        "options_data": options_data,
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
    }


async def _fetch_spots(bares: list[str]) -> dict[str, float]:
    """Underlying spot via a batched Kite LTP call (NSE cash)."""
    kite = get_kite_client()
    spots: dict[str, float] = {}
    for i in range(0, len(bares), _LTP_CHUNK):
        chunk = bares[i:i + _LTP_CHUNK]
        try:
            data = await kite.get_ltp([f"NSE:{b}" for b in chunk])
        except Exception as exc:
            logger.debug(f"[hub_options] LTP chunk failed: {exc}")
            continue
        for b in chunk:
            d = data.get(f"NSE:{b}")
            if d:
                spots[b] = float(d.get("last_price") or 0.0)
    return spots


async def enrich_equity_options(
    session: AsyncSession,
    symbols: list[str],
    *,
    max_symbols: int | None = None,
    strike_window: int | None = None,
) -> dict:
    """Fetch + persist per-stock option data for F&O ∩ `symbols` (hub universe).

    Caller is responsible for committing the session. Best-effort per symbol —
    one bad chain never aborts the run. Returns a summary dict.
    """
    max_symbols = max_symbols or settings.HUB_OPTIONS_MAX_SYMBOLS
    strike_window = strike_window or settings.HUB_OPTIONS_STRIKE_WINDOW

    kite = get_kite_client()
    if not kite.access_token:
        return {"status": "no_kite_token", "enriched": 0}

    fno = await get_fno_underlyings(session)
    if not fno:
        return {"status": "no_nfo_master", "enriched": 0,
                "hint": "ENABLE_HUB_OPTIONS=True and let the daily instrument sync run"}

    targets = [b for b in (_bare(s) for s in symbols) if b in fno]
    # Stable order, de-duped, capped.
    seen: set[str] = set()
    targets = [b for b in targets if not (b in seen or seen.add(b))][:max_symbols]
    if not targets:
        return {"status": "no_targets", "enriched": 0, "fno_universe": len(fno)}

    spots = await _fetch_spots(targets)

    enriched = 0
    errors = 0
    for bare in targets:
        spot = spots.get(bare, 0.0)
        try:
            chain = await _build_chain_via_kite(bare, spot, session, strike_window=strike_window)
            if not chain or not chain["options_data"]:
                continue

            call_oi, put_oi = chain["total_call_oi"], chain["total_put_oi"]
            pcr = round(put_oi / call_oi, 4) if call_oi > 0 else 0.0
            max_pain = calculate_max_pain(chain["options_data"], spot)
            levels = get_support_resistance_from_oi(chain["options_data"], spot)
            atm_strike = min(
                (r["strike_price"] for r in chain["options_data"] if r["strike_price"]),
                key=lambda k: abs(k - spot), default=spot,
            )

            session.add(OptionsChainSnapshot(
                symbol=bare,
                expiry_date=chain["expiry_date"],
                atm_strike=atm_strike,
                pcr=pcr,
                max_pain=max_pain,
                total_call_oi=call_oi,
                total_put_oi=put_oi,
                support_levels=levels["support"],
                resistance_levels=levels["resistance"],
            ))
            await session.flush()

            # OptionContractSnapshot + IVHistory (this is what the hub reads).
            atm_iv = await compute_and_persist_greeks(bare, chain, session)
            enriched += 1
            logger.info(
                f"[hub_options] {bare:<12} pcr={pcr:.3f} max_pain={max_pain:,.0f} "
                f"strikes={len(chain['options_data'])}"
                + (f" atm_iv={atm_iv:.1%}" if atm_iv else "")
            )
        except Exception as exc:
            errors += 1
            logger.debug(f"[hub_options] {bare}: enrich failed: {exc}")
        await asyncio.sleep(_THROTTLE_SECS)

    return {
        "status": "ok",
        "enriched": enriched,
        "errors": errors,
        "targets": len(targets),
        "fno_universe": len(fno),
    }
