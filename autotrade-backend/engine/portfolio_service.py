"""Personal Portfolio Tracker — service layer.

Handles price lookup, XIRR, tax calculation, and all DB mutations
for the /api/v1/portfolios endpoints.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import yfinance as yf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import TrackerHolding, TrackerPortfolio, TrackerTransaction
from utils.logger import logger
from utils.nav_cache import get_latest_nav as _get_mf_nav  # noqa: F401  (re-export
# kept for any in-tree call site that imports it from this module historically)

# ── Stock lookup dictionary (display_name → yfinance symbol) ─────────────────

NSE_STOCK_LOOKUP: dict[str, str] = {
    "Reliance Industries":   "RELIANCE.NS",
    "TCS":                   "TCS.NS",
    "HDFC Bank":             "HDFCBANK.NS",
    "Infosys":               "INFY.NS",
    "ICICI Bank":            "ICICIBANK.NS",
    "Hindustan Unilever":    "HINDUNILVR.NS",
    "State Bank of India":   "SBIN.NS",
    "Bajaj Finance":         "BAJFINANCE.NS",
    "Bharti Airtel":         "BHARTIARTL.NS",
    "Kotak Mahindra Bank":   "KOTAKBANK.NS",
    "ITC":                   "ITC.NS",
    "Asian Paints":          "ASIANPAINT.NS",
    "Larsen & Toubro":       "LT.NS",
    "Maruti Suzuki":         "MARUTI.NS",
    "Axis Bank":             "AXISBANK.NS",
    "Wipro":                 "WIPRO.NS",
    "HCL Technologies":      "HCLTECH.NS",
    "UltraTech Cement":      "ULTRACEMCO.NS",
    "Nestle India":          "NESTLEIND.NS",
    "Titan Company":         "TITAN.NS",
    "Tech Mahindra":         "TECHM.NS",
    "Power Grid Corp":       "POWERGRID.NS",
    "NTPC":                  "NTPC.NS",
    "Bajaj Finserv":         "BAJAJFINSV.NS",
    "Sun Pharma":            "SUNPHARMA.NS",
    "Dr. Reddy's Labs":      "DRREDDY.NS",
    "Cipla":                 "CIPLA.NS",
    "Adani Ports":           "ADANIPORTS.NS",
    "JSW Steel":             "JSWSTEEL.NS",
    "Tata Steel":            "TATASTEEL.NS",
    "Hindalco Industries":   "HINDALCO.NS",
    "Grasim Industries":     "GRASIM.NS",
    "Coal India":            "COALINDIA.NS",
    "ONGC":                  "ONGC.NS",
    "Indian Oil Corp":       "IOC.NS",
    "BPCL":                  "BPCL.NS",
    "Hero MotoCorp":         "HEROMOTOCO.NS",
    "Bajaj Auto":            "BAJAJ-AUTO.NS",
    "Eicher Motors":         "EICHERMOT.NS",
    "Divi's Labs":           "DIVISLAB.NS",
    "Pidilite Industries":   "PIDILITIND.NS",
    "Havells India":         "HAVELLS.NS",
    "SBI Life Insurance":    "SBILIFE.NS",
    "HDFC Life Insurance":   "HDFCLIFE.NS",
    "Adani Enterprises":     "ADANIENT.NS",
    "Tata Motors":           "TATAMOTORS.NS",
    "IndusInd Bank":         "INDUSINDBK.NS",
    "Mahindra & Mahindra":   "M&M.NS",
    "Shriram Finance":       "SHRIRAMFIN.NS",
    "Zomato":                "ZOMATO.NS",
    "Paytm":                 "PAYTM.NS",
    "Nykaa":                 "NYKAA.NS",
    "Avenue Supermarts":     "DMART.NS",
    "Trent":                 "TRENT.NS",
    "Varun Beverages":       "VBL.NS",
    "Godrej Consumer":       "GODREJCP.NS",
    "Dabur India":           "DABUR.NS",
    "Marico":                "MARICO.NS",
    "Berger Paints":         "BERGEPAINT.NS",
}

NSE_SECTOR_MAP: dict[str, str] = {
    "RELIANCE.NS":   "Energy",
    "TCS.NS":        "IT",
    "HDFCBANK.NS":   "Banking",
    "INFY.NS":       "IT",
    "ICICIBANK.NS":  "Banking",
    "HINDUNILVR.NS": "FMCG",
    "SBIN.NS":       "Banking",
    "BAJFINANCE.NS": "Finance",
    "BHARTIARTL.NS": "Telecom",
    "KOTAKBANK.NS":  "Banking",
    "ITC.NS":        "FMCG",
    "ASIANPAINT.NS": "Paints",
    "LT.NS":         "Infrastructure",
    "MARUTI.NS":     "Auto",
    "AXISBANK.NS":   "Banking",
    "WIPRO.NS":      "IT",
    "HCLTECH.NS":    "IT",
    "ULTRACEMCO.NS": "Cement",
    "NESTLEIND.NS":  "FMCG",
    "TITAN.NS":      "Consumer",
    "TECHM.NS":      "IT",
    "POWERGRID.NS":  "Power",
    "NTPC.NS":       "Power",
    "BAJAJFINSV.NS": "Finance",
    "SUNPHARMA.NS":  "Pharma",
    "DRREDDY.NS":    "Pharma",
    "CIPLA.NS":      "Pharma",
    "ADANIPORTS.NS": "Infrastructure",
    "JSWSTEEL.NS":   "Metals",
    "TATASTEEL.NS":  "Metals",
    "HINDALCO.NS":   "Metals",
    "GRASIM.NS":     "Diversified",
    "COALINDIA.NS":  "Mining",
    "ONGC.NS":       "Energy",
    "IOC.NS":        "Energy",
    "BPCL.NS":       "Energy",
    "HEROMOTOCO.NS": "Auto",
    "BAJAJ-AUTO.NS": "Auto",
    "EICHERMOT.NS":  "Auto",
    "DIVISLAB.NS":   "Pharma",
    "PIDILITIND.NS": "Chemicals",
    "HAVELLS.NS":    "Consumer",
    "SBILIFE.NS":    "Insurance",
    "HDFCLIFE.NS":   "Insurance",
    "ADANIENT.NS":   "Diversified",
    "TATAMOTORS.NS": "Auto",
    "INDUSINDBK.NS": "Banking",
    "M&M.NS":        "Auto",
    "SHRIRAMFIN.NS": "Finance",
    "ZOMATO.NS":     "Consumer",
    "PAYTM.NS":      "Finance",
    "NYKAA.NS":      "Consumer",
    "DMART.NS":      "Retail",
    "TRENT.NS":      "Retail",
    "VBL.NS":        "FMCG",
    "GODREJCP.NS":   "FMCG",
    "DABUR.NS":      "FMCG",
    "MARICO.NS":     "FMCG",
    "BERGEPAINT.NS": "Paints",
}


# ── Price helpers ─────────────────────────────────────────────────────────────

async def get_current_price(symbol: str) -> float | None:
    """PRICE_CACHE first; MF NAV for MF: symbols; yfinance fallback for stocks.

    Async because the MF NAV path goes through utils.nav_cache (httpx.AsyncClient).
    The yfinance fallback is a blocking call wrapped in ``asyncio.to_thread``.
    """
    if symbol.startswith("MF:"):
        return await _get_mf_nav(symbol[3:])
    from crawler.live_prices import PRICE_CACHE
    cached = PRICE_CACHE.get(symbol)
    if cached and cached.get("price"):
        return float(cached["price"])
    try:
        import asyncio as _asyncio
        info = await _asyncio.to_thread(lambda: yf.Ticker(symbol).fast_info)
        price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
        return float(price) if price else None
    except Exception:
        return None


async def get_prices_batch(symbols: list[str]) -> dict[str, float]:
    """Batch price fetch — cache first, MF NAV for MF: symbols, yfinance for misses."""
    from crawler.live_prices import PRICE_CACHE
    result: dict[str, float] = {}
    missing: list[str] = []
    for sym in symbols:
        if sym.startswith("MF:"):
            nav = await _get_mf_nav(sym[3:])
            if nav:
                result[sym] = nav
            continue
        cached = PRICE_CACHE.get(sym)
        if cached and cached.get("price"):
            result[sym] = float(cached["price"])
        else:
            missing.append(sym)
    for sym in missing:
        price = await get_current_price(sym)
        if price:
            result[sym] = price
    return result


# ── XIRR ─────────────────────────────────────────────────────────────────────

def calculate_xirr(
    cashflows: list[tuple[date, float]],
    guess: float = 0.1,
) -> float | None:
    """Newton-Raphson XIRR. Returns annualized % (e.g. 15.0 = 15%)."""
    if len(cashflows) < 2:
        return None
    dates   = [cf[0] for cf in cashflows]
    amounts = [cf[1] for cf in cashflows]
    t0      = dates[0]
    years   = [(d - t0).days / 365.25 for d in dates]

    try:
        rate = float(guess)
        for _ in range(1000):
            base = 1.0 + rate
            if base <= 0:
                return None
            npv  = sum(a / base ** t for a, t in zip(amounts, years))
            dnpv = sum(-t * a / base ** (t + 1) for a, t in zip(amounts, years))
            if abs(dnpv) < 1e-12:
                break
            new_rate = rate - npv / dnpv
            if not isinstance(new_rate, (int, float)) or new_rate != new_rate:  # NaN check
                return None
            if abs(new_rate - rate) < 1e-8:
                rate = new_rate
                break
            rate = new_rate
    except (TypeError, ZeroDivisionError, OverflowError):
        return None

    if not isinstance(rate, (int, float)) or not (-0.99 < rate < 100):
        return None
    return round(rate * 100, 2)


def build_cashflows_for_holding(
    transactions: list[TrackerTransaction],
    current_value: float,
    today: date,
) -> list[tuple[date, float]]:
    """Build XIRR cashflow list. Buys=negative, sells/current value=positive."""
    flows: list[tuple[date, float]] = []
    for tx in transactions:
        net = tx.total_amount + tx.brokerage + tx.stt
        if tx.tx_type == "BUY":
            flows.append((tx.trade_date, -net))
        elif tx.tx_type == "SELL":
            flows.append((tx.trade_date, tx.total_amount - tx.brokerage - tx.stt))
    if current_value > 0:
        flows.append((today, current_value))
    flows.sort(key=lambda x: x[0])
    return flows


# ── Tax ───────────────────────────────────────────────────────────────────────

def calculate_tax_liability(
    transactions: list[TrackerTransaction],
    _today: date,
) -> dict:
    """STCG 20% (< 1 yr); LTCG 12.5% above ₹1.25 L (≥ 1 yr). FIFO matching."""
    stcg = 0.0
    ltcg = 0.0
    # per-symbol FIFO queues: symbol → [[buy_date, buy_price, qty]]
    queues: dict[str, list[list]] = {}

    for tx in sorted(transactions, key=lambda x: x.trade_date):
        sym = tx.symbol
        if tx.tx_type == "BUY":
            queues.setdefault(sym, []).append([tx.trade_date, tx.price, tx.quantity])
        elif tx.tx_type == "SELL":
            q = queues.get(sym, [])
            remaining = tx.quantity
            while remaining > 0 and q:
                buy_date, buy_price, buy_qty = q[0]
                matched = min(remaining, buy_qty)
                gain = (tx.price - buy_price) * matched
                if (tx.trade_date - buy_date).days < 365:
                    stcg += gain
                else:
                    ltcg += gain
                q[0][2] -= matched
                if q[0][2] <= 0:
                    q.pop(0)
                remaining -= matched

    stcg_tax = max(0.0, stcg * 0.20)
    ltcg_exempt = 125_000.0
    ltcg_taxable = max(0.0, ltcg - ltcg_exempt)
    ltcg_tax = ltcg_taxable * 0.125

    return {
        "stcg_gains":   round(stcg, 2),
        "ltcg_gains":   round(ltcg, 2),
        "stcg_tax":     round(stcg_tax, 2),
        "ltcg_tax":     round(ltcg_tax, 2),
        "total_tax":    round(stcg_tax + ltcg_tax, 2),
        "ltcg_exempt":  ltcg_exempt,
        "ltcg_taxable": round(ltcg_taxable, 2),
    }


# ── Holding metrics ───────────────────────────────────────────────────────────

def calculate_holding_metrics(
    holding: TrackerHolding,
    transactions: list[TrackerTransaction],
    current_price: float | None,
    today: date,
) -> dict:
    ltp      = current_price or holding.avg_buy_price
    invested = holding.avg_buy_price * holding.quantity
    cur_val  = ltp * holding.quantity
    pnl      = cur_val - invested
    pnl_pct  = pnl / invested * 100 if invested else 0.0

    cashflows = build_cashflows_for_holding(transactions, cur_val, today)
    xirr_pct  = calculate_xirr(cashflows) if len(cashflows) >= 2 else None

    from crawler.live_prices import PRICE_CACHE
    cached        = PRICE_CACHE.get(holding.symbol, {})
    day_change     = float(cached.get("change",     0.0) or 0.0)
    day_change_pct = float(cached.get("change_pct", 0.0) or 0.0)
    day_pnl        = day_change * holding.quantity

    return {
        "invested":       round(invested, 2),
        "current_value":  round(cur_val,  2),
        "pnl":            round(pnl,      2),
        "pnl_pct":        round(pnl_pct,  2),
        "xirr":           xirr_pct,
        "current_price":  current_price,
        "day_change":     round(day_change,     2),
        "day_change_pct": round(day_change_pct, 2),
        "day_pnl":        round(day_pnl,        2),
    }


# ── Portfolio summary ─────────────────────────────────────────────────────────

async def calculate_portfolio_summary(portfolio_id: str, session: AsyncSession) -> dict | None:
    res = await session.execute(
        select(TrackerPortfolio).where(TrackerPortfolio.id == portfolio_id)
    )
    portfolio = res.scalar_one_or_none()
    if not portfolio:
        return None

    res = await session.execute(
        select(TrackerHolding).where(TrackerHolding.portfolio_id == portfolio_id)
    )
    holdings = list(res.scalars().all())

    empty_summary = {
        "total_invested": 0, "current_value": 0, "total_pnl": 0,
        "total_pnl_pct": 0, "today_pnl": 0, "xirr": None, "holdings_count": 0,
    }
    if not holdings:
        return {
            "portfolio": _portfolio_to_dict(portfolio),
            "holdings": [],
            "summary": empty_summary,
            "allocation": {"by_stock": [], "by_sector": []},
            "tax": {"stcg_gains": 0, "ltcg_gains": 0, "stcg_tax": 0, "ltcg_tax": 0, "total_tax": 0, "ltcg_exempt": 125000, "ltcg_taxable": 0},
        }

    symbols = [h.symbol for h in holdings]
    prices  = await get_prices_batch(symbols)
    today   = date.today()

    holdings_out: list[dict] = []
    total_invested = 0.0
    total_cur      = 0.0
    total_day_pnl  = 0.0
    all_txns: list[TrackerTransaction] = []
    sector_vals: dict[str, float] = {}

    for h in holdings:
        res = await session.execute(
            select(TrackerTransaction)
            .where(TrackerTransaction.holding_id == h.id)
            .order_by(TrackerTransaction.trade_date)
        )
        txns = list(res.scalars().all())
        all_txns.extend(txns)

        price   = prices.get(h.symbol)
        metrics = calculate_holding_metrics(h, txns, price, today)

        holdings_out.append({**_holding_to_dict(h), **metrics, "weight": 0.0})
        total_invested += metrics["invested"]
        total_cur      += metrics["current_value"]
        total_day_pnl  += metrics["day_pnl"]
        sector = h.sector or "Other"
        sector_vals[sector] = sector_vals.get(sector, 0.0) + metrics["current_value"]

    # Weights
    for hd in holdings_out:
        hd["weight"] = round(hd["current_value"] / total_cur * 100, 2) if total_cur else 0.0

    total_pnl     = total_cur - total_invested
    total_pnl_pct = total_pnl / total_invested * 100 if total_invested else 0.0

    port_flows = build_cashflows_for_holding(all_txns, total_cur, today)
    port_xirr  = calculate_xirr(port_flows) if len(port_flows) >= 2 else None

    tax_data = calculate_tax_liability(all_txns, today)

    by_stock = [
        {"symbol": hd["symbol"], "name": hd["company_name"] or hd["symbol"],
         "value": hd["current_value"], "weight": hd["weight"]}
        for hd in holdings_out
    ]
    by_sector = sorted(
        [
            {"sector": s, "value": round(v, 2),
             "weight": round(v / total_cur * 100, 2) if total_cur else 0.0}
            for s, v in sector_vals.items()
        ],
        key=lambda x: -x["value"],
    )

    return {
        "portfolio": _portfolio_to_dict(portfolio),
        "holdings":  sorted(holdings_out, key=lambda x: -x["current_value"]),
        "summary": {
            "total_invested": round(total_invested, 2),
            "current_value":  round(total_cur,      2),
            "total_pnl":      round(total_pnl,      2),
            "total_pnl_pct":  round(total_pnl_pct,  2),
            "today_pnl":      round(total_day_pnl,  2),
            "xirr":           port_xirr,
            "holdings_count": len(holdings),
        },
        "allocation": {"by_stock": by_stock, "by_sector": by_sector},
        "tax": tax_data,
    }


# ── Mutations ─────────────────────────────────────────────────────────────────

async def add_or_update_holding(
    portfolio_id: str,
    symbol: str,
    quantity: float,
    price: float,
    trade_date: date,
    notes: str,
    session: AsyncSession,
    company_name: str = "",
    sector_override: str = "",
) -> dict:
    is_mf = symbol.startswith("MF:")
    if not company_name:
        company_name = _get_company_name(symbol) if not is_mf else symbol[3:]
    sector       = sector_override or (NSE_SECTOR_MAP.get(symbol, "Other") if not is_mf else "Mutual Fund")
    total_amount = round(quantity * price, 2)
    stt          = 0.0 if is_mf else round(total_amount * 0.001, 2)

    res = await session.execute(
        select(TrackerHolding).where(
            TrackerHolding.portfolio_id == portfolio_id,
            TrackerHolding.symbol == symbol,
        )
    )
    holding = res.scalar_one_or_none()

    if holding:
        old_total          = holding.avg_buy_price * holding.quantity
        new_qty            = holding.quantity + quantity
        holding.avg_buy_price = (old_total + total_amount) / new_qty
        holding.quantity   = new_qty
        holding.updated_at = datetime.utcnow()
        if trade_date < holding.first_buy_date:
            holding.first_buy_date = trade_date
    else:
        holding = TrackerHolding(
            portfolio_id=portfolio_id,
            symbol=symbol,
            company_name=company_name,
            sector=sector,
            quantity=quantity,
            avg_buy_price=price,
            first_buy_date=trade_date,
            notes=notes,
        )
        session.add(holding)
        await session.flush()

    tx = TrackerTransaction(
        portfolio_id=portfolio_id,
        holding_id=holding.id,
        symbol=symbol,
        company_name=company_name,
        tx_type="BUY",
        quantity=quantity,
        price=price,
        total_amount=total_amount,
        brokerage=0.0,
        stt=stt,
        trade_date=trade_date,
        notes=notes,
    )
    session.add(tx)
    await session.commit()
    await session.refresh(holding)
    return _holding_to_dict(holding)


async def sell_holding(
    holding_id: str,
    quantity: float,
    price: float,
    trade_date: date,
    notes: str,
    session: AsyncSession,
) -> dict:
    res = await session.execute(
        select(TrackerHolding).where(TrackerHolding.id == holding_id)
    )
    holding = res.scalar_one_or_none()
    if not holding:
        raise ValueError(f"Holding {holding_id} not found")
    if quantity > holding.quantity + 1e-9:
        raise ValueError(f"Cannot sell {quantity}; only {holding.quantity} held")

    total_amount = round(quantity * price, 2)
    stt          = round(total_amount * 0.001, 2)

    tx = TrackerTransaction(
        portfolio_id=holding.portfolio_id,
        holding_id=holding.id,
        symbol=holding.symbol,
        company_name=holding.company_name,
        tx_type="SELL",
        quantity=quantity,
        price=price,
        total_amount=total_amount,
        brokerage=0.0,
        stt=stt,
        trade_date=trade_date,
        notes=notes,
    )
    session.add(tx)

    new_qty = holding.quantity - quantity
    if new_qty <= 1e-9:
        await session.delete(holding)
        remaining = 0.0
    else:
        holding.quantity   = new_qty
        holding.updated_at = datetime.utcnow()
        remaining          = new_qty

    await session.commit()
    return {"sold": True, "remaining_qty": round(remaining, 4), "symbol": holding.symbol}


# ── Stock search ──────────────────────────────────────────────────────────────

# Words that look like acronyms but are actually generic English; force title-case.
_FORCE_TITLE_CASE = {
    "BANK", "BANKS", "GOLD", "FUND", "FUNDS", "STEEL", "POWER", "AUTO",
    "OIL", "GAS", "CARD", "CARDS", "INFRA", "INDIA", "INDS", "IND", "LTD",
    "LIMITED", "GROUP", "LIFE", "MOTOR", "MOTORS", "CHEM", "CEMENT", "FOODS",
    "PHARMA", "PHARM", "PRO", "PLUS", "NEXT", "PAY", "PAYS", "SERV", "SER",
    "INDUSTRIES", "INSURANCE", "TEXTILES", "FIN", "FINANCE",
}

def _proper_case_name(raw: str) -> str:
    """Convert SCREAMING instrument names ("HDFC BANK") to presentable form ("HDFC Bank").

    Heuristic: short (2–6 char) all-caps tokens stay as-is *unless* they are
    in the deny-list of generic English words. HDFC / ICICI / SBI / ONGC pass
    through; BANK / GOLD / FUND get title-cased.
    """
    if not raw:
        return ""
    out = []
    for w in raw.split():
        bare = w.strip(".,&-")
        if (bare.isupper() and 2 <= len(bare) <= 6
                and bare not in _FORCE_TITLE_CASE):
            out.append(w)
        else:
            out.append(w.title())
    return " ".join(out)


async def search_stocks_async(query: str, session: AsyncSession) -> list[dict]:
    """Search the full NSE equity universe via the kite_instruments table.

    Ranking: exact tradingsymbol > tradingsymbol prefix > name prefix > substring.
    """
    from sqlalchemy import case, func, or_
    from db.models import KiteInstrument

    q = query.strip()
    if not q:
        return []

    q_upper   = q.upper()
    q_pattern = f"%{q_upper}%"
    q_prefix  = f"{q_upper}%"

    stmt = (
        select(KiteInstrument)
        .where(
            KiteInstrument.instrument_type == "EQ",
            KiteInstrument.segment == "NSE",
            # Exclude bonds / NCDs / SDLs / G-Secs which are also stored as EQ.
            KiteInstrument.name != "",                       # bonds often have blank names
            ~KiteInstrument.name.like(r"%\%%"),              # interest-rate bearing instruments
            ~KiteInstrument.tradingsymbol.like("%-SG"),      # State Government securities
            ~KiteInstrument.tradingsymbol.like("%-SK"),
            ~KiteInstrument.tradingsymbol.like("%-TB"),      # GOI T-Bills
            or_(
                func.upper(KiteInstrument.tradingsymbol).like(q_pattern),
                func.upper(KiteInstrument.name).like(q_pattern),
            ),
        )
        .order_by(
            case(
                (func.upper(KiteInstrument.tradingsymbol) == q_upper, 1),
                (func.upper(KiteInstrument.tradingsymbol).like(q_prefix), 2),
                (func.upper(KiteInstrument.name).like(q_prefix), 3),
                else_=4,
            ),
            KiteInstrument.tradingsymbol,
        )
        .limit(15)
    )
    rows = (await session.execute(stmt)).scalars().all()

    return [
        {
            "name":   _proper_case_name(r.name) or r.tradingsymbol,
            "symbol": f"{r.tradingsymbol}.NS",
            "ticker": r.tradingsymbol,
            "sector": NSE_SECTOR_MAP.get(f"{r.tradingsymbol}.NS", "Other"),
        }
        for r in rows
    ]


def search_stocks_live(query: str) -> list[dict]:
    """Direct yfinance lookup for any NSE ticker not in the hardcoded dict.

    Tries '{QUERY}.NS' and returns one result if the ticker has a valid price.
    Blocking — must be called via run_in_executor from async context.
    """
    ticker_str = query.strip().upper()
    if not ticker_str:
        return []
    symbol = ticker_str + ".NS"
    try:
        t = yf.Ticker(symbol)
        fast = t.fast_info
        price = getattr(fast, "last_price", None) or getattr(fast, "regularMarketPrice", None)
        if not price or float(price) <= 0:
            return []
        # Try to get long name / sector from .info (may be slow; tolerate failure)
        try:
            info   = t.info
            name   = info.get("longName") or info.get("shortName") or ticker_str
            sector = info.get("sector") or "Other"
        except Exception:
            name   = ticker_str
            sector = "Other"
        return [{"name": name, "symbol": symbol, "ticker": ticker_str, "sector": sector}]
    except Exception:
        return []


# ── Serializers ───────────────────────────────────────────────────────────────

def _get_company_name(symbol: str) -> str:
    for name, sym in NSE_STOCK_LOOKUP.items():
        if sym == symbol:
            return name
    return symbol.replace(".NS", "")


def _portfolio_to_dict(p: TrackerPortfolio) -> dict:
    return {
        "id":          p.id,
        "name":        p.name,
        "description": p.description,
        "currency":    p.currency,
        "is_active":   p.is_active,
        "created_at":  p.created_at.isoformat(),
        "updated_at":  p.updated_at.isoformat(),
    }


def _holding_to_dict(h: TrackerHolding) -> dict:
    is_mf = h.symbol.startswith("MF:")
    # Source inference: notes-prefixed for Zerodha-synced rows
    notes_str = (h.notes or "")
    if "source:zerodha" in notes_str.lower():
        source = "ZERODHA"
    elif is_mf:
        source = "MUTUAL_FUND"
    else:
        source = "MANUAL"
    return {
        "id":             h.id,
        "portfolio_id":   h.portfolio_id,
        "symbol":         h.symbol,
        "display_symbol": h.symbol[3:] if is_mf else h.symbol.replace(".NS", "").replace(".BO", ""),
        "is_mf":          is_mf,
        "source":         source,
        "company_name":   h.company_name,
        "sector":         h.sector,
        "quantity":       h.quantity,
        "avg_buy_price":  h.avg_buy_price,
        "first_buy_date": h.first_buy_date.isoformat() if h.first_buy_date else None,
        "notes":          h.notes,
        "created_at":     h.created_at.isoformat(),
        "updated_at":     h.updated_at.isoformat(),
    }


def _tx_to_dict(tx: TrackerTransaction) -> dict:
    return {
        "id":           tx.id,
        "portfolio_id": tx.portfolio_id,
        "holding_id":   tx.holding_id,
        "symbol":       tx.symbol,
        "company_name": tx.company_name,
        "tx_type":      tx.tx_type,
        "quantity":     tx.quantity,
        "price":        tx.price,
        "total_amount": tx.total_amount,
        "brokerage":    tx.brokerage,
        "stt":          tx.stt,
        "trade_date":   tx.trade_date.isoformat(),
        "notes":        tx.notes,
        "created_at":   tx.created_at.isoformat(),
    }
