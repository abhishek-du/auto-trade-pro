"""Mutual Fund Analyzer — DB-backed NAV tracking and SIP analysis.

NAV data is fetched from AMFI via mftool, persisted to the MutualFundNAV
table, and used for SIP simulation and buy-signal generation.  All mftool
calls are dispatched to the thread-pool executor.

Public API
----------
fetch_and_save_nav(scheme_code, session)                        -> dict        (async)
simulate_sip(scheme_code, monthly_amount, months, session)      -> dict        (async)
compare_funds(scheme_codes, session)                            -> list[dict]  (async)
get_mf_buy_signal(scheme_code, session)                         -> dict        (async)
project_sip(monthly_amount, expected_annual_return_pct, months) -> dict        (sync)
"""

from __future__ import annotations

import asyncio
import datetime
import math
from typing import Optional

import pandas as pd
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import MutualFundNAV
from utils.logger import logger

# ── Optional mftool ───────────────────────────────────────────────────────────

_MFTOOL_AVAILABLE = False
try:
    from mftool import Mftool as _Mftool
    _MFTOOL_AVAILABLE = True
except ImportError:
    pass


def _get_mftool() -> "_Mftool":
    if not _MFTOOL_AVAILABLE:
        raise ImportError("mftool not installed — run: pip install mftool")
    return _Mftool()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_nav_float(value) -> float:
    """Convert mftool NAV string '123.456' → float.  Returns 0.0 on failure."""
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _normalize_hist_df(raw) -> pd.DataFrame:
    """Normalise mftool historical NAV result to a sorted DataFrame.

    Accepts either a dict (as_Dataframe=False) or a DataFrame (as_Dataframe=True).
    Returns columns: date (datetime64[ns]), nav (float64).
    """
    if isinstance(raw, pd.DataFrame):
        df = raw.copy()
        # Reset index in case date is the index
        if df.index.name == "date" or not {"date", "nav"}.issubset(df.columns):
            df = df.reset_index()
        if len(df.columns) >= 2:
            df.columns = list(df.columns[:2])
            df = df.rename(columns={df.columns[0]: "date", df.columns[1]: "nav"})
    elif isinstance(raw, dict) and raw.get("status") == "SUCCESS":
        records = raw.get("data", [])
        df = pd.DataFrame(records)
    else:
        return pd.DataFrame(columns=["date", "nav"])

    df["date"] = pd.to_datetime(df["date"], format="%d-%m-%Y", errors="coerce")
    df["nav"]  = pd.to_numeric(df["nav"], errors="coerce")
    df = (
        df.dropna(subset=["date", "nav"])
        .sort_values("date")
        .reset_index(drop=True)
    )
    return df


def _return_pct(nav_now: float, nav_then: float) -> float | None:
    """Simple point-to-point return in percent."""
    if not nav_then or nav_then <= 0:
        return None
    return round((nav_now / nav_then - 1) * 100, 2)


def _cagr(nav_now: float, nav_then: float, years: float) -> float | None:
    """CAGR in percent."""
    if not nav_then or nav_then <= 0 or years <= 0 or nav_now <= 0:
        return None
    return round(((nav_now / nav_then) ** (1 / years) - 1) * 100, 2)


def _nav_n_days_ago(df: pd.DataFrame, days: int) -> float | None:
    target = df["date"].iloc[-1] - pd.Timedelta(days=days)
    subset = df[df["date"] <= target]
    return float(subset["nav"].iloc[-1]) if not subset.empty else None


# ── 1. fetch_and_save_nav ─────────────────────────────────────────────────────

