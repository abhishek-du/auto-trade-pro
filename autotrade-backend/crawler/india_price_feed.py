"""Indian market price and NAV crawler for AutoTrade Pro.

Strategy:
  - yfinance handles NSE stocks, indices, India VIX, INR forex, and commodities.
  - mftool handles mutual fund NAV parsing.
  - nselib handles FPI fallback data not covered by yfinance.
  - Custom httpx should be used for NSE JSON endpoints such as FII/DII and options.
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from sqlalchemy.ext.asyncio import AsyncSession

from crawler.price_feed import save_candles_to_db
from utils.config import settings
from utils.logger import logger

NSE_HOLIDAYS_2026 = {
    "2026-01-26",
    "2026-03-25",
    "2026-04-14",
    "2026-04-17",
    "2026-05-01",
    "2026-08-15",
    "2026-10-02",
    "2026-12-25",
}

NIFTY_INDEX_SYMBOLS = {
    "^NSEI": "NIFTY50",
    "^BSESN": "SENSEX",
    "^NSEBANK": "BANKNIFTY",
}

_MF_TOOL = None


def is_nse_market_open() -> bool:
    """Return True when NSE is open in IST, excluding weekends and 2026 holidays."""
    ist = ZoneInfo(settings.IST_TIMEZONE)
    now = datetime.datetime.now(ist)

    if now.weekday() >= 5:
        return False

    if now.strftime("%Y-%m-%d") in NSE_HOLIDAYS_2026:
        return False

    market_open = now.replace(
        hour=settings.NSE_OPEN_HOUR,
        minute=settings.NSE_OPEN_MINUTE,
        second=0,
        microsecond=0,
    )
    market_close = now.replace(
        hour=settings.NSE_CLOSE_HOUR,
        minute=settings.NSE_CLOSE_MINUTE,
        second=0,
        microsecond=0,
    )
    return market_open <= now <= market_close


def _utc_index(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with a UTC-aware DatetimeIndex."""
    result = df.copy()
    if result.index.tz is None:
        result.index = result.index.tz_localize(settings.IST_TIMEZONE)
    result.index = result.index.tz_convert("UTC")
    return result


def fetch_nse_candles(symbol: str, interval: str = "1h", period: str = "60d") -> list[dict]:
    """Fetch NSE OHLCV candles from yfinance for symbols such as RELIANCE.NS."""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df.empty:
            logger.warning(f"yfinance NSE empty response for {symbol} ({interval})")
            return []

        df = _utc_index(df)
        rows: list[dict] = []
        for row in df.itertuples():
            rows.append({
                "symbol": symbol,
                "timeframe": interval,
                "open": float(row.Open),
                "high": float(row.High),
                "low": float(row.Low),
                "close": float(row.Close),
                "volume": float(getattr(row, "Volume", 0.0) or 0.0),
                "timestamp": row.Index.to_pydatetime(),
            })
        return rows
    except Exception as exc:
        logger.warning(f"Failed NSE candles {symbol}: {exc}")
        return []


def _fast_info_value(info, key: str, default=0.0):
    try:
        value = info.get(key, default)
    except AttributeError:
        value = getattr(info, key, default)
    return default if value is None else value


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(str(value).replace(",", "").strip())
    except Exception:
        return default


def fetch_nifty_indices() -> dict:
    """Fetch NIFTY50, SENSEX, and BANKNIFTY snapshots from yfinance fast_info."""
    snapshots: dict = {}
    for sym, name in NIFTY_INDEX_SYMBOLS.items():
        try:
            info = yf.Ticker(sym).fast_info
            price = float(_fast_info_value(info, "last_price", 0.0))
            prev_close = float(_fast_info_value(info, "previous_close", 0.0))
            change = price - prev_close if prev_close else 0.0
            change_pct = (change / prev_close * 100.0) if prev_close else 0.0
            snapshots[name] = {
                "price": price,
                "change": round(change, 4),
                "change_pct": round(change_pct, 4),
                "high_52w": float(_fast_info_value(info, "year_high", 0.0)),
                "low_52w": float(_fast_info_value(info, "year_low", 0.0)),
            }
        except Exception as exc:
            logger.warning(f"Failed index snapshot {sym}: {exc}")
            snapshots[name] = {
                "price": 0.0,
                "change": 0.0,
                "change_pct": 0.0,
                "high_52w": 0.0,
                "low_52w": 0.0,
            }
    return snapshots


def fetch_india_vix() -> float:
    """Fetch India VIX from yfinance, falling back to nselib if needed."""
    value = 0.0
    try:
        value = float(_fast_info_value(yf.Ticker("^INDIAVIX").fast_info, "last_price", 0.0))
    except Exception as exc:
        logger.warning(f"yfinance India VIX failed: {exc}")

    if value:
        logger.info(f"India VIX: {value}")
        return value

    try:
        from nselib import capital_market

        df = capital_market.india_vix_data(period="1W")
        value = float(df["Close"].iloc[-1]) if not df.empty else 15.0
    except Exception as exc:
        logger.warning(f"nselib India VIX fallback failed: {exc}")
        value = 15.0

    logger.info(f"India VIX: {value}")
    return value


def _get_mf_tool():
    global _MF_TOOL
    if _MF_TOOL is None:
        from mftool import Mftool

        _MF_TOOL = Mftool()
    return _MF_TOOL


