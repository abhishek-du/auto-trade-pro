"""Portfolio Doctor — AI-powered health analysis for the personal portfolio tracker.

Runs 7 diagnostic modules (concentration, risk, diversification, tax, performance,
sector timing, position sizing) and generates an AI narrative via Groq.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from enum import Enum
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from utils.config import settings
from utils.logger import logger


# ── Severity & Finding dataclasses ───────────────────────────────────────────

class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING  = "WARNING"
    INFO     = "INFO"
    GOOD     = "GOOD"


@dataclass
class Finding:
    module:   str
    severity: Severity
    title:    str
    detail:   str
    metric:   dict
    actions:  list
    stocks:   list
    priority: int

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class DiagnosisReport:
    portfolio_id:  str
    overall_score: int
    overall_grade: str
    summary:       str
    findings:      list
    ai_narrative:  str
    quick_wins:    list
    data_snapshot: dict
    generated_at:  str
    is_ai_generated: bool

    def to_dict(self) -> dict:
        return {
            "portfolio_id":   self.portfolio_id,
            "overall_score":  self.overall_score,
            "overall_grade":  self.overall_grade,
            "summary":        self.summary,
            "findings":       [f.to_dict() if isinstance(f, Finding) else f for f in self.findings],
            "ai_narrative":   self.ai_narrative,
            "quick_wins":     self.quick_wins,
            "data_snapshot":  self.data_snapshot,
            "generated_at":   self.generated_at,
            "is_ai_generated": self.is_ai_generated,
        }


# ── Helper: display symbol ────────────────────────────────────────────────────

def _disp(symbol: str) -> str:
    return symbol.replace(".NS", "").replace(".BO", "")


def _build_sector_alloc(portfolio_summary: dict) -> dict:
    """Convert by_sector list from portfolio_summary to dict keyed by sector."""
    by_sector = portfolio_summary.get("allocation", {}).get("by_sector", [])
    return {
        e["sector"]: {"weight_pct": e.get("weight", 0), "value": e.get("value", 0)}
        for e in by_sector
    }


# ── MODULE 1: CONCENTRATION ───────────────────────────────────────────────────

def check_concentration(
    portfolio_summary: dict,
    allocation_map: dict,
) -> list[Finding]:
    holdings     = portfolio_summary.get("holdings", [])
    sector_alloc = _build_sector_alloc(portfolio_summary)
    findings: list[Finding] = []

    # Check 1: single stock > 25%
    for h in holdings:
        w = h.get("weight", 0)
        if w > 25:
            sym = _disp(h.get("symbol", ""))
            findings.append(Finding(
                module="CONCENTRATION",
                severity=Severity.CRITICAL if w > 40 else Severity.WARNING,
                title=f"{sym} is {w:.1f}% of your portfolio",
                detail=(
                    f"You have ₹{h.get('current_value', 0):,.0f} in a single stock. "
                    f"A sharp fall in {sym} could significantly damage your portfolio."
                ),
                metric={"stock": sym, "weight_pct": w,
                        "value": h.get("current_value", 0), "threshold": 25},
                actions=[
                    f"Consider trimming {sym} to below 20% of portfolio",
                    "Reinvest proceeds into underrepresented sectors",
                    f"If {sym} has LTCG, harvest up to ₹1.25L tax-free first",
                ],
                stocks=[h.get("symbol", "")],
                priority=1,
            ))

    # Check 2: single sector > 40%
    for sector, data in sector_alloc.items():
        w = data.get("weight_pct", 0)
        if w > 40:
            findings.append(Finding(
                module="CONCENTRATION",
                severity=Severity.CRITICAL if w > 60 else Severity.WARNING,
                title=f"{sector} sector is {w:.1f}% of portfolio",
                detail=(
                    f"Over-exposure to one sector amplifies risk. "
                    f"A downturn in {sector} (regulatory, macro, or cyclical) "
                    f"would disproportionately impact your returns."
                ),
                metric={"sector": sector, "weight_pct": w,
                        "value": data.get("value", 0), "threshold": 40},
                actions=[
                    f"Reduce {sector} allocation towards 25–30%",
                    "Add stocks from different sectors: FMCG, Pharma, or IT",
                    "Consider a diversified index fund to broaden exposure",
                ],
                stocks=[],
                priority=1,
            ))

    # Check 3: 100% equity, no debt/gold
    equity_pct = (
        allocation_map.get("large_cap",  {}).get("total_pct", 0) +
        allocation_map.get("mid_cap",    {}).get("total_pct", 0) +
        allocation_map.get("small_cap",  {}).get("total_pct", 0)
    )
    debt_pct = allocation_map.get("debt", {}).get("total_pct", 0)
    gold_pct = allocation_map.get("gold", {}).get("total_pct", 0)

    if equity_pct > 95 and debt_pct < 5:
        findings.append(Finding(
            module="CONCENTRATION",
            severity=Severity.WARNING,
            title="100% equity — no debt or gold as cushion",
            detail=(
                "An all-equity portfolio experiences full market volatility. "
                "During corrections like March 2020 or Oct 2008, a 30–50% drawdown "
                "is possible with no buffer assets."
            ),
            metric={"equity_pct": equity_pct, "debt_pct": debt_pct, "gold_pct": gold_pct},
            actions=[
                "Add 10–20% debt allocation (liquid fund or short-term debt fund)",
                "Consider 5–10% gold ETF or Sovereign Gold Bond for hedge",
                "This reduces volatility without significantly hurting long-term returns",
            ],
            stocks=[],
            priority=2,
        ))

    if not findings:
        findings.append(Finding(
            module="CONCENTRATION",
            severity=Severity.GOOD,
            title="Portfolio concentration is healthy",
            detail="No single stock dominates and sector allocation looks balanced.",
            metric={},
            actions=[],
            stocks=[],
            priority=10,
        ))

    return findings


# ── MODULE 2: RISK QUALITY ────────────────────────────────────────────────────

def check_risk_quality(
    holdings: list[dict],
    fundamentals: dict,
    risk_score: dict,
) -> list[Finding]:
    findings: list[Finding] = []
    risky_stocks = []

    for h in holdings:
        symbol = h.get("symbol", "")
        fund   = fundamentals.get(symbol, {})
        issues = []

        pe        = fund.get("pe_ratio")
        de        = fund.get("debt_to_equity")
        roe       = fund.get("roe")           # already in %
        rev_growth = fund.get("revenue_growth_ttm")  # already in %

        if pe and pe > 80:
            issues.append(f"PE ratio {pe:.0f}x — extremely expensive vs market avg 20–25x")
        if de and de > 3.0:
            issues.append(f"Debt/Equity {de:.1f}x — highly leveraged, risky in rate-up cycle")
        if roe is not None and roe < 0:
            issues.append(f"Negative ROE ({roe:.1f}%) — company destroying shareholder value")
        if rev_growth is not None and rev_growth < -10:
            issues.append(f"Revenue declining {rev_growth:.1f}% — business deteriorating")

        if issues:
            risky_stocks.append({
                "symbol": _disp(symbol),
                "weight": h.get("weight", 0),
                "issues": issues,
            })

    if risky_stocks:
        total_risky = sum(s["weight"] for s in risky_stocks)
        findings.append(Finding(
            module="RISK_QUALITY",
            severity=Severity.CRITICAL if total_risky > 20 else Severity.WARNING,
            title=f"{len(risky_stocks)} stocks have fundamental red flags",
            detail=(
                f"Stocks with poor fundamentals represent {total_risky:.1f}% of your portfolio. "
                f"These carry elevated risk of permanent capital loss, not just temporary volatility."
            ),
            metric={
                "risky_stock_count": len(risky_stocks),
                "total_risky_weight": total_risky,
                "stocks": risky_stocks[:3],
            },
            actions=[
                f"Review and consider exiting: {', '.join(s['symbol'] for s in risky_stocks[:3])}",
                "High PE stocks are vulnerable to earnings misses — check Q4 results",
                "High debt stocks are vulnerable if RBI keeps rates elevated",
                "Negative ROE companies are destroying your capital slowly",
            ],
            stocks=[s["symbol"] for s in risky_stocks],
            priority=1,
        ))

    score = risk_score.get("score", 5)
    label = risk_score.get("label", "Moderate")
    if score > 7.5:
        findings.append(Finding(
            module="RISK_QUALITY",
            severity=Severity.WARNING,
            title=f"High portfolio risk score: {score:.1f}/10 ({label})",
            detail=(
                "Your portfolio is heavily weighted towards mid and small cap stocks. "
                "These outperform in bull markets but can fall 40–60% in corrections."
            ),
            metric={"risk_score": score, "label": label},
            actions=[
                "Shift 10–15% from small/mid cap to large cap or index funds",
                "Large caps recover faster from market drawdowns",
                "Consider NIFTY 50 index fund as a stabiliser",
            ],
            stocks=[],
            priority=2,
        ))

    return findings


# ── MODULE 3: DIVERSIFICATION ─────────────────────────────────────────────────

def check_diversification(
    holdings: list[dict],
    allocation_map: dict,
    portfolio_total: float,
) -> list[Finding]:
    findings: list[Finding] = []
    n = len(holdings)

    if n < 5:
        findings.append(Finding(
            module="DIVERSIFICATION",
            severity=Severity.CRITICAL,
            title=f"Only {n} stocks — dangerously underdiversified",
            detail=(
                f"With fewer than 5 holdings, a single stock blowup could "
                f"wipe out 20–30% of your portfolio. This is speculation, not investing."
            ),
            metric={"holdings_count": n, "recommended_min": 8},
            actions=[
                "Add at least 3–5 more stocks from different sectors",
                "Consider a NIFTY 50 index fund to instantly diversify",
                "Target 10–15 quality stocks across 5+ sectors",
            ],
            stocks=[],
            priority=1,
        ))
    elif n > 30:
        findings.append(Finding(
            module="DIVERSIFICATION",
            severity=Severity.INFO,
            title=f"{n} holdings — consider consolidating",
            detail=(
                "Too many small positions dilute your best ideas and become hard to track. "
                "Beyond 25 stocks, new additions add negligible diversification benefit."
            ),
            metric={"holdings_count": n, "recommended_max": 25},
            actions=[
                "Exit your bottom 5–10 positions (lowest conviction ideas)",
                "Consolidate proceeds into your highest-conviction holdings",
                "Simpler portfolio is easier to monitor and rebalance",
            ],
            stocks=[],
            priority=4,
        ))
    else:
        findings.append(Finding(
            module="DIVERSIFICATION",
            severity=Severity.GOOD,
            title=f"{n} holdings — good diversification count",
            detail="Portfolio size is in the optimal 8–25 stock range for active investors.",
            metric={"holdings_count": n},
            actions=[],
            stocks=[],
            priority=10,
        ))

    missing = []
    if allocation_map.get("debt", {}).get("total_pct", 0) < 5:
        missing.append("Debt/Fixed Income")
    if allocation_map.get("gold", {}).get("total_pct", 0) < 3:
        missing.append("Gold (Gold ETF or SGB)")
    if allocation_map.get("international", {}).get("total_pct", 0) < 2:
        missing.append("International (US/Global fund)")

    if missing:
        findings.append(Finding(
            module="DIVERSIFICATION",
            severity=Severity.INFO,
            title=f"Missing asset classes: {', '.join(missing)}",
            detail=(
                "True diversification means owning assets that don't move together. "
                "Indian equity + debt + gold + international = much smoother ride."
            ),
            metric={"missing": missing},
            actions=[
                "Add Parag Parikh Flexi Cap (has US exposure) or US tech ETF",
                "Consider 5–10% Sovereign Gold Bonds (tax-free at maturity)",
                "A short-term debt fund provides stability during equity downturns",
            ],
            stocks=[],
            priority=3,
        ))

    return findings


# ── MODULE 4: TAX EFFICIENCY ──────────────────────────────────────────────────

def check_tax_efficiency(
    tax_summary,
    harvesting_ops: dict,
    holdings: list[dict],
) -> list[Finding]:
    from engine.tax_engine import TaxSummary
    findings: list[Finding] = []

    stcg_tax  = tax_summary.stcg_total_tax  if tax_summary else 0
    ltcg_tax  = tax_summary.ltcg_total_tax  if tax_summary else 0
    total_tax = tax_summary.total_tax       if tax_summary else 0

    # High STCG tax
    if stcg_tax > 10_000:
        findings.append(Finding(
            module="TAX_EFFICIENCY",
            severity=Severity.CRITICAL if stcg_tax > 50_000 else Severity.WARNING,
            title=f"STCG tax liability: ₹{stcg_tax:,.0f} this financial year",
            detail=(
                f"Short-term capital gains are taxed at 20%. You have already realised "
                f"₹{tax_summary.stcg_equity_net:,.0f} in STCG this FY. "
                f"Holding for 12 months converts this to LTCG (12.5%)."
            ),
            metric={"stcg_tax": stcg_tax, "stcg_gains": tax_summary.stcg_equity_net},
            actions=[
                "Delay selling profitable holdings until the 12-month mark",
                "For positions already sold: check loss harvesting to offset gains",
                f"Estimated savings if deferred: ₹{stcg_tax * 0.375:,.0f} (37.5% of STCG tax)",
            ],
            stocks=[],
            priority=2,
        ))

    # Loss harvesting opportunities
    harvest_ops = harvesting_ops.get("loss_harvest", [])
    if harvest_ops:
        total_saveable = sum(op.get("estimated_tax_saved", 0) for op in harvest_ops)
        top3 = harvest_ops[:3]
        actions = [
            f"Sell {top3[0]['symbol']} (loss ₹{abs(top3[0].get('unrealized_loss', 0)):,.0f}) — saves ₹{top3[0].get('estimated_tax_saved', 0):,.0f}",
            "Rebuy the same stock immediately — no wash-sale rule in India",
            "Do this before March 31 to utilise in current FY",
        ] if top3 else ["Review positions at a loss for harvesting opportunities"]
        findings.append(Finding(
            module="TAX_EFFICIENCY",
            severity=Severity.WARNING,
            title=f"Tax-loss harvesting can save ₹{total_saveable:,.0f} in taxes",
            detail=(
                f"You have {len(harvest_ops)} holdings sitting at a loss that could be "
                f"sold to offset your capital gains. India has no wash-sale rule — "
                f"you can rebuy the same stock immediately."
            ),
            metric={
                "opportunities": len(harvest_ops),
                "total_saveable": total_saveable,
                "top_stocks": [op["symbol"] for op in top3],
            },
            actions=actions,
            stocks=[op["symbol"] for op in top3],
            priority=2,
        ))

    # LTCG exemption underutilised
    gain_harvest        = harvesting_ops.get("gain_harvest", [])
    exemption_remaining = harvesting_ops.get("summary", {}).get("ltcg_exemption_remaining", 0)
    if exemption_remaining > 20_000 and gain_harvest:
        top_gain = gain_harvest[0]
        findings.append(Finding(
            module="TAX_EFFICIENCY",
            severity=Severity.INFO,
            title=f"₹{exemption_remaining:,.0f} LTCG exemption still available this FY",
            detail=(
                f"You can book up to ₹{exemption_remaining:,.0f} in long-term gains "
                f"completely tax-free. This resets your cost basis at no tax cost. "
                f"Repeat every March for compounding benefit."
            ),
            metric={
                "exemption_remaining": exemption_remaining,
                "bookable_stocks": len(gain_harvest),
            },
            actions=[
                f"Sell and rebuy {top_gain['symbol']} to book ₹{top_gain.get('bookable_gain', 0):,.0f} LTCG tax-free",
                "Do this before March 31 each year — it resets on April 1",
                "This strategy effectively lowers your future tax burden on these stocks",
            ],
            stocks=[op["symbol"] for op in gain_harvest[:3]],
            priority=3,
        ))

    # Timing suggestions
    timing = harvesting_ops.get("timing_suggestions", [])
    if timing:
        best = timing[0]
        findings.append(Finding(
            module="TAX_EFFICIENCY",
            severity=Severity.INFO,
            title=f"Hold {best['symbol']} {best['days_to_ltcg']} more days to save ₹{best['potential_saving']:,.0f}",
            detail=(
                f"Selling {best['symbol']} today incurs 20% STCG. "
                f"Waiting {best['days_to_ltcg']} more days converts it to 12.5% LTCG. "
                f"The tax saving is ₹{best['potential_saving']:,.0f}."
            ),
            metric={
                "symbol": best["symbol"],
                "days_to_ltcg": best["days_to_ltcg"],
                "saving": best["potential_saving"],
            },
            actions=[
                f"Do not sell {best['symbol']} for {best['days_to_ltcg']} more days",
                "Set a calendar reminder for the LTCG conversion date",
                f"At current price, you'll save ₹{best['potential_saving']:,.0f} in tax",
            ],
            stocks=[best["symbol"]],
            priority=3,
        ))

    return findings


# ── MODULE 5: PERFORMANCE ─────────────────────────────────────────────────────

def check_performance(
    portfolio_summary: dict,
    holdings: list[dict],
) -> list[Finding]:
    findings: list[Finding] = []
    summary    = portfolio_summary.get("summary", {})
    xirr       = summary.get("xirr") or 0
    abs_return = summary.get("total_pnl_pct", 0)

    NIFTY_CAGR_3Y = 13.5

    if xirr > 0 and xirr < NIFTY_CAGR_3Y - 3:
        findings.append(Finding(
            module="PERFORMANCE",
            severity=Severity.WARNING,
            title=f"XIRR {xirr:.1f}% underperforming NIFTY 50 ({NIFTY_CAGR_3Y}%)",
            detail=(
                f"Your portfolio is generating {NIFTY_CAGR_3Y - xirr:.1f}% less than a simple "
                f"NIFTY 50 index fund — with more risk and effort. "
                f"Active stock picking should beat the index, otherwise index funds are better."
            ),
            metric={"xirr": xirr, "nifty_benchmark": NIFTY_CAGR_3Y, "gap": NIFTY_CAGR_3Y - xirr},
            actions=[
                "Consider switching underperforming stocks to NIFTY 50 index funds",
                "Track why each stock is held — if reason is invalid, exit",
                "A NIFTY 50 index fund delivered similar returns with zero effort",
            ],
            stocks=[],
            priority=2,
        ))
    elif xirr > NIFTY_CAGR_3Y + 5:
        findings.append(Finding(
            module="PERFORMANCE",
            severity=Severity.GOOD,
            title=f"Excellent! XIRR {xirr:.1f}% is beating NIFTY 50 by {xirr - NIFTY_CAGR_3Y:.1f}%",
            detail="Your stock selection is generating alpha above the benchmark. Keep doing what's working.",
            metric={"xirr": xirr, "alpha": xirr - NIFTY_CAGR_3Y},
            actions=["Review what's working and apply same filters to new stock picks"],
            stocks=[],
            priority=10,
        ))

    # Persistent losers
    today = date.today()
    losers = sorted(
        [h for h in holdings if (h.get("pnl") or 0) < 0],
        key=lambda x: x.get("pnl_pct", 0),
    )
    for h in losers[:3]:
        pnl_pct = h.get("pnl_pct", 0)
        if pnl_pct < -20:
            fbd = h.get("first_buy_date")
            if not fbd:
                continue
            try:
                days = (today - date.fromisoformat(str(fbd))).days
            except Exception:
                continue
            if days > 180:
                sym = _disp(h.get("symbol", ""))
                findings.append(Finding(
                    module="PERFORMANCE",
                    severity=Severity.WARNING,
                    title=f"{sym} down {pnl_pct:.1f}% for over {days // 30} months",
                    detail=(
                        f"Holding a long-term loser has an opportunity cost — "
                        f"that capital could be working harder elsewhere. "
                        f"Ask: would you buy {sym} at today's price? If no, sell."
                    ),
                    metric={"symbol": sym, "pnl_pct": pnl_pct,
                            "days_held": days, "loss_amount": h.get("pnl", 0)},
                    actions=[
                        f"Objectively re-evaluate {sym}: has the thesis changed?",
                        "If company fundamentals have deteriorated, exit and redeploy",
                        f"Selling at a loss creates STCL/LTCL that can offset other gains",
                    ],
                    stocks=[h.get("symbol", "")],
                    priority=2,
                ))

    return findings


# ── MODULE 6: SECTOR TIMING ───────────────────────────────────────────────────

def check_sector_timing(
    portfolio_summary: dict,
    sector_data: dict,
) -> list[Finding]:
    findings: list[Finding] = []
    sector_alloc = _build_sector_alloc(portfolio_summary)

    for sector_key, sector_info in sector_data.items():
        mood       = sector_info.get("mood", "NEUTRAL")
        avg_change = sector_info.get("avg_change_pct", 0)
        pw         = sector_alloc.get(sector_key, {}).get("weight_pct", 0)

        if mood == "STRONGLY_BEARISH" and pw > 20:
            findings.append(Finding(
                module="SECTOR_TIMING",
                severity=Severity.WARNING,
                title=f"Overweight {sector_key} ({pw:.0f}%) — sector underperforming",
                detail=(
                    f"{sector_key} is {avg_change:+.1f}% today and in bearish mode. "
                    f"Your {pw:.0f}% allocation amplifies this sector's weakness."
                ),
                metric={"sector": sector_key, "portfolio_weight": pw,
                        "sector_mood": mood, "sector_change": avg_change},
                actions=[
                    f"Consider reducing {sector_key} exposure tactically",
                    "Rotate into sectors showing relative strength",
                    "This is a tactical flag — check sector fundamentals before acting",
                ],
                stocks=[],
                priority=3,
            ))
        elif mood == "STRONGLY_BULLISH" and pw < 2:
            top_stocks = [s.get("symbol", "").replace(".NS", "") for s in sector_info.get("stocks", [])[:3]]
            findings.append(Finding(
                module="SECTOR_TIMING",
                severity=Severity.INFO,
                title=f"{sector_key} surging but you have no exposure",
                detail=(
                    f"{sector_key} is {avg_change:+.1f}% and in strongly bullish mode. "
                    f"You have less than 2% exposure — missing this move."
                ),
                metric={"sector": sector_key, "portfolio_weight": pw, "sector_mood": mood},
                actions=[
                    f"Consider a small tactical position in {sector_key}",
                    f"Top stocks in {sector_key}: {', '.join(top_stocks)}",
                    "Do not chase performance — only add if fundamentals support it",
                ],
                stocks=[],
                priority=4,
            ))

    return findings


# ── MODULE 7: POSITION SIZING ─────────────────────────────────────────────────

def check_position_sizing(
    holdings: list[dict],
    portfolio_total: float,
) -> list[Finding]:
    findings: list[Finding] = []
    if not holdings or portfolio_total <= 0:
        return findings

    weights = [h.get("weight", 0) for h in holdings]
    max_w   = max(weights)
    min_w   = min(weights)

    tiny = [h for h in holdings if h.get("weight", 0) < 1.0]
    if len(tiny) > 3:
        names = [_disp(h.get("symbol", "")) for h in tiny[:5]]
        findings.append(Finding(
            module="POSITION_SIZING",
            severity=Severity.INFO,
            title=f"{len(tiny)} holdings are less than 1% each — dead weight",
            detail=(
                f"Tiny positions like {', '.join(names)} don't move your portfolio "
                f"even if they double. They waste attention and tracking effort."
            ),
            metric={
                "tiny_count": len(tiny),
                "tiny_stocks": names,
                "total_weight": sum(h.get("weight", 0) for h in tiny),
            },
            actions=[
                f"Exit or top up: {', '.join(names)}",
                "If conviction is high, add to them. If not, exit them.",
                "Minimum meaningful position: 3–4% of portfolio",
            ],
            stocks=[h.get("symbol", "") for h in tiny],
            priority=4,
        ))

    if max_w > 0 and min_w > 0:
        ratio = max_w / max(min_w, 0.1)
        if ratio > 20:
            findings.append(Finding(
                module="POSITION_SIZING",
                severity=Severity.INFO,
                title=f"Inconsistent sizing: largest position is {ratio:.0f}x the smallest",
                detail=(
                    "Your portfolio has no position sizing discipline — "
                    "some stocks are casual buys, others are concentrated bets. "
                    "This makes risk management nearly impossible."
                ),
                metric={"max_weight": max_w, "min_weight": min_w, "ratio": ratio},
                actions=[
                    "Define a position sizing rule: e.g. 5–10% per stock",
                    "Either top up small positions or exit them",
                    "Consistent sizing = consistent risk per trade",
                ],
                stocks=[],
                priority=3,
            ))

    return findings


# ── MODULE 8: HEALTH SCORE ────────────────────────────────────────────────────

def calculate_health_score(all_findings: list[Finding]) -> tuple[int, str]:
    score = 100
    for f in all_findings:
        if f.severity == Severity.CRITICAL: score -= 25
        elif f.severity == Severity.WARNING: score -= 10
        elif f.severity == Severity.INFO:    score -= 3
        elif f.severity == Severity.GOOD:    score += 2

    score = max(0, min(100, score))

    if score >= 85:   grade = "A"
    elif score >= 70: grade = "B"
    elif score >= 55: grade = "C"
    elif score >= 40: grade = "D"
    else:             grade = "F"

    return score, grade


# ── AI NARRATIVE ──────────────────────────────────────────────────────────────



async def generate_ai_narrative(
    findings: list[Finding],
    portfolio_summary: dict,
) -> tuple[str, bool]:
    """Return (narrative_text, is_ai_generated)."""
    summary    = portfolio_summary.get("summary", {})
    critical   = [f for f in findings if f.severity == Severity.CRITICAL]
    warnings   = [f for f in findings if f.severity == Severity.WARNING]
    goods      = [f for f in findings if f.severity == Severity.GOOD]

    findings_text = ""
    for f in sorted(findings, key=lambda x: x.priority):
        findings_text += f"\n[{f.severity.value}] {f.module}: {f.title}\n"
        findings_text += f"  Detail: {f.detail}\n"
        if f.actions:
            findings_text += f"  Actions: {'; '.join(f.actions[:2])}\n"

    system = (
        "You are Dr. Arjun, a senior Indian portfolio doctor. "
        "You analyse Indian retail investor portfolios and give honest, direct, actionable medical-style diagnoses. "
        "Speak like a trusted advisor — direct but not harsh. "
        "Use Indian financial context: NSE, BSE, SEBI, RBI, LTCG, STCG. "
        "Reference Indian investment products: SGB, ELSS, FD, liquid funds. "
        "Keep the report conversational — not a list of bullets. "
        "Maximum 4 paragraphs. Be specific with numbers."
    )
    user = (
        f"Here is the diagnosis data for this portfolio:\n\n"
        f"Portfolio Overview:\n"
        f"  Total Value: ₹{summary.get('current_value', 0):,.0f}\n"
        f"  Total Invested: ₹{summary.get('total_invested', 0):,.0f}\n"
        f"  Overall P&L: {summary.get('total_pnl_pct', 0):+.1f}%\n"
        f"  XIRR: {summary.get('xirr') or 0:.1f}%\n"
        f"  Holdings: {summary.get('holdings_count', 0)} stocks\n"
        f"  Critical Issues: {len(critical)}\n"
        f"  Warnings: {len(warnings)}\n"
        f"  Healthy aspects: {len(goods)}\n\n"
        f"Diagnosis Findings:\n{findings_text}\n\n"
        f"Write a portfolio health report in the style of a doctor giving a patient their annual health checkup results. "
        f"Start with an overall assessment, then address the most important issues with specific advice. "
        f"End with 2-3 positive observations and encouragement. "
        f"Do not use bullet points. Write in flowing paragraphs. "
        f"Include specific stock names and numbers where available."
    )

    if not getattr(settings, "groq_available", False) or not getattr(settings, "GROQ_API_KEY", ""):
        crit_titles = [f.title for f in critical[:2]]
        warn_titles = [f.title for f in warnings[:2]]
        narrative = (
            f"Portfolio diagnosis complete. "
            f"{'Critical issues found: ' + '; '.join(crit_titles) + '. ' if crit_titles else 'No critical issues. '}"
            f"{'Warnings: ' + '; '.join(warn_titles) + '.' if warn_titles else 'Minor optimisations suggested.'}"
            f" Add GROQ_API_KEY to .env for detailed AI narrative."
        )
        return narrative, False

    from utils.llm import call_llm_chat as call_groq_chat
    text = await call_groq_chat(
        [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        max_tokens=700, temperature=0.5, timeout=30.0,
    )
    if text:
        return text, True

    # Groq unreachable or returned empty — synthesize a short factual summary
    # from the most critical findings so the UI still has something to show.
    crit_titles = [f.title for f in critical[:2]]
    narrative = (
        f"Portfolio diagnosis complete. "
        f"{'Critical issues: ' + '; '.join(crit_titles) + '. ' if crit_titles else 'No critical issues found. '}"
        f"Review the findings above for detailed recommendations."
    )
    return narrative, False


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _current_fy() -> str:
    today = date.today()
    if today.month >= 4:
        return f"FY{today.year}-{str(today.year + 1)[2:]}"
    return f"FY{today.year - 1}-{str(today.year)[2:]}"


# ── MAIN ORCHESTRATOR ─────────────────────────────────────────────────────────

async def run_full_diagnosis(
    portfolio_id: str,
    sip_goal_ids: list[str],
    risk_profile: str,
    annual_income: float,
    session: AsyncSession,
) -> DiagnosisReport:
    from engine.portfolio_service import calculate_portfolio_summary
    from engine.allocation_engine import (
        build_allocation_map,
        calculate_portfolio_risk_score,
        get_recommended_allocation,
    )
    from engine.tax_engine import (
        build_tax_trades_from_transactions,
        calculate_tax_summary,
        find_harvesting_opportunities,
    )
    from engine.fundamental_analyzer import fetch_fundamentals_yfinance
    from crawler.live_prices import PRICE_CACHE
    from crawler.sector_data import get_sector_cache

    # Parallel data collection
    results = await asyncio.gather(
        calculate_portfolio_summary(portfolio_id, session),
        build_allocation_map(portfolio_id, sip_goal_ids, session),
        asyncio.to_thread(get_sector_cache),
    )
    portfolio_summary = results[0]
    allocation_data, total_value = results[1]
    sector_cache = results[2]

    empty = DiagnosisReport(
        portfolio_id=portfolio_id,
        overall_score=0,
        overall_grade="N/A",
        summary="Portfolio is empty. Add holdings to get a diagnosis.",
        findings=[],
        ai_narrative="",
        quick_wins=[],
        data_snapshot={},
        generated_at=datetime.utcnow().isoformat(),
        is_ai_generated=False,
    )

    if not portfolio_summary or not portfolio_summary.get("holdings"):
        return empty

    holdings = portfolio_summary.get("holdings", [])

    # Fetch fundamentals for each holding (cap at 15)
    fundamentals: dict = {}
    loop = asyncio.get_event_loop()
    for h in holdings[:15]:
        sym = h.get("symbol", "")
        if not sym:
            continue
        try:
            data = await loop.run_in_executor(None, fetch_fundamentals_yfinance, sym)
            fundamentals[sym] = data
        except Exception:
            fundamentals[sym] = {}
        await asyncio.sleep(0.1)

    # Tax data
    fy = _current_fy()
    tax_trades = await build_tax_trades_from_transactions(portfolio_id, fy, session)
    current_prices = {
        h.get("symbol", ""): PRICE_CACHE.get(h.get("symbol", ""), {}).get("price") or h.get("current_price") or 0
        for h in holdings
    }
    tax_summary_obj = calculate_tax_summary(tax_trades, fy, annual_income) if tax_trades else None

    existing_stcg = tax_summary_obj.stcg_equity_net  if tax_summary_obj else 0
    existing_ltcg = tax_summary_obj.ltcg_equity_net  if tax_summary_obj else 0
    ltcg_remaining = max(0, 125_000 - (tax_summary_obj.ltcg_exempt_used if tax_summary_obj else 0))

    harvesting = find_harvesting_opportunities(
        open_holdings=holdings,
        current_prices=current_prices,
        existing_stcg=existing_stcg,
        existing_ltcg=existing_ltcg,
        ltcg_exemption_remaining=ltcg_remaining,
    )

    # Risk score and rebalancing
    risk_score = calculate_portfolio_risk_score(allocation_data)
    portfolio_summary["risk_score"] = risk_score

    # Run all diagnostic modules
    all_findings: list[Finding] = []
    all_findings.extend(check_concentration(portfolio_summary, allocation_data))
    all_findings.extend(check_risk_quality(holdings, fundamentals, risk_score))
    all_findings.extend(check_diversification(holdings, allocation_data, total_value))
    all_findings.extend(check_tax_efficiency(tax_summary_obj, harvesting, holdings))
    all_findings.extend(check_performance(portfolio_summary, holdings))
    all_findings.extend(check_sector_timing(portfolio_summary, sector_cache))
    all_findings.extend(check_position_sizing(holdings, total_value))

    all_findings.sort(key=lambda f: (f.priority, f.severity != Severity.GOOD))

    score, grade = calculate_health_score(all_findings)

    # Quick wins
    urgent = [f for f in all_findings if f.severity in (Severity.CRITICAL, Severity.WARNING) and f.actions]
    quick_wins = [f.actions[0] for f in urgent[:3] if f.actions]

    # AI narrative
    ai_narrative, is_ai = await generate_ai_narrative(all_findings, portfolio_summary)

    # Push findings to the Intelligence Hub cache so the master cycle can
    # block/penalise flagged symbols without re-running a diagnosis.
    try:
        from engine.intelligence_hub import update_portfolio_doctor_cache
        conc_flags, overweight, losers, harvest = [], [], [], []
        for f in all_findings:
            mod, sev = f.module, f.severity
            if mod == "CONCENTRATION" and sev == Severity.CRITICAL:
                conc_flags.extend(f.stocks or [])
            if mod == "SECTOR_TIMING" and sev == Severity.WARNING:
                sec = (f.metric or {}).get("sector", "")
                if sec:
                    overweight.append(sec)
            if mod == "PERFORMANCE" and "loser" in (f.title or "").lower():
                losers.extend(f.stocks or [])
            if mod == "TAX_EFFICIENCY" and "harvest" in (f.title or "").lower():
                harvest.extend(f.stocks or [])
        update_portfolio_doctor_cache({
            "health_score":        score,
            "health_grade":        grade,
            "concentration_flags": conc_flags,
            "overweight_sectors":  overweight,
            "losers_to_exit":      losers,
            "tax_harvest_symbols": harvest,
            "updated_at":          datetime.utcnow().isoformat(),
        })
    except Exception:
        pass

    summary_obj = portfolio_summary.get("summary", {})
    return DiagnosisReport(
        portfolio_id=portfolio_id,
        overall_score=score,
        overall_grade=grade,
        summary=(
            f"Portfolio diagnosed: {score}/100 (Grade {grade}). "
            f"{sum(1 for f in all_findings if f.severity == Severity.CRITICAL)} critical, "
            f"{sum(1 for f in all_findings if f.severity == Severity.WARNING)} warnings."
        ),
        findings=all_findings,
        ai_narrative=ai_narrative,
        quick_wins=quick_wins,
        data_snapshot={
            "portfolio_value":  total_value,
            "holdings_count":   len(holdings),
            "xirr":             summary_obj.get("xirr") or 0,
            "risk_score":       risk_score,
            "tax_liability":    tax_summary_obj.total_tax if tax_summary_obj else 0,
            "generated_at":     datetime.utcnow().isoformat(),
        },
        generated_at=datetime.utcnow().isoformat(),
        is_ai_generated=is_ai,
    )


async def run_quick_diagnosis(
    portfolio_id: str,
    session: AsyncSession,
) -> DiagnosisReport:
    """Lightweight diagnosis — no AI, no fundamentals. Returns in < 3 seconds."""
    from engine.portfolio_service import calculate_portfolio_summary
    from engine.allocation_engine import build_allocation_map, calculate_portfolio_risk_score
    from engine.tax_engine import find_harvesting_opportunities
    from crawler.live_prices import PRICE_CACHE
    from crawler.sector_data import get_sector_cache

    results = await asyncio.gather(
        calculate_portfolio_summary(portfolio_id, session),
        build_allocation_map(portfolio_id, [], session),
        asyncio.to_thread(get_sector_cache),
    )
    portfolio_summary = results[0]
    allocation_data, total_value = results[1]
    sector_cache = results[2]

    if not portfolio_summary or not portfolio_summary.get("holdings"):
        return DiagnosisReport(
            portfolio_id=portfolio_id,
            overall_score=0,
            overall_grade="N/A",
            summary="Portfolio is empty.",
            findings=[],
            ai_narrative="",
            quick_wins=[],
            data_snapshot={},
            generated_at=datetime.utcnow().isoformat(),
            is_ai_generated=False,
        )

    holdings = portfolio_summary.get("holdings", [])
    risk_score = calculate_portfolio_risk_score(allocation_data)
    portfolio_summary["risk_score"] = risk_score

    current_prices = {
        h.get("symbol", ""): PRICE_CACHE.get(h.get("symbol", ""), {}).get("price") or h.get("current_price") or 0
        for h in holdings
    }
    harvesting = find_harvesting_opportunities(
        open_holdings=holdings,
        current_prices=current_prices,
        existing_stcg=0,
        existing_ltcg=0,
        ltcg_exemption_remaining=125_000,
    )

    all_findings: list[Finding] = []
    all_findings.extend(check_concentration(portfolio_summary, allocation_data))
    all_findings.extend(check_diversification(holdings, allocation_data, total_value))
    all_findings.extend(check_tax_efficiency(None, harvesting, holdings))
    all_findings.extend(check_position_sizing(holdings, total_value))
    all_findings.extend(check_sector_timing(portfolio_summary, sector_cache))

    all_findings.sort(key=lambda f: (f.priority, f.severity != Severity.GOOD))
    score, grade = calculate_health_score(all_findings)
    urgent     = [f for f in all_findings if f.severity in (Severity.CRITICAL, Severity.WARNING) and f.actions]
    quick_wins = [f.actions[0] for f in urgent[:3] if f.actions]

    return DiagnosisReport(
        portfolio_id=portfolio_id,
        overall_score=score,
        overall_grade=grade,
        summary=f"Quick check: {score}/100 (Grade {grade})",
        findings=all_findings,
        ai_narrative="",
        quick_wins=quick_wins,
        data_snapshot={"portfolio_value": total_value, "holdings_count": len(holdings)},
        generated_at=datetime.utcnow().isoformat(),
        is_ai_generated=False,
    )
