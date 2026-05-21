"""NSE options-chain analysis for NIFTY and BANKNIFTY.

Fetches strike-wise open interest from NSE using the same two-step
browser-session pattern required by all NSE JSON endpoints.

All NSE API calls use BROWSER_HEADERS imported from fii_dii_crawler so the
impersonation headers are defined in exactly one place.

Public API
----------
fetch_options_chain(symbol)          -> dict
calculate_pcr(options_data)          -> float
calculate_max_pain(options_data, spot) -> float
get_support_resistance_from_oi(options_data, spot) -> dict
calculate_options_score(pcr, max_pain, spot) -> float
run_options_analysis(session)        -> dict
"""

from __future__ import annotations

import asyncio
import datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from crawler.fii_dii_crawler import BROWSER_HEADERS
from db.models import OptionsChainSnapshot
from utils.logger import logger

# ── NSE endpoints ─────────────────────────────────────────────────────────────

_NSE_HOME = "https://www.nseindia.com"
_CHAIN_URL = "https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"

SUPPORTED_SYMBOLS = ("NIFTY", "BANKNIFTY")

# ── Internal helpers ──────────────────────────────────────────────────────────

def _to_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return default


def _to_int(value, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (ValueError, TypeError):
        return default


def _parse_expiry(value) -> datetime.date:
    """Parse an NSE expiry string into a Python date.

    NSE uses various formats across API versions:
      '22-May-2026', '22-05-2026', '2026-05-22'
    Falls back to today when parsing fails rather than raising.
    """
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.datetime):
        return value.date()
    raw = str(value or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y"):
        try:
            return datetime.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return datetime.date.today()


# ── Payload parser ────────────────────────────────────────────────────────────

def _parse_chain_payload(payload: dict) -> tuple[list[dict], float, int, int, datetime.date]:
    """Extract strike data, spot price, and aggregate OI from the NSE payload.

    NSE response structure (as of May 2026):
    {
      "records": {
        "data": [...],           # full strike list for all expiries
        "underlyingValue": 24500 # live spot price
      },
      "filtered": {
        "data": [...],           # strikes for the nearest expiry only
        "CE": {"totOI": ..., "totVol": ...},
        "PE": {"totOI": ..., "totVol": ...},
        "underlyingValue": 24500
      }
    }

    Returns (options_data, spot_price, total_call_oi, total_put_oi, expiry_date).
    """
    filtered = payload.get("filtered") or {}
    records  = payload.get("records")  or {}

    # Spot price: NSE provides it directly as a float under "underlyingValue"
    spot = _to_float(
        filtered.get("underlyingValue")
        or records.get("underlyingValue")
    )

    # Parse strike-by-strike rows from the nearest-expiry filtered data
    raw_rows = filtered.get("data") or []
    options_data: list[dict] = []
    first_expiry = None

    for row in raw_rows:
        if not isinstance(row, dict):
            continue

        ce = row.get("CE") or {}
        pe = row.get("PE") or {}

        expiry_raw = (
            row.get("expiryDate")
            or ce.get("expiryDate")
            or pe.get("expiryDate")
        )
        if expiry_raw and first_expiry is None:
            first_expiry = _parse_expiry(expiry_raw)

        options_data.append({
            "strike_price":    _to_float(row.get("strikePrice")),
            "expiry_date":     _parse_expiry(expiry_raw) if expiry_raw else datetime.date.today(),
            "call_oi":         _to_int(ce.get("openInterest")),
            "put_oi":          _to_int(pe.get("openInterest")),
            "call_oi_change":  _to_int(ce.get("changeinOpenInterest")),
            "put_oi_change":   _to_int(pe.get("changeinOpenInterest")),
            "call_ltp":        _to_float(ce.get("lastPrice")),
            "put_ltp":         _to_float(pe.get("lastPrice")),
        })

    # Aggregate OI: prefer pre-computed totals from NSE, fall back to summing rows
    total_call_oi = _to_int((filtered.get("CE") or {}).get("totOI"))
    total_put_oi  = _to_int((filtered.get("PE") or {}).get("totOI"))

    if total_call_oi == 0 and options_data:
        total_call_oi = sum(r["call_oi"] for r in options_data)
    if total_put_oi == 0 and options_data:
        total_put_oi  = sum(r["put_oi"]  for r in options_data)

    expiry_date = first_expiry or datetime.date.today()
    return options_data, spot, total_call_oi, total_put_oi, expiry_date


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Fetch
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_options_chain(symbol: str = "NIFTY") -> dict:
    """Fetch the NSE options chain for NIFTY or BANKNIFTY.

    Two-step NSE session pattern (required):
      1. GET the NSE homepage — this sets the session cookie NSE expects.
      2. GET the options-chain API URL within the same client session.

    Returns
    -------
    dict with keys:
        symbol, expiry_date, spot_price, options_data (list),
        total_call_oi, total_put_oi

    Raises
    ------
    ValueError — if the symbol is not supported or NSE returns a non-200.
    httpx.HTTPError — on network failure (let the caller decide to retry or log).
    """
    normalized = symbol.upper().replace(" ", "").replace("-", "")
    if normalized not in SUPPORTED_SYMBOLS:
        raise ValueError(
            f"Unsupported symbol '{symbol}'. Supported: {SUPPORTED_SYMBOLS}"
        )

    url = _CHAIN_URL.format(symbol=normalized)

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        # Step 1 — get session cookie from NSE homepage
        await client.get(_NSE_HOME, headers=BROWSER_HEADERS)
        await asyncio.sleep(2)   # NSE bot-detection requires a pause after homepage hit

        # Step 2 — call the options-chain JSON API
        response = await client.get(url, headers=BROWSER_HEADERS)

    if response.status_code != 200:
        raise ValueError(
            f"NSE options chain returned HTTP {response.status_code} for {normalized}"
        )

    payload = response.json()
    options_data, spot, total_call_oi, total_put_oi, expiry_date = _parse_chain_payload(payload)

    logger.info(
        f"Options chain fetched  {normalized:<10}  "
        f"expiry={expiry_date}  spot={spot:,.2f}  "
        f"strikes={len(options_data)}  "
        f"call_oi={total_call_oi:,}  put_oi={total_put_oi:,}"
    )

    return {
        "symbol":        normalized,
        "expiry_date":   expiry_date,
        "spot_price":    spot,
        "options_data":  options_data,
        "total_call_oi": total_call_oi,
        "total_put_oi":  total_put_oi,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. PCR
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_pcr(options_data: list) -> float:
    """Calculate the Put/Call Ratio from per-strike OI data.

    PCR = Total Put OI / Total Call OI

    A PCR above 1.2 signals excessive bearish positioning (contrarian buy).
    A PCR below 0.8 signals excessive bullish positioning (contrarian sell).

    Returns 0.0 when total call OI is zero to avoid division by zero.
    """
    total_call_oi = sum(_to_int(row.get("call_oi")) for row in options_data)
    total_put_oi  = sum(_to_int(row.get("put_oi"))  for row in options_data)

    if total_call_oi <= 0:
        return 0.0

    return round(total_put_oi / total_call_oi, 4)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Max Pain
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_max_pain(options_data: list, spot_price: float) -> float:
    """Find the Max Pain strike — where option buyers collectively lose the most.

    Algorithm
    ---------
    For each candidate expiry price E (one per unique strike in the chain):
        total_payout(E) = sum over all strikes K of:
            max(E - K, 0) × call_OI(K)    ← intrinsic value owed to call buyers
          + max(K - E, 0) × put_OI(K)     ← intrinsic value owed to put buyers

    Max Pain = the E that *minimises* total_payout.

    At this price, option sellers keep the most premium, which is why markets
    tend to "gravitate" toward Max Pain as weekly expiry approaches.

    Returns spot_price unchanged when options_data is empty.
    """
    strikes = sorted({
        _to_float(row.get("strike_price"))
        for row in options_data
        if row.get("strike_price")
    })

    if not strikes:
        return float(spot_price or 0.0)

    min_payout  = float("inf")
    max_pain_strike = strikes[0]

    for expiry_candidate in strikes:
        total_payout = 0.0
        for row in options_data:
            k        = _to_float(row.get("strike_price"))
            call_oi  = _to_int(row.get("call_oi"))
            put_oi   = _to_int(row.get("put_oi"))
            # Intrinsic value paid to call buyers if index expires at expiry_candidate
            total_payout += max(expiry_candidate - k, 0.0) * call_oi
            # Intrinsic value paid to put buyers if index expires at expiry_candidate
            total_payout += max(k - expiry_candidate, 0.0) * put_oi

        if total_payout < min_payout:
            min_payout       = total_payout
            max_pain_strike  = expiry_candidate

    return max_pain_strike


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Support / resistance from OI
# ═══════════════════════════════════════════════════════════════════════════════

def get_support_resistance_from_oi(options_data: list, spot: float) -> dict:
    """Derive key price levels from open interest concentration.

    Support levels  — top 3 strikes *below* spot ranked by Put OI (put writers
                      defend these levels; pin risk keeps price above them).
    Resistance levels — top 3 strikes *above* spot ranked by Call OI (call
                      writers defend these levels; pin risk keeps price below).

    Returns
    -------
    {'support': [s1, s2, s3], 'resistance': [r1, r2, r3]}
    Lists may be shorter than 3 when fewer strikes exist on one side.
    """
    below = [
        (_to_float(row.get("strike_price")), _to_int(row.get("put_oi")))
        for row in options_data
        if _to_float(row.get("strike_price")) < spot
    ]
    above = [
        (_to_float(row.get("strike_price")), _to_int(row.get("call_oi")))
        for row in options_data
        if _to_float(row.get("strike_price")) > spot
    ]

    # Highest OI first
    support    = [s for s, _ in sorted(below, key=lambda x: x[1], reverse=True)[:3]]
    resistance = [s for s, _ in sorted(above, key=lambda x: x[1], reverse=True)[:3]]

    return {"support": support, "resistance": resistance}


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Score
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_options_score(pcr: float, max_pain: float, spot: float) -> float:
    """Combine PCR contrarian signal and Max Pain gravity into a score.

    PCR scoring (contrarian — high put buying = bullish signal)
    -----------------------------------------------------------
    PCR > 1.5   →  +30   extreme put buying, strong contrarian buy
    PCR ≥ 1.2   →  +20   bearish sentiment, contrarian buy
    PCR ≥ 0.8   →    0   neutral sentiment
    PCR ≥ 0.5   →  -20   bullish sentiment, contrarian sell
    PCR < 0.5   →  -30   extreme call buying, strong contrarian sell

    Max Pain gravity scoring
    ------------------------
    Spot more than 1% BELOW Max Pain  →  +15 (price expected to rise)
    Spot more than 1% ABOVE Max Pain  →  -15 (price expected to fall)
    Within 1% band                    →    0

    Result is clamped to [-100, +100].
    """
    # PCR component
    if   pcr >  1.5: pcr_score =  30
    elif pcr >= 1.2: pcr_score =  20
    elif pcr >= 0.8: pcr_score =   0
    elif pcr >= 0.5: pcr_score = -20
    else:            pcr_score = -30

    # Max Pain gravity component
    mp_score = 0
    if spot and max_pain:
        deviation = (max_pain - spot) / spot   # positive = spot below max pain
        if   deviation >  0.01: mp_score =  15
        elif deviation < -0.01: mp_score = -15

    total = float(max(-100, min(100, pcr_score + mp_score)))
    return total


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

async def run_options_analysis(session: AsyncSession) -> dict:
    """Fetch, analyse, and persist options-chain snapshots for NIFTY and BANKNIFTY.

    For each symbol:
      1. Fetches the live options chain from NSE.
      2. Calculates PCR, Max Pain, ATM strike, support/resistance levels.
      3. Computes a combined options score.
      4. Persists a new OptionsChainSnapshot row (append-only — one per tick).
      5. Logs the summary line specified by the spec.

    The caller is responsible for committing or rolling back the session.

    Returns
    -------
    dict keyed by symbol ('NIFTY', 'BANKNIFTY'). Each value is either a
    result dict (on success) or ``{'error': str}`` (on failure).
    """
    results: dict = {}

    for symbol in SUPPORTED_SYMBOLS:
        try:
            chain        = await fetch_options_chain(symbol)
            options_data = chain["options_data"]
            spot         = chain["spot_price"]
            total_call_oi = chain["total_call_oi"]
            total_put_oi  = chain["total_put_oi"]

            # PCR: use pre-computed aggregate totals when available (more accurate
            # than re-summing per-strike rows which may cover multiple expiries)
            if total_call_oi > 0:
                pcr = round(total_put_oi / total_call_oi, 4)
            else:
                pcr = calculate_pcr(options_data)

            max_pain = calculate_max_pain(options_data, spot)
            levels   = get_support_resistance_from_oi(options_data, spot)
            score    = calculate_options_score(pcr, max_pain, spot)

            # ATM strike = the traded strike closest to current spot
            atm_strike = min(
                (row["strike_price"] for row in options_data if row["strike_price"]),
                key=lambda k: abs(k - spot),
                default=spot,
            )

            snapshot = OptionsChainSnapshot(
                symbol=symbol,
                expiry_date=chain["expiry_date"],
                atm_strike=atm_strike,
                pcr=pcr,
                max_pain=max_pain,
                total_call_oi=total_call_oi,
                total_put_oi=total_put_oi,
                support_levels=levels["support"],
                resistance_levels=levels["resistance"],
            )
            session.add(snapshot)
            await session.flush()

            results[symbol] = {
                "spot":              spot,
                "expiry_date":       chain["expiry_date"],
                "atm_strike":        atm_strike,
                "pcr":               pcr,
                "max_pain":          max_pain,
                "total_call_oi":     total_call_oi,
                "total_put_oi":      total_put_oi,
                "support_levels":    levels["support"],
                "resistance_levels": levels["resistance"],
                "options_score":     score,
            }

            logger.info(
                f"{symbol} PCR: {pcr:.4f}  │  "
                f"Max Pain: {max_pain:,.0f}  │  "
                f"Spot: {spot:,.2f}  │  "
                f"ATM: {atm_strike:,.0f}  │  "
                f"Score: {score:+.0f}"
            )

        except Exception as exc:
            logger.warning(f"Options analysis failed for {symbol}: {exc}")
            results[symbol] = {"error": str(exc)}

    return results