def fetch_mutual_fund_nav(scheme_code: str) -> dict:
    """Fetch one mutual fund NAV using mftool's AMFI parser."""
    try:
        mf = _get_mf_tool()
        data = mf.get_scheme_quote(scheme_code) or {}
        nav = _to_float(data.get("nav") or data.get("NAV"))
        change = _to_float(data.get("change"))
        change_pct = _to_float(data.get("change_pct") or data.get("change_percent"))
        return {
            "scheme_code": scheme_code,
            "name": data.get("scheme_name") or data.get("name") or "",
            "nav": nav,
            "date": data.get("date") or data.get("last_updated") or "",
            "change": change,
            "change_pct": change_pct,
        }
    except Exception as exc:
        logger.warning(f"Failed mutual fund NAV {scheme_code}: {exc}")
        return {
            "scheme_code": scheme_code,
            "name": "",
            "nav": 0.0,
            "date": "",
            "change": 0.0,
            "change_pct": 0.0,
        }


def _nav_return(historic: pd.DataFrame, days: int) -> float:
    if historic.empty:
        return 0.0

    historic = historic.sort_index()
    nav_col = "nav" if "nav" in historic.columns else "NAV" if "NAV" in historic.columns else None
    if nav_col is None or len(historic) < 2:
        return 0.0

    try:
        series = pd.to_numeric(historic[nav_col], errors="coerce").dropna()
        if len(series) < 2:
            return 0.0
        latest = float(series.iloc[-1])
        base = float(series.iloc[max(0, len(series) - days)])
        return round(((latest - base) / base) * 100.0, 4) if base else 0.0
    except Exception:
        return 0.0


def fetch_all_mutual_fund_navs() -> list[dict]:
    """Fetch configured mutual fund NAVs and basic historical returns."""
    results: list[dict] = []
    try:
        mf = _get_mf_tool()
    except Exception as exc:
        logger.warning(f"mftool unavailable: {exc}")
        return results

    for scheme_code in settings.WATCHLIST_MUTUAL_FUND_SCHEMES:
        try:
            nav = fetch_mutual_fund_nav(scheme_code)
            historic = mf.get_scheme_historical_nav(scheme_code, as_Dataframe=True)
            if historic is None:
                historic = pd.DataFrame()
            results.append({
                "scheme_code": scheme_code,
                "name": nav["name"],
                "nav": nav["nav"],
                "one_month_return": _nav_return(historic, 30),
                "one_year_return": _nav_return(historic, 365),
            })
        except Exception as exc:
            logger.warning(f"Failed mutual fund history {scheme_code}: {exc}")
            continue
    return results


def fetch_bse_announcements(symbol_bse_code: str) -> list[dict]:
    """Fetch BSE quote/corporate-action context via bsedata, returning [] on failure."""
    try:
        from bsedata.bse import BSE

        bse = BSE(update_codes=False)
        result = bse.getQuote(symbol_bse_code)
        if not result:
            return []
        return [{
            "symbol_bse_code": symbol_bse_code,
            "company": result.get("companyName") or result.get("company_name") or "",
            "price": result.get("currentValue") or result.get("price") or "",
            "change": result.get("change") or "",
            "change_pct": result.get("pChange") or result.get("change_percent") or "",
            "raw": result,
        }]
    except Exception as exc:
        logger.warning(f"BSE announcements unavailable for {symbol_bse_code}: {exc}")
        return []


def fetch_fpi_investment_data() -> dict:
    """Fetch official FPI investment activity via nselib."""
    today_str = datetime.datetime.now(ZoneInfo(settings.IST_TIMEZONE)).strftime("%d-%m-%Y")
    try:
        from nselib import capital_market

        df = capital_market.nsdl_fpi_investment_activity(trade_date=today_str)
        if df.empty:
            return {"net_investment": 0.0, "buy_value": 0.0, "sell_value": 0.0, "date": today_str}

        row = df.iloc[-1]
        buy_value = _to_float(
            row.get("Buy Value")
            or row.get("buy_value")
            or row.get("Gross Purchases")
            or row.get("gross_purchases")
        )
        sell_value = _to_float(
            row.get("Sell Value")
            or row.get("sell_value")
            or row.get("Gross Sales")
            or row.get("gross_sales")
        )
        net_investment = _to_float(
            row.get("Net Value")
            or row.get("net_investment")
            or row.get("Net Investment"),
            buy_value - sell_value,
        )
        return {
            "net_investment": net_investment,
            "buy_value": buy_value,
            "sell_value": sell_value,
            "date": str(row.get("Date") or today_str),
        }
    except Exception as exc:
        logger.warning(f"FPI investment data unavailable: {exc}")
        return {"net_investment": 0.0, "buy_value": 0.0, "sell_value": 0.0, "date": today_str}


async def run_india_price_crawl(session: AsyncSession) -> dict:
    """Fetch Indian market candles and snapshots, then persist candles to the DB."""
    if not is_nse_market_open():
        logger.info("NSE closed -- skipping India crawl")
        return {
            "symbols_fetched": 0,
            "candles_saved": 0,
            "vix": None,
            "market_open": False,
        }

    all_candles: list[dict] = []
    symbols_fetched = 0
    errors: list[str] = []

    for symbol in settings.nse_symbols:
        try:
            data = fetch_nse_candles(symbol)
            if data:
                symbols_fetched += 1
                all_candles.extend(data)
            else:
                logger.warning(f"Failed {symbol}: no candle data -- continuing")
        except Exception as exc:
            logger.warning(f"Failed {symbol}: {exc} -- continuing")
            errors.append(f"{symbol}: {exc}")
            continue

    indices = fetch_nifty_indices()
    vix = fetch_india_vix()
    candles_saved = await save_candles_to_db(all_candles, session)

    result = {
        "symbols_fetched": symbols_fetched,
        "candles_saved": candles_saved,
        "vix": vix,
        "market_open": True,
    }
    logger.info(
        f"India crawl done symbols={symbols_fetched} "
        f"candles_saved={candles_saved} vix={vix} indices={indices} errors={len(errors)}"
    )
    return result
