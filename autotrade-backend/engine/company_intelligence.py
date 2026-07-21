"""Normalized company-intelligence aggregator (Phase U2).

Single entry point: get_company_intelligence(symbol) -> dict.

Fuses three layers with different reliability/freshness profiles into one
normalized schema, so a caller (Phase U3's LLM tool) needs to know about one
function, not eight separate Upstox endpoints plus the existing yfinance/
Screener/FundamentalData paths:

  1. Upstox (crawler/upstox_data.py) — the ONLY source in this codebase for
     financial statements (revenue/operating-profit/net-profit/cash-flow),
     corporate actions, competitors, and shareholding trend history. Primary
     for valuation/quality ratios too, but NOT complete there — Upstox's
     key-ratios endpoint does not return Debt/Equity or Current Ratio at all
     (confirmed live against RELIANCE: only P/E, P/B, ROA, ROE, ROCE, Quick
     Ratio, EV/EBITDA come back), so those two fields always fall through to
     the sources below even when Upstox is otherwise healthy.
  2. FundamentalData (db/models.py) — weekly-cached DB row; fills whatever
     Upstox's ratio set is missing.
  3. yfinance/Screener (engine/fundamental_analyzer.py) — same two sources
     engine/agent/decision_engine.py's existing _tool_fundamentals() already
     uses; live fallback of last resort.

Every numeric ratio and every section tracks its own source, and the
top-level response carries completeness (0.0-1.0) + failed_sections, so a
caller can tell "this company has no cash-flow data on Upstox" apart from
"Upstox happened to be down right now" — crawler/upstox_data.py already
fails soft to {}/[] on any error, so this layer is what turns that silence
into an honest, inspectable signal instead of a plain empty dict.
"""
from __future__ import annotations

from datetime import datetime, timezone

from utils.logger import logger

_SECTIONS = (
    "identity", "financial_statements", "valuation",
    "quality", "ownership", "corporate_events", "competitors",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _safe(coro):
    try:
        return await coro
    except Exception as exc:
        logger.debug(f"[company_intelligence] section fetch failed: {exc}")
        return None


async def _fetch_upstox(symbol: str) -> dict:
    """Fire every Upstox endpoint concurrently — sequential calls here would
    risk up to ~8x12s against one shared per-endpoint timeout."""
    import asyncio
    from crawler.upstox_data import (
        get_company_profile, get_income_statement, get_balance_sheet,
        get_cash_flow, get_key_ratios, get_shareholding,
        get_corporate_actions, get_competitors,
    )
    (
        profile, income, balance, cashflow,
        ratios, shareholding, corp_actions, competitors,
    ) = await asyncio.gather(
        _safe(get_company_profile(symbol)),
        _safe(get_income_statement(symbol)),
        _safe(get_balance_sheet(symbol)),
        _safe(get_cash_flow(symbol)),
        _safe(get_key_ratios(symbol)),
        _safe(get_shareholding(symbol)),
        _safe(get_corporate_actions(symbol)),
        _safe(get_competitors(symbol)),
    )
    return {
        "profile":      profile or {},
        "income":       income or {},
        "balance":      balance or {},
        "cashflow":     cashflow or {},
        "ratios":       ratios or [],
        "shareholding": shareholding or [],
        "corp_actions": corp_actions or [],
        "competitors":  competitors or [],
    }


async def _fetch_fundamental_data_row(symbol: str):
    try:
        from db.database import AsyncSessionLocal
        from db.models import FundamentalData
        from sqlalchemy import select
        bare = symbol.replace(".NS", "").replace(".BO", "")
        async with AsyncSessionLocal() as s:
            return (await s.execute(select(FundamentalData).where(
                FundamentalData.symbol.in_([symbol, bare])
            ).limit(1))).scalar_one_or_none()
    except Exception as exc:
        logger.debug(f"[company_intelligence] FundamentalData lookup failed: {exc}")
        return None


async def _fetch_live_fallback(symbol: str) -> dict:
    """Same two sources engine/agent/decision_engine.py's _tool_fundamentals()
    already uses — reused, not reimplemented."""
    try:
        import asyncio
        from engine.fundamental_analyzer import fetch_fundamentals_yfinance, fetch_fundamentals_screener
        bare = symbol.replace(".NS", "").replace(".BO", "")
        yf_data, sc_data = await asyncio.gather(
            asyncio.to_thread(fetch_fundamentals_yfinance, symbol),
            fetch_fundamentals_screener(bare),
        )
        return {**(yf_data or {}), **(sc_data or {})}
    except Exception as exc:
        logger.debug(f"[company_intelligence] live fallback failed: {exc}")
        return {}


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace("%", "").replace(",", "").strip())
    except ValueError:
        return None


def _latest_by_category(rows: list | None, category: str) -> dict | None:
    """income_statement/cash_flow rows look like
    [{"category": "revenue", "history": [{"value", "period", "change"}, ...]}]."""
    for row in rows or []:
        if row.get("category") == category:
            hist = row.get("history") or []
            return hist[0] if hist else None
    return None


