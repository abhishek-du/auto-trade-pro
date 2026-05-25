"""Indian Capital Gains Tax Engine — STCG/LTCG/Debt classification, loss set-off,
LTCG harvesting, tax-loss harvesting.

Tax rules effective from July 23, 2024 (Budget 2024):
  Equity STCG (Section 111A): 20% flat
  Equity LTCG (Section 112A): 12.5% flat, ₹1.25L annual exemption
  Debt MF (post Apr-2023, Section 50AA): all gains at slab rate
  + 4% Health & Education Cess on all tax amounts
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import TrackerHolding, TrackerTransaction
from utils.logger import logger

# ── Constants ─────────────────────────────────────────────────────────────────

PIVOT_DATE     = date(2024, 7, 23)   # Budget 2024 — new rates kick in
DEBT_RULE_DATE = date(2023, 4, 1)    # Debt MF rule change
LTCG_EXEMPTION = 125_000.0           # ₹1.25L per FY


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class TaxableTrade:
    symbol:           str
    company_name:     str
    asset_type:       str    # EQUITY / EQUITY_MF / DEBT_MF_PRE2023 / DEBT_MF_POST2023
    buy_date:         date
    sell_date:        date
    buy_price:        float
    sell_price:       float
    quantity:         float
    buy_amount:       float
    sell_amount:      float
    gross_gain:       float
    holding_days:     int
    holding_months:   float
    is_long_term:     bool
    gain_type:        str    # STCG / LTCG / DEBT_SLAB
    tax_rate:         float
    is_slab_taxed:    bool
    sale_before_pivot: bool
    financial_year:   str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["buy_date"]  = self.buy_date.isoformat()
        d["sell_date"] = self.sell_date.isoformat()
        return d


@dataclass
class TaxSummary:
    financial_year:        str
    total_income_assumed:  float
    slab_rate:             float

    stcg_equity_gains:     float
    stcg_equity_losses:    float
    stcg_equity_net:       float
    stcg_tax_before_cess:  float
    stcg_surcharge:        float
    stcg_cess:             float
    stcg_total_tax:        float

    ltcg_equity_gains:     float
    ltcg_equity_losses:    float
    ltcg_equity_net:       float   # after STCL + LTCL set-off, before exemption
    ltcg_exempt_used:      float
    ltcg_exempt_remaining: float
    ltcg_taxable:          float
    ltcg_tax_before_cess:  float
    ltcg_surcharge:        float
    ltcg_cess:             float
    ltcg_total_tax:        float

    debt_slab_gains:       float
    debt_slab_losses:      float
    debt_slab_net:         float
    debt_slab_tax:         float

    total_tax:             float
    effective_tax_rate:    float

    stcl_carried_forward:  float
    ltcl_carried_forward:  float
    total_loss_carried:    float

    trades:                list[TaxableTrade] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if k != "trades"}
        d["trades"] = [t.to_dict() for t in self.trades]
        return d


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_fy(sell_date: date) -> str:
    """Returns 'FY2025-26' for a sell date."""
    if sell_date.month >= 4:
        y = sell_date.year
    else:
        y = sell_date.year - 1
    return f"FY{y}-{str(y + 1)[2:]}"


def _parse_fy(financial_year: str) -> tuple[date, date]:
    """'FY2025-26' → (date(2025,4,1), date(2026,3,31))"""
    yr = financial_year.replace("FY", "")
    start_year = int(yr.split("-")[0])
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


def get_slab_rate(income: float) -> float:
    """New tax regime FY2025-26 (Budget 2025)."""
    if income <= 400_000:   return 0.0
    if income <= 800_000:   return 0.05
    if income <= 1_200_000: return 0.10
    if income <= 1_600_000: return 0.15
    if income <= 2_000_000: return 0.20
    if income <= 2_400_000: return 0.25
    return 0.30


def _surcharge_rate(total_income: float) -> float:
    """Surcharge rate — capped at 15% for Section 111A / 112A gains."""
    if total_income > 10_000_000:  return 0.15   # > ₹1Cr (capped for equity)
    if total_income > 5_000_000:   return 0.10   # 50L – 1Cr
    return 0.0


# ── Section A — Trade Classification ─────────────────────────────────────────

def classify_trade(
    symbol:       str,
    company_name: str,
    asset_type:   str,
    buy_date:     date,
    sell_date:    date,
    buy_price:    float,
    sell_price:   float,
    quantity:     float,
) -> TaxableTrade:
    holding_days   = (sell_date - buy_date).days
    holding_months = round(holding_days / 30.44, 1)
    sale_before_pivot = sell_date < PIVOT_DATE

    is_long_term  = False
    gain_type     = "STCG"
    tax_rate      = 0.0
    is_slab_taxed = False

    if asset_type in ("EQUITY", "EQUITY_MF"):
        if holding_days > 365:
            is_long_term = True
            gain_type    = "LTCG"
            tax_rate     = 0.10 if sale_before_pivot else 0.125
        else:
            gain_type = "STCG"
            tax_rate  = 0.15 if sale_before_pivot else 0.20

    elif asset_type == "DEBT_MF_POST2023":
        gain_type     = "DEBT_SLAB"
        is_slab_taxed = True
        tax_rate      = 0.0

    elif asset_type == "DEBT_MF_PRE2023":
        if holding_days > 730:   # 24 months
            is_long_term = True
            gain_type    = "LTCG"
            tax_rate     = 0.125
        else:
            gain_type     = "STCG"
            is_slab_taxed = True
            tax_rate      = 0.0

    gross_gain = round((sell_price - buy_price) * quantity, 2)

    return TaxableTrade(
        symbol=symbol,
        company_name=company_name,
        asset_type=asset_type,
        buy_date=buy_date,
        sell_date=sell_date,
        buy_price=round(buy_price, 2),
        sell_price=round(sell_price, 2),
        quantity=quantity,
        buy_amount=round(buy_price * quantity, 2),
        sell_amount=round(sell_price * quantity, 2),
        gross_gain=gross_gain,
        holding_days=holding_days,
        holding_months=holding_months,
        is_long_term=is_long_term,
        gain_type=gain_type,
        tax_rate=tax_rate,
        is_slab_taxed=is_slab_taxed,
        sale_before_pivot=sale_before_pivot,
        financial_year=_get_fy(sell_date),
    )


# ── Section B — Tax Summary Calculation ──────────────────────────────────────

def calculate_tax_summary(
    trades: list[TaxableTrade],
    financial_year: str,
    annual_income: float = 1_000_000,
    already_used_ltcg_exemption: float = 0.0,
) -> TaxSummary:
    fy_trades = [t for t in trades if t.financial_year == financial_year]

    # Split gains and losses by type
    stcg_gains    = sum(t.gross_gain for t in fy_trades if t.gain_type == "STCG" and t.gross_gain > 0 and not t.is_slab_taxed)
    stcg_losses   = abs(sum(t.gross_gain for t in fy_trades if t.gain_type == "STCG" and t.gross_gain < 0 and not t.is_slab_taxed))
    ltcg_gains    = sum(t.gross_gain for t in fy_trades if t.gain_type == "LTCG" and t.gross_gain > 0)
    ltcg_losses   = abs(sum(t.gross_gain for t in fy_trades if t.gain_type == "LTCG" and t.gross_gain < 0))
    debt_gains    = sum(t.gross_gain for t in fy_trades if t.gain_type == "DEBT_SLAB" and t.gross_gain > 0)
    debt_losses   = abs(sum(t.gross_gain for t in fy_trades if t.gain_type == "DEBT_SLAB" and t.gross_gain < 0))
    # Also capture slab-taxed STCG (debt pre-2023 held < 24m)
    slab_stcg     = sum(t.gross_gain for t in fy_trades if t.gain_type == "STCG" and t.is_slab_taxed and t.gross_gain > 0)
    debt_gains   += slab_stcg
    slab_stcg_loss = abs(sum(t.gross_gain for t in fy_trades if t.gain_type == "STCG" and t.is_slab_taxed and t.gross_gain < 0))
    debt_losses  += slab_stcg_loss

    # ── Loss set-off ──────────────────────────────────────────────────────────
    # Step 1: STCL offsets STCG
    stcg_net      = stcg_gains - stcg_losses
    remaining_stcl = max(0.0, -stcg_net)   # excess STCL after STCG absorption

    # Step 2: Remaining STCL offsets LTCG
    ltcg_after_stcl  = max(0.0, ltcg_gains - remaining_stcl)
    remaining_stcl   = max(0.0, remaining_stcl - ltcg_gains)

    # Step 3: LTCL offsets only LTCG
    ltcg_net         = max(0.0, ltcg_after_stcl - ltcg_losses)
    ltcl_unused      = max(0.0, ltcg_losses - ltcg_after_stcl)

    # Step 4: LTCG exemption
    available_exemption  = max(0.0, LTCG_EXEMPTION - already_used_ltcg_exemption)
    ltcg_exempt_used     = round(min(ltcg_net, available_exemption), 2)
    ltcg_exempt_remaining = round(max(0.0, available_exemption - ltcg_exempt_used), 2)
    ltcg_taxable         = round(max(0.0, ltcg_net - ltcg_exempt_used), 2)

    # Step 5: Debt slab
    debt_net = round(max(0.0, debt_gains - debt_losses), 2)

    # ── Surcharge ─────────────────────────────────────────────────────────────
    total_income = annual_income + max(0.0, stcg_net) + ltcg_taxable
    surcharge    = _surcharge_rate(total_income)

    # ── Tax computation ───────────────────────────────────────────────────────
    stcg_base         = round(max(0.0, stcg_net) * 0.20, 2)
    stcg_surcharge_amt = round(stcg_base * surcharge, 2)
    stcg_cess_amt     = round((stcg_base + stcg_surcharge_amt) * 0.04, 2)
    stcg_total        = round(stcg_base + stcg_surcharge_amt + stcg_cess_amt, 2)

    ltcg_base         = round(ltcg_taxable * 0.125, 2)
    ltcg_surcharge_amt = round(ltcg_base * surcharge, 2)
    ltcg_cess_amt     = round((ltcg_base + ltcg_surcharge_amt) * 0.04, 2)
    ltcg_total        = round(ltcg_base + ltcg_surcharge_amt + ltcg_cess_amt, 2)

    slab_rate  = get_slab_rate(annual_income)
    debt_tax   = round(debt_net * slab_rate * 1.04, 2)

    total_tax  = round(stcg_total + ltcg_total + debt_tax, 2)

    total_gains = stcg_gains + ltcg_gains + debt_gains
    eff_rate    = round(total_tax / total_gains * 100, 2) if total_gains > 0 else 0.0

    # Carry-forward amounts
    stcl_cf = round(remaining_stcl, 2)       # STCL remaining after LTCG offset
    ltcl_cf = round(ltcl_unused, 2)

    return TaxSummary(
        financial_year=financial_year,
        total_income_assumed=annual_income,
        slab_rate=slab_rate,

        stcg_equity_gains=round(stcg_gains, 2),
        stcg_equity_losses=round(stcg_losses, 2),
        stcg_equity_net=round(stcg_net, 2),
        stcg_tax_before_cess=stcg_base,
        stcg_surcharge=stcg_surcharge_amt,
        stcg_cess=stcg_cess_amt,
        stcg_total_tax=stcg_total,

        ltcg_equity_gains=round(ltcg_gains, 2),
        ltcg_equity_losses=round(ltcg_losses, 2),
        ltcg_equity_net=round(ltcg_net, 2),
        ltcg_exempt_used=ltcg_exempt_used,
        ltcg_exempt_remaining=ltcg_exempt_remaining,
        ltcg_taxable=ltcg_taxable,
        ltcg_tax_before_cess=ltcg_base,
        ltcg_surcharge=ltcg_surcharge_amt,
        ltcg_cess=ltcg_cess_amt,
        ltcg_total_tax=ltcg_total,

        debt_slab_gains=round(debt_gains, 2),
        debt_slab_losses=round(debt_losses, 2),
        debt_slab_net=debt_net,
        debt_slab_tax=debt_tax,

        total_tax=total_tax,
        effective_tax_rate=eff_rate,

        stcl_carried_forward=stcl_cf,
        ltcl_carried_forward=ltcl_cf,
        total_loss_carried=round(stcl_cf + ltcl_cf, 2),

        trades=fy_trades,
    )


# ── Section C — Loss / Gain Harvesting ───────────────────────────────────────

def find_harvesting_opportunities(
    open_holdings: list[dict],
    current_prices: dict[str, float],
    existing_stcg: float,
    existing_ltcg: float,
    ltcg_exemption_remaining: float,
) -> dict:
    opportunities: dict = {
        "loss_harvest":       [],
        "gain_harvest":       [],
        "timing_suggestions": [],
        "summary":            {},
    }

    for holding in open_holdings:
        symbol        = holding.get("symbol", "")
        current_price = current_prices.get(symbol, 0.0)
        if not current_price:
            continue

        buy_price     = float(holding.get("avg_buy_price", 0) or 0)
        quantity      = float(holding.get("quantity", 0) or 0)
        buy_date_str  = holding.get("first_buy_date")
        company_name  = holding.get("company_name", symbol)

        if not buy_date_str or not buy_price or not quantity:
            continue

        try:
            buy_date = date.fromisoformat(str(buy_date_str))
        except Exception:
            continue

        holding_days     = (date.today() - buy_date).days
        unrealized_pnl   = round((current_price - buy_price) * quantity, 2)
        unrealized_pct   = round((current_price - buy_price) / buy_price * 100, 2) if buy_price else 0

        # ── LOSS HARVESTING ───────────────────────────────────────────────────
        if unrealized_pnl < 0:
            is_stcl    = holding_days <= 365
            loss_type  = "STCL" if is_stcl else "LTCL"
            tax_saved  = 0.0

            if is_stcl and existing_stcg > 0:
                offset_amount = min(abs(unrealized_pnl), existing_stcg)
                tax_saved     = round(offset_amount * 0.20 * 1.04, 2)
            elif not is_stcl:
                taxable_ltcg = max(0.0, existing_ltcg - ltcg_exemption_remaining)
                if taxable_ltcg > 0:
                    offset_amount = min(abs(unrealized_pnl), taxable_ltcg)
                    tax_saved     = round(offset_amount * 0.125 * 1.04, 2)

            if tax_saved > 500:
                opportunities["loss_harvest"].append({
                    "symbol":              symbol.replace(".NS", ""),
                    "company_name":        company_name,
                    "unrealized_loss":     unrealized_pnl,
                    "unrealized_loss_pct": unrealized_pct,
                    "loss_type":           loss_type,
                    "holding_days":        holding_days,
                    "quantity":            quantity,
                    "estimated_tax_saved": tax_saved,
                    "action":              f"Sell {quantity:.0f} shares to book ₹{abs(unrealized_pnl):,.0f} {loss_type}",
                    "note":                "Rebuy after 1+ trading day. No wash-sale rule in India.",
                })

        # ── GAIN HARVESTING (LTCG within exempt limit) ───────────────────────
        elif unrealized_pnl > 0 and holding_days > 365:
            if ltcg_exemption_remaining > 1_000:
                per_unit_gain = current_price - buy_price
                if per_unit_gain > 0:
                    bookable_gain  = min(unrealized_pnl, ltcg_exemption_remaining)
                    bookable_units = round(bookable_gain / per_unit_gain, 2)
                    tax_saved_gh   = round(bookable_gain * 0.125 * 1.04, 2)

                    opportunities["gain_harvest"].append({
                        "symbol":              symbol.replace(".NS", ""),
                        "company_name":        company_name,
                        "unrealized_gain":     unrealized_pnl,
                        "exemption_remaining": round(ltcg_exemption_remaining, 2),
                        "bookable_gain":       round(bookable_gain, 2),
                        "bookable_units":      bookable_units,
                        "current_price":       current_price,
                        "tax_saved":           tax_saved_gh,
                        "action":              f"Sell {bookable_units} units to book ₹{bookable_gain:,.0f} LTCG tax-free",
                        "note":                f"Rebuy immediately at ₹{current_price:,.2f}. Resets cost basis. Repeat every March.",
                    })

        # ── TIMING SUGGESTIONS (close to 1-year threshold) ──────────────────
        if unrealized_pnl > 0 and 330 <= holding_days <= 365:
            days_to_ltcg    = 365 - holding_days
            stcg_tax_now    = round(unrealized_pnl * 0.20 * 1.04, 2)
            ltcg_tax_later  = round(max(0, unrealized_pnl - ltcg_exemption_remaining) * 0.125 * 1.04, 2)
            potential_saving = round(stcg_tax_now - ltcg_tax_later, 2)

            if potential_saving > 1_000:
                opportunities["timing_suggestions"].append({
                    "symbol":              symbol.replace(".NS", ""),
                    "company_name":        company_name,
                    "days_to_ltcg":        days_to_ltcg,
                    "holding_days":        holding_days,
                    "current_gain":        unrealized_pnl,
                    "stcg_tax_if_sold_now": stcg_tax_now,
                    "ltcg_tax_after_waiting": ltcg_tax_later,
                    "potential_saving":    potential_saving,
                    "action":              f"Hold {symbol.replace('.NS','')} {days_to_ltcg} more days to save ₹{potential_saving:,.0f}",
                })

    # Sort by saving
    for key in ("loss_harvest", "gain_harvest", "timing_suggestions"):
        opportunities[key].sort(
            key=lambda x: x.get("estimated_tax_saved", x.get("tax_saved", x.get("potential_saving", 0))),
            reverse=True,
        )

    total_saveable = (
        sum(o["estimated_tax_saved"] for o in opportunities["loss_harvest"]) +
        sum(o["tax_saved"]           for o in opportunities["gain_harvest"])
    )

    opportunities["summary"] = {
        "loss_harvest_count":    len(opportunities["loss_harvest"]),
        "gain_harvest_count":    len(opportunities["gain_harvest"]),
        "timing_count":          len(opportunities["timing_suggestions"]),
        "total_tax_saveable":    round(total_saveable, 2),
        "ltcg_exemption_remaining": round(ltcg_exemption_remaining, 2),
    }

    return opportunities


# ── Section D — DB → TaxableTrade converter ──────────────────────────────────

async def build_tax_trades_from_transactions(
    portfolio_id: str,
    financial_year: str,
    session: AsyncSession,
) -> list[TaxableTrade]:
    """FIFO-match BUY/SELL transactions → TaxableTrade list for the given FY."""
    fy_start, fy_end = _parse_fy(financial_year)

    # All BUY transactions (for FIFO queue, we need history before FY too)
    all_buys_res = await session.execute(
        select(TrackerTransaction)
        .where(
            TrackerTransaction.portfolio_id == portfolio_id,
            TrackerTransaction.tx_type == "BUY",
        )
        .order_by(TrackerTransaction.symbol, TrackerTransaction.trade_date)
    )
    all_buys = list(all_buys_res.scalars().all())

    # SELL transactions in this FY only
    sells_res = await session.execute(
        select(TrackerTransaction)
        .where(
            TrackerTransaction.portfolio_id == portfolio_id,
            TrackerTransaction.tx_type == "SELL",
            TrackerTransaction.trade_date >= fy_start,
            TrackerTransaction.trade_date <= fy_end,
        )
        .order_by(TrackerTransaction.trade_date)
    )
    sells = list(sells_res.scalars().all())

    if not sells:
        return []

    # Build per-symbol FIFO buy queues: symbol → [[buy_date, buy_price, remaining_qty]]
    queues: dict[str, list[list]] = {}
    for b in sorted(all_buys, key=lambda x: (x.symbol, x.trade_date)):
        queues.setdefault(b.symbol, []).append([b.trade_date, float(b.price), float(b.quantity)])

    results: list[TaxableTrade] = []

    for sell_tx in sells:
        sym         = sell_tx.symbol
        sell_qty    = float(sell_tx.quantity)
        sell_price  = float(sell_tx.price)
        sell_date   = sell_tx.trade_date
        company     = sell_tx.company_name or sym.replace(".NS", "")
        asset_type  = "EQUITY"   # all portfolio holdings are listed equity

        q = queues.get(sym, [])
        remaining = sell_qty

        while remaining > 0 and q:
            buy_date, buy_price, buy_qty = q[0]
            matched = min(remaining, buy_qty)

            trade = classify_trade(
                symbol=sym,
                company_name=company,
                asset_type=asset_type,
                buy_date=buy_date,
                sell_date=sell_date,
                buy_price=buy_price,
                sell_price=sell_price,
                quantity=matched,
            )
            results.append(trade)

            q[0][2] -= matched
            if q[0][2] <= 1e-9:
                q.pop(0)
            remaining -= matched

        if remaining > 0:
            # No buy records found — treat as zero-cost (edge case)
            logger.warning(f"[tax_engine] No buy records for {sym} sell on {sell_date}; {remaining} units unmatched")

    return results
