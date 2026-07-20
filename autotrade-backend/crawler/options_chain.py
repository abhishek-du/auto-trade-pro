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
from db.models import OptionsChainSnapshot, OptionContractSnapshot, IVHistory
from engine.fno import options_pricing as _bs
from utils.config import settings
from utils.logger import logger

# ── NSE endpoints ─────────────────────────────────────────────────────────────

_NSE_HOME = "https://www.nseindia.com"
_NSE_OC_PAGE = "https://www.nseindia.com/option-chain"
# Current NSE API (v3). The old /api/option-chain-indices was retired → 404.
# v3 requires an explicit expiry; expiries come from contract-info.
_CONTRACT_INFO = "https://www.nseindia.com/api/option-chain-contract-info?symbol={symbol}"
_CHAIN_V3 = "https://www.nseindia.com/api/option-chain-v3?type={otype}&symbol={symbol}&expiry={expiry}"

SUPPORTED_SYMBOLS = ("NIFTY", "BANKNIFTY", "FINNIFTY")

# Circuit breaker: track last NSE failure time to avoid hammering a blocked endpoint
import time as _time
_last_nse_failure: float = 0.0
_NSE_BACKOFF_SECS = 1800  # 30 min cooldown after a 404/non-200

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

    # Strike rows: v3 endpoint puts them under records.data (already expiry-filtered);
    # the legacy endpoint used filtered.data. Support both.
    raw_rows = filtered.get("data") or records.get("data") or []
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

def _fetch_nse_chain_sync(normalized: str, equity: bool, expiry_date: datetime.date | None = None) -> dict:
    """Blocking NSE fetch via curl_cffi (Chrome TLS impersonation).

    NSE/Akamai fingerprints the TLS handshake, so plain httpx/requests get a
    404 bot-block. curl_cffi impersonates Chrome's JA3 and clears it. Uses the
    current v3 endpoint (the old option-chain-indices was retired → 404).

    Sequence: homepage + option-chain page (Akamai cookies) → contract-info
    (expiry list) → option-chain-v3 for the requested expiry (nearest, if
    `expiry_date` is not given or doesn't match any listed expiry).
    """
    from curl_cffi import requests as creq

    otype = "Equity" if equity else "Indices"
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "referer": _NSE_OC_PAGE,
    }
    s = creq.Session(impersonate="chrome120")
    # Warm-up: collect Akamai cookies from real pages.
    s.get(_NSE_HOME, timeout=15)
    _time.sleep(1.0)
    s.get(_NSE_OC_PAGE, timeout=15)
    _time.sleep(1.0)

    # Expiry list.
    ci = s.get(_CONTRACT_INFO.format(symbol=normalized), headers=headers, timeout=20)
    if ci.status_code != 200:
        raise ValueError(f"NSE contract-info HTTP {ci.status_code} for {normalized}")
    expiries = (ci.json() or {}).get("expiryDates") or []
    if not expiries:
        raise ValueError(f"NSE returned no expiries for {normalized}")
    target = expiries[0]
    if expiry_date is not None:
        for e in expiries:
            if _parse_expiry(e) == expiry_date:
                target = e
                break

    # Chain for the target expiry.
    import urllib.parse
    url = _CHAIN_V3.format(otype=otype, symbol=normalized, expiry=urllib.parse.quote(target))
    r = s.get(url, headers=headers, timeout=20)
    if r.status_code != 200:
        raise ValueError(f"NSE option-chain-v3 HTTP {r.status_code} for {normalized}")
    payload = r.json()
    if not payload or not (payload.get("records") or {}).get("data"):
        raise ValueError(f"NSE returned empty chain for {normalized} (market closed?)")
    payload["_all_expiries"] = expiries
    return payload


