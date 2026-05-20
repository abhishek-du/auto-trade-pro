"""Mutual Fund Analyzer — NAV history, CAGR, SIP simulation, risk metrics.

Uses mftool (AMFI data) for all NAV lookups. All network calls run in
asyncio executors so the async DB session is never blocked.

Public API
----------
fetch_scheme_info(scheme_code)                           -> SchemeInfo | None  (async)
analyze_scheme(scheme_code)                              -> MutualFundAnalysis | None  (async)
analyze_all_schemes(scheme_codes)                        -> list[MutualFundAnalysis]   (async)
project_sip(monthly_amount, annual_return_pct, months)  -> dict  (sync, projection only)
"""

from __future__ import annotations

import asyncio
import datetime
import math
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from utils.logger import logger

# ── Optional mftool import ────────────────────────────────────────────────────

_MFTOOL_AVAILABLE = False
try:
    from mftool import Mftool as _Mftool
    _MFTOOL_AVAILABLE = True
except ImportError:
    pass

_DEFAULT_SIP_AMOUNT: float = 5_000.0   # ₹5,000 / month for simulation
_RISK_FREE_RATE:     float = 0.065     # 6.5% p.a. — approximate Indian T-bill rate


def _get_mftool():
    if not _MFTOOL_AVAILABLE:
        raise ImportError("mftool not installed — run: pip install mftool")
    return _Mftool()


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class SchemeInfo:
    scheme_code: str
    scheme_name: str
    nav: float
    nav_date: datetime.date
    category: str
    fund_house: str


@dataclass
class SIPResult:
    scheme_code: str
    scheme_name: str
    monthly_amount: float
    months_invested: int
    total_invested: float
    current_value: float
    absolute_return_pct: float
    cagr: float
    units_held: float


@dataclass
class MutualFundAnalysis:
    scheme_code:  str
    scheme_name:  str
    fund_house:   str
    category:     str
    current_nav:  float
    nav_date:     datetime.date
    return_1y:    Optional[float]     # CAGR %
    return_3y:    Optional[float]
    return_5y:    Optional[float]
    sip_1y:       Optional[SIPResult] # ₹5,000/month × 12 months
    sip_3y:       Optional[SIPResult] # ₹5,000/month × 36 months
    volatility:   Optional[float]     # annualised std dev of daily returns %
    sharpe_ratio: Optional[float]     # annualised, risk-free = 6.5%
    analyzed_at:  datetime.datetime = field(default_factory=datetime.datetime.now)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_historical_nav(scheme_code: str) -> pd.DataFrame:
    """Fetch and parse AMFI historical NAV into a sorted DataFrame.

    Returns columns: date (datetime64), nav (float64).
    Returns empty DataFrame on failure.
    """
    mf = _get_mftool()
    try:
        data = mf.get_scheme_historical_nav(scheme_code, as_Dataframe=False)
    except Exception as exc:
        logger.warning(f"_parse_historical_nav {scheme_code}: mftool error — {exc}")
        return pd.DataFrame()

    if not data or data.get("status") != "SUCCESS":
        logger.warning(f"_parse_historical_nav {scheme_code}: bad status — {data!r}")
        return pd.DataFrame()

    records = data.get("data", [])
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"], format="%d-%m-%Y", errors="coerce")
    df["nav"]  = pd.to_numeric(df["nav"], errors="coerce")
    df = df.dropna(subset=["date", "nav"]).sort_values("date").reset_index(drop=True)
    return df