async def fetch_and_save_nav(
    scheme_code: str,
    session: AsyncSession,
) -> dict:
    """Fetch current NAV + returns from AMFI and persist to MutualFundNAV table.

    Steps
    -----
    1. mf.get_scheme_quote()  → current NAV, scheme name, category
    2. mf.get_scheme_historical_nav(as_Dataframe=True) → past NAVs
    3. Calculate 1-month, 3-month, 1-year, 3-year returns
    4. Query DB for prev_nav; compute day-over-day change
    5. Insert MutualFundNAV row; return summary dict

    Returns an empty dict if mftool is unavailable or the scheme is not found.
    """
    loop = asyncio.get_event_loop()

    try:
        mf = _get_mftool()
    except ImportError as exc:
        logger.error(f"fetch_and_save_nav: {exc}")
        return {}

    # ── Step 1: current quote ─────────────────────────────────────────────────
    try:
        quote = await loop.run_in_executor(None, mf.get_scheme_quote, scheme_code)
    except Exception as exc:
        logger.warning(f"fetch_and_save_nav {scheme_code}: quote failed — {exc}")
        return {}

    if not quote:
        logger.warning(f"fetch_and_save_nav {scheme_code}: empty quote")
        return {}

    nav          = _parse_nav_float(quote.get("nav") or quote.get("Net Asset Value"))
    scheme_name  = quote.get("scheme_name", "")
    category     = quote.get("scheme_category") or quote.get("mutual_fund_family", "")

    if nav <= 0:
        logger.warning(f"fetch_and_save_nav {scheme_code}: invalid NAV={nav}")
        return {}

    # ── Step 2: historical NAV ────────────────────────────────────────────────
    try:
        raw_hist = await loop.run_in_executor(
            None,
            lambda: mf.get_scheme_historical_nav(scheme_code, as_Dataframe=True),
        )
        hist_df = _normalize_hist_df(raw_hist)
    except Exception as exc:
        logger.warning(f"fetch_and_save_nav {scheme_code}: history failed — {exc}")
        hist_df = pd.DataFrame(columns=["date", "nav"])

    # ── Step 3: calculate returns ─────────────────────────────────────────────
    one_month_return = three_month_return = one_year_return = three_year_return = None

    if not hist_df.empty:
        nav_1m  = _nav_n_days_ago(hist_df, 30)
        nav_3m  = _nav_n_days_ago(hist_df, 90)
        nav_1y  = _nav_n_days_ago(hist_df, 365)
        nav_3y  = _nav_n_days_ago(hist_df, 1095)

        one_month_return   = _return_pct(nav, nav_1m)
        three_month_return = _return_pct(nav, nav_3m)
        one_year_return    = _cagr(nav, nav_1y, 1.0)    # 1Y CAGR = simple return
        three_year_return  = _cagr(nav, nav_3y, 3.0)

    # ── Step 4: prev_nav + change ─────────────────────────────────────────────
    last_row = (await session.execute(
        select(MutualFundNAV)
        .where(MutualFundNAV.scheme_code == scheme_code)
        .order_by(desc(MutualFundNAV.recorded_at))
        .limit(1)
    )).scalar_one_or_none()

    prev_nav   = last_row.nav if last_row else nav
    change     = round(nav - prev_nav, 4)
    change_pct = round(change / prev_nav * 100, 2) if prev_nav > 0 else 0.0

    # ── Step 5: persist ───────────────────────────────────────────────────────
    row = MutualFundNAV(
        scheme_code=scheme_code,
        scheme_name=scheme_name,
        nav=nav,
        prev_nav=prev_nav,
        change=change,
        change_pct=change_pct,
        category=category,
        one_month_return=one_month_return,
        three_month_return=three_month_return,
        one_year_return=one_year_return,
        three_year_return=three_year_return,
    )
    session.add(row)
    await session.flush()

    summary = {
        "scheme_code":        scheme_code,
        "scheme_name":        scheme_name,
        "nav":                nav,
        "prev_nav":           prev_nav,
        "change":             change,
        "change_pct":         change_pct,
        "category":           category,
        "one_month_return":   one_month_return,
        "three_month_return": three_month_return,
        "one_year_return":    one_year_return,
        "three_year_return":  three_year_return,
        "recorded_at":        row.recorded_at,
    }
    logger.info(
        f"MF {scheme_code} ({scheme_name[:40]})  "
        f"NAV={nav}  Δ={change_pct:+.2f}%  1Y={one_year_return}%"
    )
    return summary


# ── 2. simulate_sip ───────────────────────────────────────────────────────────