async def fetch_options_chain(symbol: str = "NIFTY", *, equity: bool = False, expiry_date: datetime.date | None = None) -> dict:
    """Fetch the live NSE option chain for an index or single stock.

    Uses curl_cffi (Chrome TLS impersonation) + the current v3 API. The blocking
    fetch runs in a thread executor so the async caller isn't blocked.

    `expiry_date` (optional): fetch this specific expiry's chain instead of the
    nearest one (falls back to nearest if it isn't in NSE's listed expiries).
    Needed because a position can be opened on a later expiry than the front
    week, and that contract still needs live premium updates every cycle.

    Returns dict: symbol, expiry_date, spot_price, options_data, total_call_oi,
    total_put_oi, all_expiries. Raises ValueError on bot-block / empty / closed-market.
    """
    normalized = symbol.upper().replace(" ", "").replace("-", "").replace(".NS", "")
    if not equity and normalized not in SUPPORTED_SYMBOLS:
        raise ValueError(f"Unsupported index symbol '{symbol}'. Supported: {SUPPORTED_SYMBOLS}")

    global _last_nse_failure
    if _last_nse_failure and (_time.time() - _last_nse_failure) < _NSE_BACKOFF_SECS:
        wait_min = int((_NSE_BACKOFF_SECS - (_time.time() - _last_nse_failure)) / 60) + 1
        raise ValueError(f"NSE options chain backing off for {wait_min} more min")

    loop = asyncio.get_event_loop()
    try:
        payload = await loop.run_in_executor(None, _fetch_nse_chain_sync, normalized, equity, expiry_date)
    except Exception as exc:
        _last_nse_failure = _time.time()
        raise ValueError(str(exc))

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
        "all_expiries":  payload.get("_all_expiries") or [],
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
# 5b. Greeks / IV per strike
# ═══════════════════════════════════════════════════════════════════════════════

async def compute_and_persist_greeks(
    underlying: str,
    chain: dict,
    session: AsyncSession,
) -> float | None:
    """Compute IV + Greeks for every strike and persist OptionContractSnapshot rows.

    Returns the ATM implied volatility (avg of ATM call/put IV) for IV-history,
    or None when it can't be derived. Best-effort: never raises.
    """
    options_data = chain.get("options_data") or []
    spot         = float(chain.get("spot_price") or 0.0)
    expiry_date  = chain.get("expiry_date")
    if not options_data or spot <= 0 or expiry_date is None:
        return None

    dte = (expiry_date - datetime.date.today()).days
    T = _bs.years_to_expiry(dte)
    if T <= 0:
        return None
    r = settings.RISK_FREE_RATE

    atm_strike = min(
        (row["strike_price"] for row in options_data if row["strike_price"]),
        key=lambda k: abs(k - spot), default=spot,
    )
    atm_call_iv = atm_put_iv = None
    rows_added = 0

    for row in options_data:
        strike = float(row.get("strike_price") or 0.0)
        if strike <= 0:
            continue
        for opt_type, ltp_key, oi_key, oic_key, flag in (
            ("CE", "call_ltp", "call_oi", "call_oi_change", "c"),
            ("PE", "put_ltp",  "put_oi",  "put_oi_change",  "p"),
        ):
            ltp = float(row.get(ltp_key) or 0.0)
            g = _bs.greeks_from_price(ltp, spot, strike, T, r, flag) if ltp > 0 else None
            session.add(OptionContractSnapshot(
                underlying=underlying.upper(), expiry_date=expiry_date,
                strike=strike, option_type=opt_type, spot=spot, ltp=ltp,
                oi=int(row.get(oi_key) or 0), oi_change=int(row.get(oic_key) or 0),
                volume=0,
                iv=round(g.iv, 4) if g else None,
                delta=g.delta if g else None, gamma=g.gamma if g else None,
                theta=g.theta if g else None, vega=g.vega if g else None,
                rho=g.rho if g else None,
            ))
            rows_added += 1
            if strike == atm_strike and g:
                if opt_type == "CE": atm_call_iv = g.iv
                else:                atm_put_iv = g.iv

    if rows_added:
        await session.flush()

    ivs = [v for v in (atm_call_iv, atm_put_iv) if v is not None]
    atm_iv = round(sum(ivs) / len(ivs), 4) if ivs else None
    if atm_iv is not None:
        await _upsert_iv_history(underlying, atm_iv, session)
    return atm_iv


