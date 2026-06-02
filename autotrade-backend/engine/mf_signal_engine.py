"""Mutual-fund signal engine — scores portfolio MFs using the same MasterContext.

Reads MF holdings from tracker_holdings (rows with the `MF:{scheme_code}`
symbol convention) and 90-day NAV history from mfapi.in.

Public API
----------
get_portfolio_mf_holdings(session)            -> list[dict]
fetch_mf_nav_history(scheme_code, days)       -> list[float]
score_mf_universe(portfolio_mfs, ctx, session)-> list[MFScore]
persist_mf_scores(scores, session)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from utils.logger import logger

SECTOR_MF_KEYWORDS = {
    "Banking": ["banking", "financial services", "bank"],
    "IT":      ["technology", "digital", " it "],
    "Pharma":  ["pharma", "healthcare"],
    "FMCG":    ["fmcg", "consumption", "consumer"],
    "Infra":   ["infrastructure", "infra"],
    "Energy":  ["energy", "power"],
}

MF_CATEGORY_BASE_SCORE = {
    "Large Cap": 60, "Large & Mid Cap": 65, "Flexi Cap": 70, "Mid Cap": 55,
    "Small Cap": 45, "ELSS": 65, "Index": 70, "Index Fund": 70, "ETF": 72,
    "Sectoral": 50, "Debt": 40, "Liquid": 20, "Hybrid": 55, "Equity": 60,
}


@dataclass
class MFScore:
    scheme_code:      str
    scheme_name:      str
    category:         str
    nav_trend_score:  float
    sector_alignment: float
    category_score:   float
    master_score:     float
    signal:           str
    reasoning:        str


async def get_portfolio_mf_holdings(session: AsyncSession) -> list[dict]:
    """Return all mutual-fund holdings across tracker portfolios.

    MF rows use a `MF:{scheme_code}` symbol prefix in tracker_holdings.
    """
    from db.models import TrackerHolding

    rows = (await session.execute(
        select(TrackerHolding).where(TrackerHolding.symbol.like("MF:%"))
    )).scalars().all()

    out = []
    for h in rows:
        code = h.symbol[3:]  # strip 'MF:'
        out.append({
            "scheme_code": code,
            "scheme_name": h.company_name or code,
            "category":    h.sector or "Flexi Cap",
            "quantity":    h.quantity,
            "avg_buy":     h.avg_buy_price,
        })
    return out


async def fetch_mf_nav_history(scheme_code: str, days: int = 90) -> list[float]:
    """Fetch NAV history (oldest→newest) from mfapi.in.

    Delegates to ``utils.nav_cache.get_nav_history`` so this function and
    every other MF NAV consumer share one cache layer + one ``httpx.AsyncClient``
    instead of the three independent process-local caches they had before.
    """
    from utils.nav_cache import get_nav_history
    return await get_nav_history(scheme_code, days)


def _match_sector(scheme_name: str) -> str | None:
    n = scheme_name.lower()
    for sector, kws in SECTOR_MF_KEYWORDS.items():
        if any(kw in n for kw in kws):
            return sector
    return None


async def score_mf_universe(portfolio_mfs: list, ctx, session: AsyncSession) -> list:
    scored: list = []
    for mf in portfolio_mfs:
        scheme_code = mf["scheme_code"]
        scheme_name = mf["scheme_name"]
        category    = mf.get("category", "Flexi Cap")

        # 1. NAV trend
        navs = await fetch_mf_nav_history(scheme_code, days=90)
        if len(navs) >= 30:
            r30 = (navs[-1] - navs[-30]) / navs[-30] * 100 if navs[-30] else 0
            r90 = (navs[-1] - navs[0]) / navs[0] * 100 if navs[0] else 0
            nav_trend_score = max(-50, min(50, r30 * 0.6 + r90 * 0.4))
        else:
            nav_trend_score = 0.0

        # 2. Sector alignment (sectoral funds only)
        sector_alignment = 0.0
        matched = _match_sector(scheme_name)
        if matched:
            sector_alignment = ctx.sectors.sector_biases.get(matched, 0) * 20

        # 3. Category base, adjusted for macro
        base = MF_CATEGORY_BASE_SCORE.get(category, 55)
        mb = ctx.macro.total_macro_bias
        if mb > 1:
            if "Debt" in category or "Liquid" in category: base -= 15
            else:                                            base += 5
        elif mb < -1:
            if "Debt" in category or "Hybrid" in category:   base += 15
            elif category in ("Small Cap", "Mid Cap"):       base -= 20
        category_score = base

        master_score = (
            nav_trend_score * 0.40 +
            sector_alignment * 0.20 +
            category_score   * 0.40
        )

        if   master_score >= 60: signal = "ADD"
        elif master_score >= 30: signal = "HOLD"
        else:                    signal = "REDUCE"

        reasoning = (
            f"NAV trend ({nav_trend_score:+.1f}), "
            f"sector alignment ({sector_alignment:+.1f}), "
            f"category fit ({category_score:.0f}/100). "
            f"Macro bias {mb:+d}."
        )

        scored.append(MFScore(
            scheme_code=scheme_code, scheme_name=scheme_name, category=category,
            nav_trend_score=round(nav_trend_score, 1),
            sector_alignment=round(sector_alignment, 1),
            category_score=round(category_score, 1),
            master_score=round(master_score, 2), signal=signal, reasoning=reasoning,
        ))

    scored.sort(key=lambda x: x.master_score, reverse=True)
    return scored


async def persist_mf_scores(scores: list, session: AsyncSession) -> None:
    from db.models import MFIntelligenceScore

    for s in scores:
        session.add(MFIntelligenceScore(
            scheme_code=s.scheme_code, scheme_name=s.scheme_name,
            category=s.category, nav_trend_score=s.nav_trend_score,
            sector_alignment=s.sector_alignment, category_score=s.category_score,
            master_score=s.master_score, signal=s.signal,
            reasoning={"text": s.reasoning},
        ))
    await session.commit()
    logger.info(f"[mf] persisted {len(scores)} MF scores")
