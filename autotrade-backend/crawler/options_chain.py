"""NSE options-chain analysis for NIFTY and BANKNIFTY."""

from __future__ import annotations

import asyncio
import datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from crawler.fii_dii_crawler import BROWSER_HEADERS
from db.models import OptionsChainSnapshot
from utils.logger import logger

OPTIONS_CHAIN_URL = "https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
SUPPORTED_SYMBOLS = ("NIFTY", "BANKNIFTY")


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(str(value).replace(",", "").strip())
    except Exception:
        return default


def _to_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(str(value).replace(",", "").strip()))
    except Exception:
        return default


def _parse_expiry(value) -> datetime.date:
    if isinstance(value, datetime.date):
        return value
    raw = str(value or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y"):
        try:
            return datetime.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return datetime.date.today()


def _option_records(payload: dict) -> tuple[list[dict], float, int, int, datetime.date]:
    filtered = payload.get("filtered") or {}
    records = payload.get("records") or {}
    raw_rows = filtered.get("data") or []

    options_data: list[dict] = []
    expiry_date = None
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        ce = row.get("CE") or {}
        pe = row.get("PE") or {}
        expiry_date = expiry_date or row.get("expiryDate") or ce.get("expiryDate") or pe.get("expiryDate")
        options_data.append({
            "strike_price": _to_float(row.get("strikePrice")),
            "expiry_date": _parse_expiry(row.get("expiryDate") or ce.get("expiryDate") or pe.get("expiryDate")),
            "call_oi": _to_int(ce.get("openInterest")),
            "put_oi": _to_int(pe.get("openInterest")),
            "call_oi_change": _to_int(ce.get("changeinOpenInterest")),
            "put_oi_change": _to_int(pe.get("changeinOpenInterest")),
            "call_ltp": _to_float(ce.get("lastPrice")),
            "put_ltp": _to_float(pe.get("lastPrice")),
        })

    total_call_oi = _to_int((filtered.get("CE") or {}).get("totOI"))
    total_put_oi = _to_int((filtered.get("PE") or {}).get("totOI"))
    if total_call_oi == 0:
        total_call_oi = sum(row["call_oi"] for row in options_data)
    if total_put_oi == 0:
        total_put_oi = sum(row["put_oi"] for row in options_data)

    spot = _to_float(
        records.get("underlyingValue")
        or filtered.get("underlyingValue")
        or records.get("indexCloseOnlineRecords", {}).get("EOD_INDEX_NAME")
    )
    parsed_expiry = _parse_expiry(expiry_date)
    return options_data, spot, total_call_oi, total_put_oi, parsed_expiry


async def fetch_options_chain(symbol: str = "NIFTY") -> dict:
    """Fetch NSE options chain JSON using a browser-session cookie first."""
    normalized = symbol.upper().replace(" ", "")
    if normalized not in SUPPORTED_SYMBOLS:
        raise ValueError(f"Unsupported options symbol: {symbol}")

    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.get("https://www.nseindia.com", headers=BROWSER_HEADERS)
        await asyncio.sleep(1)
        response = await client.get(
            OPTIONS_CHAIN_URL.format(symbol=normalized),
            headers=BROWSER_HEADERS,
        )
        if response.status_code != 200:
            raise ValueError(f"NSE returned {response.status_code}")

    payload = response.json()
    options_data, spot, total_call_oi, total_put_oi, expiry_date = _option_records(payload)
    return {
        "symbol": normalized,
        "expiry_date": expiry_date,
        "spot_price": spot,
        "options_data": options_data,
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
    }


def calculate_pcr(options_data: list) -> float:
    """Calculate put/call ratio from total put OI divided by total call OI."""
    total_call_oi = sum(_to_int(row.get("call_oi")) for row in options_data)
    total_put_oi = sum(_to_int(row.get("put_oi")) for row in options_data)
    if total_call_oi <= 0:
        return 0.0
    return round(total_put_oi / total_call_oi, 4)


def calculate_max_pain(options_data: list, spot_price: float) -> float:
    """Return strike where total option-buyer payout is minimized."""
    strikes = sorted({_to_float(row.get("strike_price")) for row in options_data if row.get("strike_price")})
    if not strikes:
        return float(spot_price or 0.0)

    pain_by_strike: dict[float, float] = {}
    for expiry_strike in strikes:
        total_pain = 0.0
        for row in options_data:
            strike = _to_float(row.get("strike_price"))
            call_oi = _to_int(row.get("call_oi"))
            put_oi = _to_int(row.get("put_oi"))
            total_pain += max(expiry_strike - strike, 0.0) * call_oi
            total_pain += max(strike - expiry_strike, 0.0) * put_oi
        pain_by_strike[expiry_strike] = total_pain

    return min(pain_by_strike, key=pain_by_strike.get)


def get_support_resistance_from_oi(options_data: list, spot: float) -> dict:
    """Return top put-OI support below spot and top call-OI resistance above spot."""
    supports = sorted(
        (
            (_to_float(row.get("strike_price")), _to_int(row.get("put_oi")))
            for row in options_data
            if _to_float(row.get("strike_price")) < spot
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    resistances = sorted(
        (
            (_to_float(row.get("strike_price")), _to_int(row.get("call_oi")))
            for row in options_data
            if _to_float(row.get("strike_price")) > spot
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    return {
        "support": [strike for strike, _ in supports[:3]],
        "resistance": [strike for strike, _ in resistances[:3]],
    }


def calculate_options_score(pcr: float, max_pain: float, spot: float) -> float:
    """Combine PCR and max-pain mean-reversion into a -100 to +100 score."""
    score = 0.0
    if pcr > 1.5:
        score += 30
    elif pcr >= 1.2:
        score += 20
    elif pcr >= 0.8:
        score += 0
    elif pcr >= 0.5:
        score -= 20
    else:
        score -= 30

    if spot and max_pain:
        diff_pct = (max_pain - spot) / spot
        if diff_pct > 0.01:
            score += 15
        elif diff_pct < -0.01:
            score -= 15

    return float(max(-100, min(100, score)))


async def run_options_analysis(session: AsyncSession) -> dict:
    """Fetch, analyse, and persist NIFTY and BANKNIFTY options-chain snapshots."""
    results: dict = {}

    for symbol in SUPPORTED_SYMBOLS:
        try:
            chain = await fetch_options_chain(symbol)
            options_data = chain["options_data"]
            spot = chain["spot_price"]
            total_call_oi = chain["total_call_oi"]
            total_put_oi = chain["total_put_oi"]
            pcr = round(total_put_oi / total_call_oi, 4) if total_call_oi else calculate_pcr(options_data)
            max_pain = calculate_max_pain(options_data, spot)
            levels = get_support_resistance_from_oi(options_data, spot)
            atm_strike = min(
                (row["strike_price"] for row in options_data),
                key=lambda strike: abs(strike - spot),
                default=spot,
            )
            score = calculate_options_score(pcr, max_pain, spot)

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
                "spot": spot,
                "expiry_date": chain["expiry_date"],
                "atm_strike": atm_strike,
                "pcr": pcr,
                "max_pain": max_pain,
                "total_call_oi": total_call_oi,
                "total_put_oi": total_put_oi,
                "support_levels": levels["support"],
                "resistance_levels": levels["resistance"],
                "options_score": score,
            }
            logger.info(f"{symbol} PCR: {pcr} | Max Pain: {max_pain} | Spot: {spot}")
        except Exception as exc:
            logger.warning(f"Options analysis failed for {symbol}: {exc}")
            results[symbol] = {"error": str(exc)}
            continue

    return results