async def simulate_sip(
    scheme_code: str,
    monthly_amount: float,
    months: int,
    session: AsyncSession,
) -> dict:
    """Simulate a monthly SIP using historical NAV data.

    Uses DB records (MutualFundNAV) grouped by calendar month.  When the DB
    has fewer than 2 months of data, falls back to mftool historical NAV.

    Returns
    -------
    dict with keys:
      total_invested, current_value, total_units, avg_nav,
      absolute_return, cagr_percent, best_month, worst_month
    """
    # ── Fetch monthly NAV series ──────────────────────────────────────────────
    monthly_df = await _get_monthly_nav_series(scheme_code, months, session)

    if monthly_df.empty or len(monthly_df) < 2:
        logger.warning(f"simulate_sip {scheme_code}: no monthly NAV data available")
        return {}

    # Get current NAV (latest record)
    current_nav = float(monthly_df["nav"].iloc[-1])

    # ── SIP simulation ────────────────────────────────────────────────────────
    units_per_month: list[dict] = []
    for _, row in monthly_df.iterrows():
        purchase_nav = float(row["nav"])
        if purchase_nav > 0:
            units = monthly_amount / purchase_nav
            units_per_month.append(
                {"month": str(row["month"]), "nav": purchase_nav, "units": units}
            )

    if not units_per_month:
        return {}

    total_units    = sum(m["units"] for m in units_per_month)
    total_invested = monthly_amount * len(units_per_month)
    current_value  = total_units * current_nav
    avg_nav        = total_invested / total_units if total_units > 0 else 0.0
    abs_return_pct = (current_value - total_invested) / total_invested * 100.0
    years          = len(units_per_month) / 12.0
    cagr_percent   = _cagr(current_value, total_invested, years) or 0.0

    # ── Monthly returns for best/worst ────────────────────────────────────────
    monthly_returns: list[dict] = []
    navs = [m["nav"] for m in units_per_month]
    for i in range(1, len(navs)):
        if navs[i - 1] > 0:
            month_ret = (navs[i] / navs[i - 1] - 1) * 100
            monthly_returns.append(
                {"month": units_per_month[i]["month"], "return_pct": round(month_ret, 2)}
            )

    best_month  = max(monthly_returns, key=lambda m: m["return_pct"]) if monthly_returns else None
    worst_month = min(monthly_returns, key=lambda m: m["return_pct"]) if monthly_returns else None

    return {
        "scheme_code":    scheme_code,
        "monthly_amount": monthly_amount,
        "months":         len(units_per_month),
        "total_invested": round(total_invested, 2),
        "current_value":  round(current_value, 2),
        "total_units":    round(total_units, 4),
        "avg_nav":        round(avg_nav, 4),
        "absolute_return": round(abs_return_pct, 2),
        "cagr_percent":   cagr_percent,
        "best_month":     best_month,
        "worst_month":    worst_month,
    }


async def _get_monthly_nav_series(
    scheme_code: str,
    months: int,
    session: AsyncSession,
) -> pd.DataFrame:
    """Return a DataFrame with columns [month (Period), nav (float)].

    Tries DB first; falls back to mftool when DB has fewer than 2 months.
    """
    # ── Query DB ──────────────────────────────────────────────────────────────
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=months * 31 + 10)
    rows = (await session.execute(
        select(MutualFundNAV)
        .where(
            MutualFundNAV.scheme_code == scheme_code,
            MutualFundNAV.recorded_at >= cutoff,
        )
        .order_by(MutualFundNAV.recorded_at)
    )).scalars().all()

    if rows:
        df = pd.DataFrame(
            [{"date": r.recorded_at, "nav": r.nav} for r in rows]
        )
        df["date"]  = pd.to_datetime(df["date"])
        df["month"] = df["date"].dt.to_period("M")
        monthly = (
            df.groupby("month", sort=True)
            .first()
            .reset_index()[["month", "nav"]]
        )
        if len(monthly) >= 2:
            return monthly.tail(months)

    # ── Fallback: mftool historical ───────────────────────────────────────────
    logger.debug(f"_get_monthly_nav_series {scheme_code}: DB sparse, falling back to mftool")
    try:
        loop = asyncio.get_event_loop()
        mf   = _get_mftool()
        raw  = await loop.run_in_executor(
            None,
            lambda: mf.get_scheme_historical_nav(scheme_code, as_Dataframe=True),
        )
        df = _normalize_hist_df(raw)
    except Exception as exc:
        logger.warning(f"_get_monthly_nav_series {scheme_code}: mftool failed — {exc}")
        return pd.DataFrame(columns=["month", "nav"])

    if df.empty:
        return pd.DataFrame(columns=["month", "nav"])

    end_date   = df["date"].iloc[-1]
    start_date = end_date - pd.DateOffset(months=months)
    df = df[df["date"] >= start_date].copy()
    df["month"] = df["date"].dt.to_period("M")
    monthly = (
        df.groupby("month", sort=True)
        .first()
        .reset_index()[["month", "nav"]]
    )
    return monthly