async def _upsert_iv_history(underlying: str, atm_iv: float, session: AsyncSession) -> None:
    """Record (or update) today's ATM IV for IV-Rank / IV-Percentile history."""
    from sqlalchemy import select as _sel
    today = datetime.date.today()
    existing = (await session.execute(
        _sel(IVHistory).where(
            IVHistory.underlying == underlying.upper(),
            IVHistory.trade_date == today,
        )
    )).scalar_one_or_none()
    if existing:
        existing.atm_iv = atm_iv
    else:
        session.add(IVHistory(underlying=underlying.upper(), trade_date=today, atm_iv=atm_iv))
    await session.flush()


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

            if not options_data or spot == 0:
                logger.info(f"[options] {symbol}: empty chain data — skipping snapshot")
                results[symbol] = {"error": "empty chain — market closed or NSE unavailable"}
                continue

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

            # Per-strike IV + Greeks (gated behind the F&O flag — extra compute).
            atm_iv = None
            if getattr(settings, "ENABLE_FNO", False):
                try:
                    atm_iv = await compute_and_persist_greeks(symbol, chain, session)
                except Exception as gex:
                    logger.debug(f"[options] {symbol} greeks compute failed: {gex}")

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
                "atm_iv":            atm_iv,
            }

            logger.info(
                f"{symbol} PCR: {pcr:.4f}  │  "
                f"Max Pain: {max_pain:,.0f}  │  "
                f"Spot: {spot:,.2f}  │  "
                f"ATM: {atm_strike:,.0f}  │  "
                f"Score: {score:+.0f}"
                + (f"  │  ATM IV: {atm_iv:.1%}" if atm_iv else "")
            )

        except Exception as exc:
            msg = str(exc)
            # Downgrade backoff/market-closed messages to debug — they're expected
            if "backing off" in msg or "market closed" in msg.lower() or "closed" in msg.lower():
                logger.debug(f"Options analysis skipped for {symbol}: {msg}")
            else:
                logger.warning(f"Options analysis failed for {symbol}: {msg}")
            results[symbol] = {"error": msg}

    # ── Non-nearest expiries with open positions ────────────────────────────
    # The pass above only ever fetches the single nearest NSE expiry per
    # symbol. A position opened on a later expiry (e.g. next week's, while
    # this week's is still the "nearest") would then never get a fresh
    # snapshot until its own expiry becomes the nearest one — silently
    # freezing current_option_premium() at the entry price for the position's
    # entire life. Cover any open position's actual expiry explicitly.
    covered = {
        (sym, res["expiry_date"])
        for sym, res in results.items()
        if "error" not in res
    }
    try:
        from sqlalchemy import select as _select, distinct as _distinct
        from db.models import OpenPosition
        today = datetime.date.today()
        rows = (await session.execute(
            _select(_distinct(OpenPosition.underlying_symbol), OpenPosition.expiry_date)
            .where(
                OpenPosition.instrument_type.in_(("CE", "PE")),
                OpenPosition.underlying_symbol.in_(SUPPORTED_SYMBOLS),
                OpenPosition.expiry_date.is_not(None),
                OpenPosition.expiry_date >= today,
            )
        )).all()
    except Exception as exc:
        logger.warning(f"Options analysis: open-position expiry lookup failed: {exc}")
        rows = []

    for underlying, expiry_date in rows:
        if (underlying, expiry_date) in covered:
            continue
        try:
            chain = await fetch_options_chain(underlying, expiry_date=expiry_date)
            if not chain["options_data"] or chain["spot_price"] == 0:
                continue
            if getattr(settings, "ENABLE_FNO", False):
                await compute_and_persist_greeks(underlying, chain, session)
            else:
                # Still persist raw per-strike LTPs even when Greeks are gated
                # off, so current_option_premium()'s snapshot fallback (tier 3)
                # has data for this contract regardless of ENABLE_FNO.
                for row in chain["options_data"]:
                    strike = float(row.get("strike_price") or 0.0)
                    if strike <= 0:
                        continue
                    for opt_type, ltp_key in (("CE", "call_ltp"), ("PE", "put_ltp")):
                        ltp = float(row.get(ltp_key) or 0.0)
                        session.add(OptionContractSnapshot(
                            underlying=underlying.upper(), expiry_date=chain["expiry_date"],
                            strike=strike, option_type=opt_type, spot=chain["spot_price"], ltp=ltp,
                        ))
                await session.flush()
            covered.add((underlying, expiry_date))
            logger.info(f"[options] {underlying} extra expiry {expiry_date} snapshotted for open position(s)")
        except Exception as exc:
            logger.warning(f"[options] extra-expiry snapshot failed for {underlying} {expiry_date}: {exc}")

    return results