def _ratio_dict(rows: list) -> dict:
    """key-ratios comes back as [{"name": "P/E", "company_value": "20.31", ...}]."""
    out: dict = {}
    for row in rows or []:
        name = row.get("name")
        if name is not None:
            out[name] = _to_float(row.get("company_value"))
    return out


def _shareholding_dict(rows: list) -> dict:
    """shareholding comes back as
    [{"category": "promoters", "history": [{"value": 50.48, "period": "Jun 2026"}, ...]}],
    history sorted most-recent-first."""
    out: dict = {}
    for row in rows or []:
        cat, hist = row.get("category"), row.get("history") or []
        if not cat or not hist:
            continue
        latest = hist[0]
        prev = hist[1] if len(hist) > 1 else None
        change_qoq = None
        if prev and latest.get("value") is not None and prev.get("value") is not None:
            change_qoq = round(latest["value"] - prev["value"], 2)
        out[cat] = {
            "latest_pct":    latest.get("value"),
            "latest_period": latest.get("period"),
            "trend":         [h.get("value") for h in hist],   # most-recent-first
            "change_qoq":    change_qoq,
        }
    return out


def _merge_ratios(upstox_ratios: dict, fdata_row, live: dict) -> tuple[dict, dict]:
    """Per-field merge, Upstox preferred, so a section doesn't get tagged
    "source: UPSTOX" when 2 of its 6 fields (D/E, Current Ratio) are actually
    silently missing from that source. Returns (values, per_field_source)."""
    merged: dict = {}
    src: dict = {}

    def take(key: str, u_val, f_val, l_val) -> None:
        if u_val is not None:
            merged[key], src[key] = u_val, "UPSTOX"
        elif f_val is not None:
            merged[key], src[key] = f_val, "FUNDAMENTAL_DATA"
        elif l_val is not None:
            merged[key], src[key] = l_val, "YFINANCE_SCREENER"
        else:
            merged[key], src[key] = None, "UNAVAILABLE"

    f = fdata_row
    take("pe",              upstox_ratios.get("P/E"),  getattr(f, "pe_ratio", None),       live.get("pe_ratio"))
    take("pb",               upstox_ratios.get("P/B"),  getattr(f, "pb_ratio", None),       live.get("pb_ratio"))
    take("roe",              upstox_ratios.get("ROE"),  getattr(f, "roe", None),            live.get("roe"))
    take("roce",             upstox_ratios.get("ROCE"), getattr(f, "roce", None),           None)  # live fallback has no ROCE field
    take("debt_to_equity",   upstox_ratios.get("D/E"),  getattr(f, "debt_to_equity", None), live.get("debt_to_equity"))
    take("current_ratio",    upstox_ratios.get("Current Ratio"), getattr(f, "current_ratio", None), live.get("current_ratio"))
    merged["market_cap_cr"] = getattr(f, "market_cap_cr", None) or live.get("market_cap_cr")
    return merged, src