# ── 3. compare_funds ─────────────────────────────────────────────────────────

async def compare_funds(
    scheme_codes: list[str],
    session: AsyncSession,
) -> list[dict]:
    """Compare funds on 1-year return, 3-year return, and consistency.

    Consistency is measured as the standard deviation of monthly NAV returns
    over the last 3 years (lower = more consistent).

    Ranking score  =  0.4 × one_year_return
                    + 0.4 × three_year_return
                    - 0.2 × consistency_std_dev

    Returns a list of dicts sorted by score descending, with the top fund
    flagged as ``best_fund: True``.
    """
    entries: list[dict] = []

    for code in scheme_codes:
        # Get or refresh latest snapshot
        latest = (await session.execute(
            select(MutualFundNAV)
            .where(MutualFundNAV.scheme_code == code)
            .order_by(desc(MutualFundNAV.recorded_at))
            .limit(1)
        )).scalar_one_or_none()

        if latest is None:
            summary = await fetch_and_save_nav(code, session)
            if not summary:
                logger.warning(f"compare_funds: could not fetch data for {code}")
                continue
            # Re-query after save
            latest = (await session.execute(
                select(MutualFundNAV)
                .where(MutualFundNAV.scheme_code == code)
                .order_by(desc(MutualFundNAV.recorded_at))
                .limit(1)
            )).scalar_one_or_none()

        if latest is None:
            continue

        # Consistency: std dev of monthly returns over 3 years from DB
        consistency_std = await _monthly_return_std(code, session, years=3)

        one_year   = latest.one_year_return   or 0.0
        three_year = latest.three_year_return or 0.0
        score = (
            0.4 * one_year
            + 0.4 * three_year
            - 0.2 * (consistency_std if consistency_std is not None else 0.0)
        )

        entries.append({
            "scheme_code":       code,
            "scheme_name":       latest.scheme_name,
            "current_nav":       latest.nav,
            "one_year_return":   one_year,
            "three_year_return": three_year,
            "consistency_std":   round(consistency_std, 2) if consistency_std else None,
            "composite_score":   round(score, 2),
            "best_fund":         False,
        })

    entries.sort(key=lambda e: e["composite_score"], reverse=True)
    if entries:
        entries[0]["best_fund"] = True

    return entries


