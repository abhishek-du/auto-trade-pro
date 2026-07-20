"""Volatility / delta-neutral F&O strategies.

Phase 6: the "volatility brain" — trade IV instead of direction.

  - LONG straddle  (buy ATM CE + ATM PE) when IV-Rank is LOW  → expecting a
    volatility expansion / big move either way.
  - SHORT straddle is undefined-risk; in paper mode we approximate it but gate it
    behind ENABLE_FUTURES (treats the short legs with the margin model). Disabled
    by default — long-vol is the safe baseline.
  - Delta-neutral check: aggregate portfolio option delta; flag when it drifts.

Both legs are bought options (defined risk) → margin = total premium debit, so
LONG straddle needs no SPAN. Reuses selection.open_option_paper_trade per leg.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import OptionContractSnapshot, OpenPosition
from engine.fno import contracts as _contracts
from engine.fno.selection import (
    OptionTradeSpec, open_option_paper_trade, _latest_premium,
)
from utils.config import settings
from utils.logger import logger

# IV-Rank thresholds for the volatility regime.
_IV_RANK_LOW  = 30.0    # below → options cheap → buy vol (long straddle)
_IV_RANK_HIGH = 70.0    # above → options rich → (short vol, gated)


async def _atm_iv_rank(underlying: str, session: AsyncSession) -> tuple[float, float] | None:
    """Return (atm_iv, iv_rank) for an underlying from the latest snapshot + history."""
    from db.models import IVHistory
    from sqlalchemy import func as _f

    last_at = (await session.execute(
        select(_f.max(OptionContractSnapshot.snapshot_at))
        .where(OptionContractSnapshot.underlying == underlying.upper())
    )).scalar()
    if last_at is None:
        return None
    rows = (await session.execute(
        select(OptionContractSnapshot).where(
            OptionContractSnapshot.underlying == underlying.upper(),
            OptionContractSnapshot.snapshot_at == last_at,
        )
    )).scalars().all()
    if not rows:
        return None
    spot = rows[0].spot or 0.0
    atm = min({r.strike for r in rows}, key=lambda k: abs(k - spot), default=0.0)
    ce_iv = next((r.iv for r in rows if r.strike == atm and r.option_type == "CE" and r.iv), None)
    pe_iv = next((r.iv for r in rows if r.strike == atm and r.option_type == "PE" and r.iv), None)
    ivs = [v for v in (ce_iv, pe_iv) if v]
    if not ivs:
        return None
    atm_iv = sum(ivs) / len(ivs)

    hist = (await session.execute(
        select(IVHistory.atm_iv).where(IVHistory.underlying == underlying.upper())
    )).scalars().all()
    if len(hist) < 5:
        return atm_iv, 50.0
    lo, hi = min(hist), max(hist)
    rank = 100 * (atm_iv - lo) / (hi - lo) if hi > lo else 50.0
    return atm_iv, round(rank, 1)


async def _build_leg(underlying: str, option_type: str, spot: float,
                     equity: float, session: AsyncSession) -> OptionTradeSpec | None:
    """Build one ATM option leg spec (1 lot — straddle legs are paired)."""
    contract = await _contracts.resolve_option(underlying, option_type, spot, session)
    if contract is None:
        return None
    premium = await _latest_premium(underlying, contract.strike, option_type, contract.expiry, session)
    if not premium or premium <= 0:
        return None
    lots = 1
    qty = lots * contract.lot_size
    debit = round(qty * premium, 2)
    return OptionTradeSpec(
        underlying=underlying.upper(), tradingsymbol=contract.tradingsymbol,
        option_type=option_type, strike=contract.strike, expiry=contract.expiry,
        lot_size=contract.lot_size, premium=round(premium, 2), lots=lots, qty=qty,
        notional=debit, stop=round(premium * 0.5, 2), target=round(premium * 2.0, 2),
        dte=contract.dte,
    )


async def open_long_straddle(underlying: str, spot: float, equity: float,
                             session: AsyncSession, *, confidence: float = 0.0) -> dict | None:
    """Buy ATM CE + ATM PE (long volatility). Defined risk = total premium."""
    ce = await _build_leg(underlying, "CE", spot, equity, session)
    pe = await _build_leg(underlying, "PE", spot, equity, session)
    if ce is None or pe is None:
        logger.debug(f"[fno/vol] {underlying}: cannot build both straddle legs")
        return None

    from engine.decision_router import TradeIntent, ConfidenceSource, EventDirectness, StrategyFamily, authorize_trade_intent
    _intent = TradeIntent(
        strategy="FNO_LONG_STRADDLE", symbol=ce.tradingsymbol, action="BUY", instrument_type="CE",
        entry_price=ce.premium, stop_loss=ce.stop, take_profit=ce.target,
        confidence=confidence, confidence_source=ConfidenceSource.CALCULATED,
        strategy_family=StrategyFamily.FNO,
        event_directness=EventDirectness.NOT_APPLICABLE,
    )
    _auth = await authorize_trade_intent(_intent, session)
    if not _auth.approved:
        logger.info(f"[fno/vol] {underlying} straddle gate blocked: {_auth.reason}")
        return None

    t_ce = await open_option_paper_trade(
        ce, session, confidence=confidence,
        ai_reason=f"📊 LONG STRADDLE leg CE {ce.strike:.0f} | long volatility",
    )
    t_pe = await open_option_paper_trade(
        pe, session, confidence=confidence,
        ai_reason=f"📊 LONG STRADDLE leg PE {pe.strike:.0f} | long volatility",
    )
    legs = [t for t in (t_ce, t_pe) if t]
    if not legs:
        return None
    total_debit = sum(l.size_usd for l in legs)
    logger.info(f"[fno/vol] LONG STRADDLE {underlying} {ce.strike:.0f} | "
                f"{len(legs)} leg(s) | debit ₹{total_debit:,.0f}")
    return {"strategy": "LONG_STRADDLE", "underlying": underlying,
            "strike": ce.strike, "legs": len(legs), "debit": total_debit}


async def portfolio_delta(session: AsyncSession) -> float:
    """Aggregate signed option delta across open option positions (qty-weighted).

    Uses each position's strike snapshot delta × size_units × sign(long=+).
    Useful to detect when a 'neutral' book has drifted directional.
    """
    rows = (await session.execute(
        select(OpenPosition).where(OpenPosition.instrument_type.in_(["CE", "PE"]))
    )).scalars().all()
    total = 0.0
    for pos in rows:
        snap = (await session.execute(
            select(OptionContractSnapshot.delta).where(
                OptionContractSnapshot.underlying == pos.underlying_symbol,
                OptionContractSnapshot.strike == pos.strike_price,
                OptionContractSnapshot.option_type == pos.option_type,
            ).order_by(OptionContractSnapshot.snapshot_at.desc()).limit(1)
        )).scalar_one_or_none()
        if snap is not None:
            total += float(snap) * pos.size_units   # long options: +delta
    return round(total, 2)


async def evaluate_volatility(session: AsyncSession, equity: float) -> list[dict]:
    """Open a long straddle on indices whose IV-Rank is low (cheap options).

    Gated by ENABLE_OPTIONS (long straddle = bought options, defined risk).
    Skips an underlying that already has any open option position.
    """
    # HARD BLOCK — News-Only Target Architecture (Phase 1). See
    # docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md §6: "Independent
    # volatility strategy" (straddle + iron condor, both handled below in this
    # function) — no news catalyst, FORBIDDEN. Hardcoded ahead of the existing
    # feature flags so this can't be silently re-enabled by flipping them.
    _NEWS_ONLY_BLOCKS_HUB_ENTRIES = True
    if _NEWS_ONLY_BLOCKS_HUB_ENTRIES:
        return []
    if not (settings.ENABLE_FNO and settings.ENABLE_OPTIONS):
        return []

    open_unders = set((await session.execute(
        select(OpenPosition.underlying_symbol).where(
            OpenPosition.instrument_type.in_(["CE", "PE"])
        )
    )).scalars().all())

    opened: list[dict] = []
    for under in settings.fno_index_symbols:
        try:
            if under in open_unders:
                continue
            ivr = await _atm_iv_rank(under, session)
            if ivr is None:
                continue
            atm_iv, rank = ivr
            if _IV_RANK_LOW <= rank <= _IV_RANK_HIGH:
                continue
                
            # Spot from the latest snapshot.
            spot = (await session.execute(
                select(OptionContractSnapshot.spot).where(
                    OptionContractSnapshot.underlying == under.upper()
                ).order_by(OptionContractSnapshot.snapshot_at.desc()).limit(1)
            )).scalar_one_or_none()
            if not spot:
                continue
                
            if rank < _IV_RANK_LOW:
                res = await open_long_straddle(under, float(spot), equity, session,
                                               confidence=round(100 - rank, 1))
                if res:
                    res["iv_rank"] = rank
                    opened.append(res)
            elif rank > _IV_RANK_HIGH:
                res = await select_iron_condor(under, float(spot), equity, session)
                if res:
                    from engine.decision_router import TradeIntent, ConfidenceSource, EventDirectness, StrategyFamily, authorize_trade_intent
                    _intent = TradeIntent(
                        strategy="FNO_IRON_CONDOR", symbol=res.ts_short_ce, action="SELL", instrument_type="CE",
                        entry_price=res.net_credit, stop_loss=0.0, take_profit=0.0,
                        confidence=round(rank, 1), confidence_source=ConfidenceSource.CALCULATED,
                        strategy_family=StrategyFamily.FNO,
                        event_directness=EventDirectness.NOT_APPLICABLE,
                    )
                    _auth = await authorize_trade_intent(_intent, session)
                    if not _auth.approved:
                        logger.info(f"[fno/vol] {under} iron condor gate blocked: {_auth.reason}")
                        continue
                    trades = await open_iron_condor_paper_trade(res, session, confidence=round(rank, 1))
                    if trades:
                        opened.append({
                            "strategy": "IRON_CONDOR", "underlying": under,
                            "legs": len(trades), "net_credit": res.net_credit,
                            "iv_rank": rank
                        })
        except Exception as exc:
            logger.warning(f"[fno/vol] {under} failed: {exc}")

    return opened

# ── Iron Condor Execution Logic ──────────────────────────────────────────────

from db.models import PaperTrade, TradeDirection, TradeStatus

@dataclass
class IronCondorSpec:
    underlying: str
    expiry: date
    lot_size: int
    lots: int
    qty: int
    
    strike_short_ce: float
    strike_long_ce: float
    premium_short_ce: float
    premium_long_ce: float
    ts_short_ce: str
    ts_long_ce: str
    
    strike_short_pe: float
    strike_long_pe: float
    premium_short_pe: float
    premium_long_pe: float
    ts_short_pe: str
    ts_long_pe: str
    
    net_credit: float
    margin_blocked: float
    dte: int

def get_condor_widths(underlying: str) -> tuple[float, float]:
    under = underlying.upper()
    if "BANK" in under or "SENSEX" in under:
        return (500.0, 1000.0)
    return (200.0, 400.0)

async def select_iron_condor(
    underlying: str, spot: float, equity: float, session: AsyncSession
) -> IronCondorSpec | None:
    # First get ATM strike for CE
    contract_atm = await _contracts.resolve_option(underlying, "CE", spot, session)
    if not contract_atm:
        contract_atm = await _contracts.resolve_option_from_snapshot(underlying, "CE", spot, session)
    if not contract_atm:
        return None
        
    atm_strike = contract_atm.strike
    expiry = contract_atm.expiry
    lot_size = contract_atm.lot_size or 1
    
    w_short, w_long = get_condor_widths(underlying)
    
    s_ce = atm_strike + w_short
    l_ce = atm_strike + w_long
    s_pe = atm_strike - w_short
    l_pe = atm_strike - w_long
    
    # Resolve all 4 legs
    c_s_ce = await _contracts.resolve_option(underlying, "CE", s_ce, session)
    c_l_ce = await _contracts.resolve_option(underlying, "CE", l_ce, session)
    c_s_pe = await _contracts.resolve_option(underlying, "PE", s_pe, session)
    c_l_pe = await _contracts.resolve_option(underlying, "PE", l_pe, session)
    
    if not all([c_s_ce, c_l_ce, c_s_pe, c_l_pe]):
        return None
        
    p_s_ce = await _latest_premium(underlying, s_ce, "CE", expiry, session)
    p_l_ce = await _latest_premium(underlying, l_ce, "CE", expiry, session)
    p_s_pe = await _latest_premium(underlying, s_pe, "PE", expiry, session)
    p_l_pe = await _latest_premium(underlying, l_pe, "PE", expiry, session)
    
    if not all([p_s_ce, p_l_ce, p_s_pe, p_l_pe]):
        return None
        
    net_credit = (p_s_ce + p_s_pe) - (p_l_ce + p_l_pe)
    if net_credit <= 0:
        return None
        
    # Sizing: risk budget = max loss = (long strike - short strike) - net_credit
    max_loss_per_qty = (l_ce - s_ce) - net_credit 
    if max_loss_per_qty <= 0:
        max_loss_per_qty = 1.0 # just in case
        
    risk_budget = equity * settings.AGENT_MAX_RISK_PER_TRADE
    risk_per_lot = max_loss_per_qty * lot_size
    lots = int(risk_budget // risk_per_lot) if risk_per_lot > 0 else 0
    lots = max(1, min(lots, settings.FNO_MAX_LOTS_PER_TRADE))
    qty = lots * lot_size
    
    margin_blocked = max(60000.0 * lots, (l_ce - s_ce) * qty)
    
    if margin_blocked > equity:
        lots = max(1, int(equity // 60000.0))
        qty = lots * lot_size
        margin_blocked = max(60000.0 * lots, (l_ce - s_ce) * qty)
        
    return IronCondorSpec(
        underlying=underlying.upper(), expiry=expiry, lot_size=lot_size, lots=lots, qty=qty,
        strike_short_ce=s_ce, strike_long_ce=l_ce, premium_short_ce=p_s_ce, premium_long_ce=p_l_ce,
        ts_short_ce=c_s_ce.tradingsymbol, ts_long_ce=c_l_ce.tradingsymbol,
        strike_short_pe=s_pe, strike_long_pe=l_pe, premium_short_pe=p_s_pe, premium_long_pe=p_l_pe,
        ts_short_pe=c_s_pe.tradingsymbol, ts_long_pe=c_l_pe.tradingsymbol,
        net_credit=round(net_credit, 2), margin_blocked=margin_blocked, dte=contract_atm.dte
    )

async def open_iron_condor_paper_trade(
    spec: IronCondorSpec, session: AsyncSession, *, confidence: float = 0.0, ai_reason: str = ""
) -> list[PaperTrade]:
    from paper_trading.virtual_wallet import VirtualWallet
    from sqlalchemy import delete

    _max = settings.AGENT_EQUITY * settings.AGENT_MAX_POSITION_WEIGHT
    if spec.margin_blocked > _max * 1.10:
        logger.error(f"[fno/condor] HARD GUARD: margin {spec.margin_blocked} > max {_max}")
        return []

    # Duplicate check
    existing = (await session.execute(
        select(OpenPosition.symbol).where(
            OpenPosition.underlying_symbol == spec.underlying
        )
    )).scalars().all()
    if existing:
        logger.warning(f"[fno/condor] BLOCKED {spec.underlying} — already have positions")
        return []

    now = datetime.utcnow()
    label = f"{spec.underlying} IRON CONDOR {spec.expiry:%d-%b}"
    ai_reason = ai_reason or f"📊 IRON CONDOR | {spec.lots} lot(s) | Net Credit ₹{spec.net_credit}"

    trades = []
    positions = []
    
    # Helper to add leg
    def add_leg(ts, strike, opt_type, direction, premium):
        t = PaperTrade(
            symbol=ts, direction=direction, status=TradeStatus.OPEN,
            entry_price=premium, stop_loss=0, take_profit=0, size_units=spec.qty,
            size_usd=premium * spec.qty, instrument_type=opt_type,
            underlying_symbol=spec.underlying, strike_price=strike, option_type=opt_type,
            expiry_date=spec.expiry, lot_size=spec.lot_size, contract_multiplier=1.0,
            margin_blocked=spec.margin_blocked if len(trades) == 0 else 0,
            signal_confidence=confidence, pattern_name="FNO_IRON_CONDOR",
            ai_reason=ai_reason, opened_at=now
        )
        session.add(t)
        trades.append(t)
        
        p = OpenPosition(
            symbol=ts, direction=direction, entry_price=premium,
            current_price=premium, stop_loss=0, take_profit=0, size_units=spec.qty,
            size_usd=premium * spec.qty, instrument_type=opt_type,
            underlying_symbol=spec.underlying, strike_price=strike, option_type=opt_type,
            expiry_date=spec.expiry, lot_size=spec.lot_size, contract_multiplier=1.0,
            margin_blocked=spec.margin_blocked if len(positions) == 0 else 0,
            unrealised_pnl=0.0, unrealised_pct=0.0, trade_id=None, opened_at=now
        )
        session.add(p)
        positions.append(p)
        return p

    p1 = add_leg(spec.ts_short_ce, spec.strike_short_ce, "CE", TradeDirection.SELL, spec.premium_short_ce)
    p2 = add_leg(spec.ts_long_ce, spec.strike_long_ce, "CE", TradeDirection.BUY, spec.premium_long_ce)
    p3 = add_leg(spec.ts_short_pe, spec.strike_short_pe, "PE", TradeDirection.SELL, spec.premium_short_pe)
    p4 = add_leg(spec.ts_long_pe, spec.strike_long_pe, "PE", TradeDirection.BUY, spec.premium_long_pe)

    await session.flush()
    # associate trade_id
    for t, p in zip(trades, positions):
        p.trade_id = t.id

    ok, msg = await VirtualWallet.deduct_margin(session, spec.margin_blocked, f"CONDOR_{spec.underlying}")
    if not ok:
        for p in positions: await session.execute(delete(OpenPosition).where(OpenPosition.id == p.id))
        for t in trades: await session.execute(delete(PaperTrade).where(PaperTrade.id == t.id))
        await session.flush()
        logger.warning(f"[fno/exec] BLOCKED {label} — {msg}")
        return []

    await session.commit()
    logger.info(f"[PAPER-FNO] IRON CONDOR {label} | {spec.lots} lot(s) | Net Credit ₹{spec.net_credit} | Margin ₹{spec.margin_blocked:,.0f}")

    try:
        if settings.telegram_available:
            from integrations.telegram_service import send
            max_profit = spec.net_credit * spec.qty
            max_loss = ((spec.strike_long_ce - spec.strike_short_ce) - spec.net_credit) * spec.qty
            await send(
                f"🦅 <b>F&O IRON CONDOR</b>\n"
                f"<b>{spec.underlying}</b> ({spec.expiry:%d-%b-%Y})\n"
                f"SELL {spec.strike_short_ce:.0f}CE @ ₹{spec.premium_short_ce:.1f}\n"
                f"BUY  {spec.strike_long_ce:.0f}CE @ ₹{spec.premium_long_ce:.1f}\n"
                f"SELL {spec.strike_short_pe:.0f}PE @ ₹{spec.premium_short_pe:.1f}\n"
                f"BUY  {spec.strike_long_pe:.0f}PE @ ₹{spec.premium_long_pe:.1f}\n"
                f"Net Credit: <b>₹{spec.net_credit}</b>  |  {spec.lots} lot(s)\n"
                f"Max Profit: ₹{max_profit:,.0f}  |  Max Loss: ₹{max_loss:,.0f}\n"
                f"Margin Blocked: ₹{spec.margin_blocked:,.0f}\n"
                f"IV Rank: {confidence:.0f}% (High IV)"
            )
    except Exception as exc:
        logger.debug(f"[fno/exec] telegram alert failed: {exc}")

    return trades
