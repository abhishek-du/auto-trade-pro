"""Indian market price and NAV crawler for AutoTrade Pro.

Strategy
--------
  yfinance     — all price data: NSE/BSE stocks, indices, India VIX,
                 INR forex pairs, and commodities.
  mftool       — mutual fund NAV parsing only (AMFI flat-file format is
                 tedious to maintain; delegate to the library).
  nselib       — FPI investment data as fallback (fills gaps yfinance
                 does not cover).
  Custom httpx — FII/DII flows and options chain (simple NSE JSON
                 endpoints; own the code completely).

Do NOT import or install NSEpy (dead since 2018) or jugaad-trader
(inactive; 156 weekly downloads as of May 2026).
"""

from __future__ import annotations

import asyncio
import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from sqlalchemy.ext.asyncio import AsyncSession

from crawler.price_feed import save_candles_to_db
from utils.config import settings
from utils.logger import logger

# ── NSE market calendar ───────────────────────────────────────────────────────

NSE_HOLIDAYS_2026 = {
    "2026-01-26",  # Republic Day
    "2026-03-25",  # Holi
    "2026-04-14",  # Dr. Ambedkar Jayanti / Ram Navami
    "2026-04-17",  # Good Friday
    "2026-05-01",  # Maharashtra Day
    "2026-08-15",  # Independence Day
    "2026-10-02",  # Gandhi Jayanti
    "2026-12-25",  # Christmas
}

# Index symbols → human-readable names
NIFTY_INDEX_SYMBOLS: dict[str, str] = {
    "^NSEI":   "NIFTY50",
    "^BSESN":  "SENSEX",
    "^NSEBANK": "BANKNIFTY",
}

# Module-level mftool singleton — expensive to create, reuse across calls.
_MF_TOOL = None


# ── 1. Market hours check ─────────────────────────────────────────────────────

def is_nse_market_open() -> bool:
    """Return True when NSE is currently open for trading (IST).

    Excludes weekends and all 2026 exchange holidays.
    Uses settings.IST_TIMEZONE so the timezone string is configurable.
    """
    ist = ZoneInfo(settings.IST_TIMEZONE)
    now = datetime.datetime.now(ist)

    if now.weekday() >= 5:          # Saturday or Sunday
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


# ── Internal helpers ──────────────────────────────────────────────────────────