async def _monthly_return_std(
    scheme_code: str,
    session: AsyncSession,
    years: int = 3,
) -> float | None:
    """Std dev of monthly NAV returns over the last *years* years from DB."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=years * 365)
    rows = (await session.execute(
        select(MutualFundNAV)
        .where(
            MutualFundNAV.scheme_code == scheme_code,
            MutualFundNAV.recorded_at >= cutoff,
        )
        .order_by(MutualFundNAV.recorded_at)
    )).scalars().all()

    if len(rows) < 4:
        return None

    df = pd.DataFrame([{"date": r.recorded_at, "nav": r.nav} for r in rows])
    df["date"]  = pd.to_datetime(df["date"])
    df["month"] = df["date"].dt.to_period("M")
    monthly = df.groupby("month", sort=True)["nav"].last().reset_index()
    monthly["ret"] = monthly["nav"].pct_change()
    monthly = monthly.dropna(subset=["ret"])

    if len(monthly) < 3:
        return None

    return round(float(monthly["ret"].std() * 100), 2)


# ── 4. get_mf_buy_signal ──────────────────────────────────────────────────────

async def get_mf_buy_signal(
    scheme_code: str,
    session: AsyncSession,
) -> dict:
    """Generate a BUY / HOLD signal for a mutual fund scheme.

    Buy conditions (ALL must be met)
    ---------------------------------
    1. one_year_return > 15 %
    2. current_nav is more than 5 % below the 52-week high (dip opportunity)
    3. India VIX < 20 (favorable market conditions)

    Hold conditions
    ---------------
    Any condition above is not satisfied.

    Returns
    -------
    dict: signal, reason, current_nav, one_year_return, high_52w, vix
    """
    # ── 1. Get latest NAV from DB (or fetch fresh) ────────────────────────────
    latest = (await session.execute(
        select(MutualFundNAV)
        .where(MutualFundNAV.scheme_code == scheme_code)
        .order_by(desc(MutualFundNAV.recorded_at))
        .limit(1)
    )).scalar_one_or_none()

    if latest is None:
        await fetch_and_save_nav(scheme_code, session)
        latest = (await session.execute(
            select(MutualFundNAV)
            .where(MutualFundNAV.scheme_code == scheme_code)
            .order_by(desc(MutualFundNAV.recorded_at))
            .limit(1)
        )).scalar_one_or_none()

    if latest is None:
        return {
            "scheme_code":     scheme_code,
            "signal":          "HOLD",
            "reason":          "No NAV data available",
            "current_nav":     None,
            "one_year_return": None,
            "high_52w":        None,
            "vix":             None,
        }

    current_nav    = latest.nav
    one_year_return = latest.one_year_return

    # ── 2. 52-week high from DB ───────────────────────────────────────────────
    cutoff_52w = datetime.datetime.utcnow() - datetime.timedelta(days=365)
    high_52w_row = (await session.execute(
        select(func.max(MutualFundNAV.nav))
        .where(
            MutualFundNAV.scheme_code == scheme_code,
            MutualFundNAV.recorded_at >= cutoff_52w,
        )
    )).scalar_one_or_none()

    # If DB has no 52-week history, use current NAV as fallback
    high_52w = float(high_52w_row) if high_52w_row else current_nav

    dip_pct = (high_52w - current_nav) / high_52w * 100 if high_52w > 0 else 0.0

    # ── 3. India VIX ─────────────────────────────────────────────────────────
    vix: float | None = None
    try:
        from crawler.india_price_feed import fetch_india_vix
        loop = asyncio.get_event_loop()
        vix  = await loop.run_in_executor(None, fetch_india_vix)
    except Exception as exc:
        logger.debug(f"get_mf_buy_signal: VIX fetch failed — {exc}")

    # ── 4. Signal logic ────────────────────────────────────────────────────────
    good_return = one_year_return is not None and one_year_return > 15.0
    good_dip    = dip_pct > 5.0
    good_vix    = vix is not None and vix < 20.0

    if good_return and good_dip and good_vix:
        signal = "BUY"
        reason = (
            f"1-year return {one_year_return:.1f}% > 15%; "
            f"NAV is {dip_pct:.1f}% below 52-week high; "
            f"India VIX={vix:.1f} < 20 (favourable market)"
        )
    elif not good_return and one_year_return is not None:
        signal = "HOLD"
        reason = f"1-year return {one_year_return:.1f}% does not exceed the 15% threshold"
    elif not good_dip:
        signal = "HOLD"
        reason = (
            f"NAV is only {dip_pct:.1f}% below 52-week high — "
            "not enough of a dip (need > 5%)"
        )
    elif not good_vix:
        vix_str = f"{vix:.1f}" if vix is not None else "N/A"
        signal  = "HOLD"
        reason  = f"India VIX={vix_str} ≥ 20 — market conditions are not favourable"
    else:
        signal = "HOLD"
        reason = "Insufficient data to generate a BUY signal"

    logger.info(
        f"MF signal {scheme_code}: {signal}  1Y={one_year_return}%  "
        f"dip={dip_pct:.1f}%  VIX={vix}"
    )
    return {
        "scheme_code":      scheme_code,
        "scheme_name":      latest.scheme_name,
        "signal":           signal,
        "reason":           reason,
        "current_nav":      current_nav,
        "one_year_return":  one_year_return,
        "high_52w":         round(high_52w, 4),
        "dip_from_high_pct": round(dip_pct, 2),
        "vix":              vix,
    }


# ── SIP projection (planning tool — no DB) ────────────────────────────────────

def project_sip(
    monthly_amount: float,
    expected_annual_return_pct: float,
    months: int,
) -> dict:
    """Project SIP corpus using a constant assumed CAGR (planning tool only).

    Does NOT use historical NAV data.  Use simulate_sip() for realistic results.
    """
    if months <= 0 or monthly_amount <= 0:
        raise ValueError("monthly_amount and months must be positive")

    annual_rate  = expected_annual_return_pct / 100.0
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
        "monthly_amount":      round(monthly_amount, 2),
        "months":              months,
        "assumed_cagr_pct":    expected_annual_return_pct,
        "total_invested":      round(total_invested, 2),
        "projected_value":     round(future_value, 2),
        "absolute_return":     round(absolute_return, 2),
        "absolute_return_pct": round(absolute_return / total_invested * 100, 2),
    }