def _parse_nav_date(raw: str | None) -> datetime.date:
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%b %d, %Y"):
        try:
            if raw:
                return datetime.datetime.strptime(raw.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return datetime.date.today()


def calculate_cagr(nav_now: float, nav_then: float, years: float) -> float:
    """Compound Annual Growth Rate in percent."""
    if nav_then <= 0 or years <= 0 or nav_now <= 0:
        return 0.0
    return ((nav_now / nav_then) ** (1.0 / years) - 1.0) * 100.0


def _simulate_sip_from_history(
    df: pd.DataFrame,
    scheme_code: str,
    scheme_name: str,
    monthly_amount: float,
    months: int,
) -> SIPResult | None:
    """Simulate monthly SIP purchases using actual historical NAVs.

    Takes the first available NAV per calendar month over the last `months`
    months as the purchase NAV, then values at today's (last available) NAV.
    Returns None when there is insufficient history.
    """
    if df.empty or len(df) < 5:
        return None

    current_nav = float(df["nav"].iloc[-1])
    end_date    = df["date"].iloc[-1]
    start_date  = end_date - pd.DateOffset(months=months)

    window = df[df["date"] >= start_date].copy()
    if window.empty:
        return None

    window["month"] = window["date"].dt.to_period("M")
    first_per_month = (
        window.groupby("month", sort=True)
        .first()
        .reset_index()
    )

    total_units    = 0.0
    total_invested = 0.0

    for _, row in first_per_month.iterrows():
        nav_at_purchase = float(row["nav"])
        if nav_at_purchase > 0:
            total_units    += monthly_amount / nav_at_purchase
            total_invested += monthly_amount

    if total_invested == 0.0:
        return None

    current_value = total_units * current_nav
    abs_return_pct = (current_value - total_invested) / total_invested * 100.0
    years = len(first_per_month) / 12.0
    cagr  = calculate_cagr(current_value, total_invested, years) if years > 0 else 0.0

    return SIPResult(
        scheme_code=scheme_code,
        scheme_name=scheme_name,
        monthly_amount=monthly_amount,
        months_invested=len(first_per_month),
        total_invested=round(total_invested, 2),
        current_value=round(current_value, 2),
        absolute_return_pct=round(abs_return_pct, 2),
        cagr=round(cagr, 2),
        units_held=round(total_units, 4),
    )


def _calculate_risk_metrics(df: pd.DataFrame) -> tuple[float | None, float | None]:
    """Return (annualised_volatility_pct, sharpe_ratio) from daily NAV returns.

    Uses up to 252 trading days (1 year). Returns (None, None) when
    fewer than 30 data points are available.
    """
    if len(df) < 30:
        return None, None

    recent = df.tail(252).copy()
    recent["ret"] = recent["nav"].pct_change()
    recent = recent.dropna(subset=["ret"])

    if len(recent) < 20:
        return None, None

    daily_std = float(recent["ret"].std())
    if daily_std == 0:
        return 0.0, 0.0

    annualised_vol = daily_std * math.sqrt(252) * 100.0

    daily_mean = float(recent["ret"].mean())
    daily_rf   = (1 + _RISK_FREE_RATE) ** (1 / 252) - 1
    sharpe     = (daily_mean - daily_rf) / daily_std * math.sqrt(252)

    return round(annualised_vol, 2), round(sharpe, 2)


# ── Public async API ──────────────────────────────────────────────────────────

async def fetch_scheme_info(scheme_code: str) -> SchemeInfo | None:
    """Return current NAV and metadata for *scheme_code* from AMFI via mftool."""
    loop = asyncio.get_event_loop()
    try:
        mf    = _get_mftool()
        quote = await loop.run_in_executor(None, mf.get_scheme_quote, scheme_code)
    except Exception as exc:
        logger.warning(f"fetch_scheme_info {scheme_code}: {exc}")
        return None

    if not quote:
        return None

    nav_raw  = quote.get("nav") or quote.get("Net Asset Value") or "0"
    nav      = float(str(nav_raw).replace(",", "")) if nav_raw else 0.0
    nav_date = _parse_nav_date(quote.get("last_updated") or quote.get("Date"))

    return SchemeInfo(
        scheme_code=scheme_code,
        scheme_name=quote.get("scheme_name", ""),
        nav=nav,
        nav_date=nav_date,
        category=quote.get("scheme_category", ""),
        fund_house=quote.get("mutual_fund_family") or quote.get("fund_house", ""),
    )


async def analyze_scheme(scheme_code: str) -> MutualFundAnalysis | None:
    """Full analysis: CAGR (1Y/3Y/5Y), SIP simulation, volatility, Sharpe.

    All blocking mftool calls are dispatched to the thread pool executor
    to avoid stalling the event loop.
    """
    loop = asyncio.get_event_loop()

    try:
        df = await loop.run_in_executor(None, _parse_historical_nav, scheme_code)
    except Exception as exc:
        logger.warning(f"analyze_scheme {scheme_code}: NAV history fetch failed — {exc}")
        return None

    if df.empty or len(df) < 5:
        logger.warning(f"analyze_scheme {scheme_code}: insufficient NAV history")
        return None

    info = await fetch_scheme_info(scheme_code)

    current_nav = float(df["nav"].iloc[-1])
    nav_date    = df["date"].iloc[-1].date()

    def _nav_years_ago(years: float) -> float | None:
        target = df["date"].iloc[-1] - pd.Timedelta(days=int(years * 365.25))
        subset = df[df["date"] <= target]
        return float(subset["nav"].iloc[-1]) if not subset.empty else None

    nav_1y = _nav_years_ago(1)
    nav_3y = _nav_years_ago(3)
    nav_5y = _nav_years_ago(5)

    return_1y = round(calculate_cagr(current_nav, nav_1y, 1.0), 2) if nav_1y else None
    return_3y = round(calculate_cagr(current_nav, nav_3y, 3.0), 2) if nav_3y else None
    return_5y = round(calculate_cagr(current_nav, nav_5y, 5.0), 2) if nav_5y else None

    scheme_name = info.scheme_name if info else scheme_code

    sip_1y = _simulate_sip_from_history(df, scheme_code, scheme_name, _DEFAULT_SIP_AMOUNT, 12)
    sip_3y = _simulate_sip_from_history(df, scheme_code, scheme_name, _DEFAULT_SIP_AMOUNT, 36)

    vol, sharpe = _calculate_risk_metrics(df)

    analysis = MutualFundAnalysis(
        scheme_code=scheme_code,
        scheme_name=scheme_name,
        fund_house=info.fund_house if info else "",
        category=info.category  if info else "",
        current_nav=round(current_nav, 4),
        nav_date=nav_date,
        return_1y=return_1y,
        return_3y=return_3y,
        return_5y=return_5y,
        sip_1y=sip_1y,
        sip_3y=sip_3y,
        volatility=vol,
        sharpe_ratio=sharpe,
    )

    logger.info(
        f"MF {scheme_code} ({scheme_name[:40]})  "
        f"NAV={current_nav:.4f}  1Y={return_1y}%  3Y={return_3y}%  "
        f"vol={vol}%  sharpe={sharpe}"
    )
    return analysis


async def analyze_all_schemes(
    scheme_codes: list[str] | None = None,
) -> list[MutualFundAnalysis]:
    """Analyze all configured mutual fund schemes.

    Falls back to settings.WATCHLIST_MUTUAL_FUND_SCHEMES when no codes given.
    Results are sorted by 1-year return (descending).
    """
    from utils.config import settings

    codes   = scheme_codes or settings.WATCHLIST_MUTUAL_FUND_SCHEMES
    results: list[MutualFundAnalysis] = []

    for code in codes:
        analysis = await analyze_scheme(code)
        if analysis:
            results.append(analysis)
        else:
            logger.warning(f"analyze_all_schemes: skipped scheme {code}")

    results.sort(
        key=lambda a: a.return_1y if a.return_1y is not None else float("-inf"),
        reverse=True,
    )
    return results


# ── Sync projection helper (no mftool required) ───────────────────────────────

def project_sip(
    monthly_amount: float,
    expected_annual_return_pct: float,
    months: int,
) -> dict:
    """Project future SIP corpus using a constant assumed CAGR.

    This is a planning tool — it does NOT use actual NAV history.

    Parameters
    ----------
    monthly_amount : ₹ invested at the start of each month
    expected_annual_return_pct : assumed CAGR (e.g. 12 for 12%)
    months : investment horizon in months

    Returns
    -------
    dict with keys: monthly_amount, months, assumed_cagr_pct,
    total_invested, projected_value, absolute_return, absolute_return_pct
    """
    if months <= 0 or monthly_amount <= 0:
        raise ValueError("monthly_amount and months must be positive")

    annual_rate = expected_annual_return_pct / 100.0
    monthly_rate = (1 + annual_rate) ** (1 / 12) - 1

    if monthly_rate == 0:
        future_value = monthly_amount * months
    else:
        future_value = (
            monthly_amount
            * ((1 + monthly_rate) ** months - 1)
            / monthly_rate
            * (1 + monthly_rate)
        )

    total_invested  = monthly_amount * months
    absolute_return = future_value - total_invested

    return {
        "monthly_amount":       round(monthly_amount, 2),
        "months":               months,
        "assumed_cagr_pct":     expected_annual_return_pct,
        "total_invested":       round(total_invested, 2),
        "projected_value":      round(future_value, 2),
        "absolute_return":      round(absolute_return, 2),
        "absolute_return_pct":  round(absolute_return / total_invested * 100, 2),
    }