def _to_utc_naive(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of df with a UTC-naive DatetimeIndex.

    yfinance may return a tz-aware index in IST, US/Eastern, or UTC.
    Standardise everything to UTC-naive before building candle dicts.
    """
    df = df.copy()
    if df.index.tz is None:
        # Assume IST for NSE tickers that lack explicit tz info
        df.index = df.index.tz_localize(settings.IST_TIMEZONE)
    df.index = df.index.tz_convert("UTC").tz_localize(None)
    return df


def _fast_info_float(info, key: str, default: float = 0.0) -> float:
    """Safe float extraction from yfinance fast_info (dict-like or attribute)."""
    try:
        val = info.get(key, default)
    except AttributeError:
        val = getattr(info, key, default)
    if val is None:
        return default
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return default


def _to_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return default


# ── 2. NSE candle fetcher ─────────────────────────────────────────────────────

def fetch_nse_candles(
    symbol: str,
    interval: str = "1h",
    period: str = "60d",
) -> list[dict]:
    """Fetch OHLCV candles from yfinance for any NSE/BSE/index symbol.

    Parameters
    ----------
    symbol   : yfinance ticker, e.g. ``RELIANCE.NS``, ``^NSEI``, ``GC=F``.
    interval : Candle size — ``'1m'``, ``'5m'``, ``'15m'``, ``'1h'``, ``'1d'``.
    period   : Look-back window accepted by yfinance, e.g. ``'60d'``, ``'1y'``.

    Returns
    -------
    list of dicts compatible with ``save_candles_to_db()``.
    Returns ``[]`` on any error — never raises.
    """
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)

        if df.empty:
            logger.warning(f"yfinance NSE: empty response for {symbol} ({interval})")
            return []

        df = _to_utc_naive(df)

        rows: list[dict] = []
        for row in df.itertuples():
            rows.append({
                "symbol":    symbol,
                "timeframe": interval,
                "open":      float(row.Open),
                "high":      float(row.High),
                "low":       float(row.Low),
                "close":     float(row.Close),
                "volume":    float(getattr(row, "Volume", 0.0) or 0.0),
                "timestamp": row.Index.to_pydatetime(),
            })

        logger.info(
            f"yfinance NSE  ✓  {symbol:<15}  {len(rows):4d} candles  "
            f"interval={interval}  latest={rows[-1]['timestamp'].strftime('%Y-%m-%d %H:%M')}"
        )
        return rows

    except Exception as exc:
        logger.warning(f"fetch_nse_candles: failed {symbol}: {exc}")
        return []


# ── 3. NIFTY / SENSEX / BANKNIFTY snapshots ──────────────────────────────────

def fetch_nifty_indices() -> dict:
    """Fetch live price snapshots for NIFTY50, SENSEX, and BANKNIFTY.

    Returns
    -------
    dict keyed by human name (``'NIFTY50'``, ``'SENSEX'``, ``'BANKNIFTY'``)::

        {
            'NIFTY50': {
                'price': 24500.0,
                'change': 120.5,
                'change_pct': 0.49,
                'high_52w': 26277.35,
                'low_52w': 21964.0,
            },
            ...
        }
    """
    snapshots: dict = {}

    for sym, name in NIFTY_INDEX_SYMBOLS.items():
        try:
            info = yf.Ticker(sym).fast_info
            price      = _fast_info_float(info, "last_price")
            prev_close = _fast_info_float(info, "previous_close")
            change     = price - prev_close if prev_close else 0.0
            change_pct = (change / prev_close * 100.0) if prev_close else 0.0

            snapshots[name] = {
                "price":      round(price, 2),
                "change":     round(change, 2),
                "change_pct": round(change_pct, 4),
                "high_52w":   _fast_info_float(info, "year_high"),
                "low_52w":    _fast_info_float(info, "year_low"),
            }
            logger.info(
                f"Index  {name:<12}  price={price:,.2f}  "
                f"change={change:+,.2f} ({change_pct:+.2f}%)"
            )
        except Exception as exc:
            logger.warning(f"fetch_nifty_indices: failed {sym} ({name}): {exc}")
            snapshots[name] = {
                "price": 0.0, "change": 0.0,
                "change_pct": 0.0, "high_52w": 0.0, "low_52w": 0.0,
            }

    return snapshots


# ── 4. India VIX ─────────────────────────────────────────────────────────────

def fetch_india_vix() -> float:
    """Fetch India VIX (NSE fear gauge) from yfinance with nselib fallback.

    Returns a float (default 15.0 when both sources fail — neutral level).
    Logs the final value regardless of source.
    """
    # Primary: yfinance history — more reliable than fast_info for index tickers
    try:
        df = yf.Ticker("^INDIAVIX").history(period="5d", interval="1d", auto_adjust=False)
        if not df.empty:
            value = float(df["Close"].dropna().iloc[-1])
            if value > 0:
                logger.info(f"India VIX: {value:.2f}  (source: yfinance history)")
                return value
    except Exception as exc:
        logger.warning(f"yfinance India VIX history failed: {exc}")

    # Fallback: yfinance download (different code path, sometimes succeeds when Ticker fails)
    try:
        df2 = yf.download("^INDIAVIX", period="5d", interval="1d",
                          progress=False, auto_adjust=False)
        if not df2.empty:
            value = float(df2["Close"].dropna().iloc[-1])
            if value > 0:
                logger.info(f"India VIX: {value:.2f}  (source: yfinance download)")
                return value
    except Exception as exc:
        logger.warning(f"yfinance India VIX download failed: {exc}")

    logger.warning("India VIX: yfinance unavailable — using neutral default 15.0")
    return 15.0


# ── 5. Single mutual fund NAV ─────────────────────────────────────────────────

def _get_mf_tool():
    """Return the module-level Mftool singleton, creating it on first call."""
    global _MF_TOOL  # noqa: PLW0603
    if _MF_TOOL is None:
        from mftool import Mftool  # noqa: PLC0415
        _MF_TOOL = Mftool()
    return _MF_TOOL


def fetch_mutual_fund_nav(scheme_code: str) -> dict:
    """Fetch the current NAV for one AMFI scheme code using mftool.

    mftool is used here because AMFI changes their flat-file format
    periodically. Delegating the parsing means a format change is fixed
    by a library upgrade, not by editing this file.

    Returns a dict with keys: scheme_code, name, nav, date, change, change_pct.
    Returns zeroed values on failure — never raises.
    """
    try:
        mf   = _get_mf_tool()
        data = mf.get_scheme_quote(scheme_code) or {}

        nav        = _to_float(data.get("nav") or data.get("NAV"))
        change     = _to_float(data.get("change") or data.get("Change"))
        change_pct = _to_float(
            data.get("change_pct")
            or data.get("change_percent")
            or data.get("pChange")
        )

        return {
            "scheme_code": scheme_code,
            "name":        data.get("scheme_name") or data.get("name") or "",
            "nav":         nav,
            "date":        data.get("date") or data.get("last_updated") or "",
            "change":      change,
            "change_pct":  change_pct,
        }

    except Exception as exc:
        logger.warning(f"fetch_mutual_fund_nav: failed scheme {scheme_code}: {exc}")
        return {
            "scheme_code": scheme_code,
            "name": "", "nav": 0.0,
            "date": "", "change": 0.0, "change_pct": 0.0,
        }


# ── 6. All configured mutual fund NAVs ───────────────────────────────────────

def _nav_period_return(historic: pd.DataFrame, days: int) -> float:
    """Compute return (%) from ``days`` ago to today using a NAV DataFrame."""
    if historic is None or historic.empty:
        return 0.0

    historic = historic.sort_index()

    # Column name varies between mftool versions
    nav_col = next(
        (c for c in ("nav", "NAV", "Net Asset Value") if c in historic.columns),
        None,
    )
    if nav_col is None:
        return 0.0

    try:
        series = pd.to_numeric(historic[nav_col], errors="coerce").dropna()
        if len(series) < 2:
            return 0.0
        latest = float(series.iloc[-1])
        base   = float(series.iloc[max(0, len(series) - days)])
        return round(((latest - base) / base) * 100.0, 4) if base else 0.0
    except Exception:
        return 0.0


def fetch_all_mutual_fund_navs() -> list[dict]:
    """Fetch NAVs and historical returns for all configured AMFI schemes.

    Iterates ``settings.WATCHLIST_MUTUAL_FUND_SCHEMES``.
    Each entry includes one-month and one-year returns calculated from
    the historical NAV series returned by mftool.

    Returns list of dicts: {scheme_code, name, nav, one_month_return, one_year_return}.
    Returns [] if mftool is unavailable.
    """
    results: list[dict] = []

    try:
        mf = _get_mf_tool()
    except Exception as exc:
        logger.warning(f"mftool unavailable — skipping MF NAV fetch: {exc}")
        return results

    for scheme_code in settings.WATCHLIST_MUTUAL_FUND_SCHEMES:
        try:
            nav_data = fetch_mutual_fund_nav(scheme_code)
            historic = mf.get_scheme_historical_nav(scheme_code, as_Dataframe=True)

            results.append({
                "scheme_code":      scheme_code,
                "name":             nav_data["name"],
                "nav":              nav_data["nav"],
                "one_month_return": _nav_period_return(historic, 30),
                "one_year_return":  _nav_period_return(historic, 365),
            })
            logger.info(
                f"MF NAV  {scheme_code}  {nav_data['name'][:40]:<40}  "
                f"nav={nav_data['nav']}"
            )
        except Exception as exc:
            logger.warning(f"fetch_all_mutual_fund_navs: failed scheme {scheme_code}: {exc}")
            continue

    return results


# ── 7. BSE announcements via bsedata ─────────────────────────────────────────

def fetch_bse_announcements(symbol_bse_code: str) -> list[dict]:
    """Fetch BSE quote and corporate action context via the bsedata library.

    bsedata is used because BSE HTML is complex to parse and scrape;
    the library absorbs format changes and is actively maintained.

    Parameters
    ----------
    symbol_bse_code : BSE numeric code, e.g. ``'500325'`` for RELIANCE.

    Returns
    -------
    list with one summary dict on success, empty list on failure.
    """
    try:
        from bsedata.bse import BSE  # noqa: PLC0415

        bse    = BSE(update_codes=False)
        result = bse.getQuote(symbol_bse_code)

        if not result:
            logger.warning(f"bsedata: empty response for code {symbol_bse_code}")
            return []

        return [{
            "symbol_bse_code": symbol_bse_code,
            "company":         result.get("companyName") or result.get("company_name") or "",
            "price":           _to_float(result.get("currentValue") or result.get("price")),
            "change":          result.get("change") or "",
            "change_pct":      result.get("pChange") or result.get("change_percent") or "",
            "raw":             result,
        }]

    except Exception as exc:
        logger.warning(f"fetch_bse_announcements: unavailable for {symbol_bse_code}: {exc}")
        return []


# ── 8. FPI investment data via nselib ─────────────────────────────────────────

def fetch_fpi_investment_data() -> dict:
    """Fetch official FPI (Foreign Portfolio Investment) data from NSDL via nselib.

    This is distinct from the FII/DII flows endpoint:
    - FPI is the SEBI-official category reported by NSDL.
    - FII/DII flows come from the NSE API (see fii_dii_crawler.py).

    Returns dict: {net_investment, buy_value, sell_value, date}.
    Returns zeroed values when nselib or NSDL are unavailable.
    """
    ist      = ZoneInfo(settings.IST_TIMEZONE)
    today_str = datetime.datetime.now(ist).strftime("%d-%m-%Y")

    try:
        from nselib import capital_market  # noqa: PLC0415

        df = capital_market.nsdl_fpi_investment_activity(trade_date=today_str)

        if df is None or df.empty:
            logger.warning("nselib FPI: empty response — returning zeroes")
            return {
                "net_investment": 0.0,
                "buy_value":  0.0,
                "sell_value": 0.0,
                "date":       today_str,
            }

        row = df.iloc[-1]

        buy_value = _to_float(
            row.get("Buy Value") or row.get("buy_value")
            or row.get("Gross Purchases") or row.get("gross_purchases")
        )
        sell_value = _to_float(
            row.get("Sell Value") or row.get("sell_value")
            or row.get("Gross Sales") or row.get("gross_sales")
        )
        net_investment = _to_float(
            row.get("Net Value") or row.get("net_investment")
            or row.get("Net Investment"),
            default=buy_value - sell_value,
        )

        logger.info(
            f"FPI  net={net_investment:+,.2f}  "
            f"buy={buy_value:,.2f}  sell={sell_value:,.2f}  date={today_str}"
        )
        return {
            "net_investment": net_investment,
            "buy_value":      buy_value,
            "sell_value":     sell_value,
            "date":           str(row.get("Date") or today_str),
        }

    except Exception as exc:
        logger.warning(f"fetch_fpi_investment_data: unavailable: {exc}")
        return {
            "net_investment": 0.0,
            "buy_value":  0.0,
            "sell_value": 0.0,
            "date":       today_str,
        }


# ── 9. Orchestrator ──────────────────────────────────────────────────────────

async def run_india_price_crawl(session: AsyncSession) -> dict:
    """Fetch OHLCV candles for all Indian watchlist symbols and persist to DB.

    Scope
    -----
    Large-cap NSE stocks   — settings.nse_symbols          (e.g. RELIANCE.NS)
    Mid-cap NSE stocks     — settings.nse_mid_symbols       (e.g. PERSISTENT.NS)
    NIFTY / SENSEX indices — settings.WATCHLIST_NIFTY_INDICES (^NSEI, ^BSESN, ^NSEBANK)
    Indian forex pairs     — settings.WATCHLIST_INDIAN_FOREX  (USDINR=X …)
    Commodities            — settings.WATCHLIST_COMMODITIES   (GC=F, SI=F, CL=F)

    The crawl only runs when NSE is open; returns early otherwise.

    Returns
    -------
    dict with keys: symbols_fetched, candles_saved, vix, market_open.
    """
    if not is_nse_market_open():
        logger.info("NSE closed -- skipping India crawl")
        return {
            "symbols_fetched": 0,
            "candles_saved":   0,
            "vix":             None,
            "market_open":     False,
        }

    # Build full symbol list — large-cap + mid-cap + indices + INR forex + commodities
    all_symbols: list[str] = (
        settings.nse_symbols
        + settings.nse_mid_symbols
        + settings.WATCHLIST_NIFTY_INDICES
        + settings.WATCHLIST_INDIAN_FOREX
        + settings.WATCHLIST_COMMODITIES
    )

    logger.info(
        f"━━ India price crawl START ━━  {len(all_symbols)} symbols  "
        f"({len(settings.nse_symbols)} large-cap  "
        f"{len(settings.nse_mid_symbols)} mid-cap  "
        f"{len(settings.WATCHLIST_NIFTY_INDICES)} indices  "
        f"{len(settings.WATCHLIST_INDIAN_FOREX)} INR forex  "
        f"{len(settings.WATCHLIST_COMMODITIES)} commodities)"
    )

    all_candles:    list[dict] = []
    symbols_fetched: int       = 0
    errors:          list[str] = []

    # Step 1 — fetch candles for every symbol sequentially (avoids yfinance flood)
    for symbol in all_symbols:
        try:
            candles = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda s=symbol: fetch_nse_candles(s, interval="1h"),
            )
            if candles:
                symbols_fetched += 1
                all_candles.extend(candles)
            else:
                logger.warning(f"  ✗  {symbol}: no candle data -- continuing")
                errors.append(f"{symbol}: empty response")
        except Exception as exc:
            logger.warning(f"  ✗  {symbol}: {exc} -- continuing")
            errors.append(f"{symbol}: {exc}")

    # Step 2 — fetch index snapshots (non-DB, informational / dashboard use)
    indices = fetch_nifty_indices()

    # Step 3 — fetch India VIX
    vix = fetch_india_vix()

    # Step 4 — persist new candles to DB (chunked upsert, 3 000 rows per statement)
    candles_saved = await save_candles_to_db(all_candles, session)

    result = {
        "symbols_fetched": symbols_fetched,
        "candles_saved":   candles_saved,
        "vix":             vix,
        "market_open":     True,
    }
    logger.info(
        f"━━ India price crawl DONE  ━━  "
        f"symbols={symbols_fetched}/{len(all_symbols)}  "
        f"candles_saved={candles_saved}  "
        f"vix={vix:.2f}  "
        f"errors={len(errors)}"
    )
    return result