async def get_company_intelligence(symbol: str) -> dict:
    """Normalized company-intelligence snapshot for the LLM tradeability loop.
    Upstox primary; FundamentalData DB row + live yfinance/Screener fill
    whatever Upstox doesn't return. Financial statements / corporate actions /
    competitors have NO fallback — nothing else in this codebase has that
    data, so those sections report UNAVAILABLE rather than fabricating one.
    """
    bare = symbol.replace(".NS", "").replace(".BO", "").upper()
    upstox = await _fetch_upstox(bare)
    sections: dict[str, dict] = {}
    out: dict = {"symbol": bare}

    # ── identity ────────────────────────────────────────────────────────────
    if upstox["profile"]:
        p = upstox["profile"]
        out["identity"] = {
            "sector":               p.get("sector"),
            "business_description": p.get("company_profile"),
        }
        sections["identity"] = {"source": "UPSTOX", "retrieved_at": _now_iso()}
    else:
        out["identity"] = None
        sections["identity"] = {"source": "UNAVAILABLE", "retrieved_at": None}

    # ── financial_statements (Upstox-only, no fallback exists anywhere) ─────
    inc_rows = upstox["income"].get("income_statement") if upstox["income"] else None
    cf_rows  = upstox["cashflow"].get("cash_flow")       if upstox["cashflow"] else None
    bs_hist  = upstox["balance"].get("history")          if upstox["balance"] else None
    if inc_rows or cf_rows or bs_hist:
        out["financial_statements"] = {
            "units":               "crore",
            "revenue":             _latest_by_category(inc_rows, "revenue"),
            "operating_profit":    _latest_by_category(inc_rows, "operating_profit"),
            "net_profit":          _latest_by_category(inc_rows, "net_profit"),
            "operating_cash_flow": _latest_by_category(cf_rows, "operating"),
            "investing_cash_flow": _latest_by_category(cf_rows, "investing"),
            "financing_cash_flow": _latest_by_category(cf_rows, "financing"),
            "total_assets":        (bs_hist[0] if bs_hist else {}).get("total_asset"),
            "total_liabilities":   (bs_hist[0] if bs_hist else {}).get("total_liability"),
        }
        sections["financial_statements"] = {"source": "UPSTOX", "retrieved_at": _now_iso()}
    else:
        out["financial_statements"] = None
        sections["financial_statements"] = {"source": "UNAVAILABLE", "retrieved_at": None}

    # ── valuation / quality (per-field merge: Upstox -> FundamentalData -> live) ─
    upstox_ratios = _ratio_dict(upstox["ratios"])
    fdata_row = await _fetch_fundamental_data_row(bare)
    live_ratios: dict = {}
    # Only pay for the live yfinance/Screener fetch if Upstox+DB still leave a gap.
    provisional, _ = _merge_ratios(upstox_ratios, fdata_row, {})
    if any(provisional.get(k) is None for k in ("pe", "pb", "roe", "debt_to_equity", "current_ratio")):
        live_ratios = await _fetch_live_fallback(bare)
    ratios, ratio_src = _merge_ratios(upstox_ratios, fdata_row, live_ratios)

    out["valuation"] = {"pe": ratios["pe"], "pb": ratios["pb"], "market_cap_cr": ratios["market_cap_cr"]}
    sections["valuation"] = {
        "source":       ratio_src.get("pe", "UNAVAILABLE"),
        "field_sources": {k: ratio_src[k] for k in ("pe", "pb")},
        "retrieved_at": _now_iso() if any(v != "UNAVAILABLE" for v in ratio_src.values()) else None,
    }

    out["quality"] = {
        "roe": ratios["roe"], "roce": ratios["roce"],
        "debt_to_equity": ratios["debt_to_equity"], "current_ratio": ratios["current_ratio"],
    }
    sections["quality"] = {
        "source":        ratio_src.get("roe", "UNAVAILABLE"),
        "field_sources": {k: ratio_src[k] for k in ("roe", "roce", "debt_to_equity", "current_ratio")},
        "retrieved_at":  _now_iso() if any(v != "UNAVAILABLE" for v in ratio_src.values()) else None,
    }
    if all(v == "UNAVAILABLE" for v in ratio_src.values()):
        out["valuation"] = None
        out["quality"] = None
        sections["valuation"]["source"] = sections["quality"]["source"] = "UNAVAILABLE"

    # ── ownership (Upstox shareholding -> FundamentalData partial) ──────────
    sh = _shareholding_dict(upstox["shareholding"])
    if sh:
        out["ownership"] = {
            "promoter":     sh.get("promoters"),
            "fii":          sh.get("fii"),
            "dii":          sh.get("other_dii"),
            "mutual_funds": sh.get("mutual_funds"),
            "public":       sh.get("retail_and_other"),
        }
        sections["ownership"] = {"source": "UPSTOX", "retrieved_at": _now_iso()}
    elif fdata_row is not None and (fdata_row.promoter_holding is not None or fdata_row.fii_holding is not None):
        out["ownership"] = {
            "promoter":     {"latest_pct": fdata_row.promoter_holding, "trend": None},
            "fii":          {"latest_pct": fdata_row.fii_holding, "trend": None},
            "dii": None, "mutual_funds": None, "public": None,
        }
        sections["ownership"] = {
            "source": "FUNDAMENTAL_DATA",
            "retrieved_at": fdata_row.last_updated.isoformat() if fdata_row.last_updated else None,
        }
    else:
        out["ownership"] = None
        sections["ownership"] = {"source": "UNAVAILABLE", "retrieved_at": None}

    # ── corporate_events / competitors (Upstox-only, no fallback) ───────────
    out["corporate_events"] = upstox["corp_actions"] or None
    sections["corporate_events"] = {
        "source": "UPSTOX" if upstox["corp_actions"] else "UNAVAILABLE",
        "retrieved_at": _now_iso() if upstox["corp_actions"] else None,
    }

    out["competitors"] = upstox["competitors"] or None
    sections["competitors"] = {
        "source": "UPSTOX" if upstox["competitors"] else "UNAVAILABLE",
        "retrieved_at": _now_iso() if upstox["competitors"] else None,
    }

    # ── metadata ─────────────────────────────────────────────────────────────
    failed = [k for k in _SECTIONS if sections[k]["source"] == "UNAVAILABLE"]
    completeness = round(1 - len(failed) / len(_SECTIONS), 2)
    status = "healthy" if completeness == 1.0 else ("partial" if completeness > 0 else "unavailable")
    out["metadata"] = {
        "status":          status,
        "completeness":    completeness,
        "failed_sections": failed,
        "sections":        sections,
        "retrieved_at":    _now_iso(),
    }
    return out
