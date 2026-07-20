"""Index futures — directional paper trading with the approximate margin model.

Futures are leveraged: margin is a fraction of notional (SPAN+exposure), not the
full notional. Long or short both allowed (unlike equity cash). P&L is
linear: qty × (exit − entry) for long, mirrored for short.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    PaperTrade, OpenPosition, Candle, TradeDirection, TradeStatus,
)
from engine.fno import contracts as _contracts
from engine.fno import margin as _margin
from utils.config import settings
from utils.logger import logger

# Directional futures exit bands (% of entry price).
_STOP_PCT   = 0.015   # 1.5%
_TARGET_PCT = 0.030   # 3.0%  → RR 1:2

# Underlying → index candle symbol (shared with selection).
_INDEX_CANDLE = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}


@dataclass
class FutureTradeSpec:
    underlying:    str
    tradingsymbol: str
    direction:     str       # BUY (long) | SELL (short)
    expiry:        date
    lot_size:      int
    entry:         float
    lots:          int
    qty:           int
    notional:      float
    margin:        float     # SPAN+exposure approximation
    stop:          float
    target:        float
    dte:           int


async def select_index_future(
    underlying: str,
    direction: str,
    spot: float,
    equity: float,
    session: AsyncSession,
) -> FutureTradeSpec | None:
    """Resolve a directional signal to a futures contract + margin-aware sizing."""
    contract = await _contracts.resolve_future(underlying, session)
    if contract is None:
        logger.debug(f"[fno/fut] {underlying}: no resolvable futures contract")
        return None

    lot_size = contract.lot_size or 1
    entry = spot
    # Size by per-lot risk against the % stop, capped by lots and available margin.
    stop_dist = entry * _STOP_PCT
    risk_budget = equity * settings.AGENT_MAX_RISK_PER_TRADE
    risk_per_lot = stop_dist * lot_size
    lots = int(risk_budget // risk_per_lot) if risk_per_lot > 0 else 0
    # B11 fix: no max(1,...) floor. If the risk budget can't afford even one lot,
    # size 0 and skip the trade rather than force a lot that breaches the per-trade
    # risk cap. The caller treats lots<=0 as "no trade".
    lots = min(lots, settings.FNO_MAX_LOTS_PER_TRADE)
    if lots < 1:
        return None

    qty = lots * lot_size
    notional = round(qty * entry, 2)
    margin = _margin.span_exposure_margin(notional)

    # Trim lots until margin fits both the available budget AND 5% cap.
    _max_margin = equity * settings.AGENT_MAX_POSITION_WEIGHT
    while lots > 1:
        ok, _ = await _margin.can_block_margin(margin, equity, session)
        if ok and margin <= _max_margin * 1.10:
            break
        lots -= 1
        qty = lots * lot_size
        notional = round(qty * entry, 2)
        margin = _margin.span_exposure_margin(notional)

    # If even 1 lot exceeds the margin cap, reject entirely
    if margin > _max_margin * 1.10:
        logger.warning(
            f"[fno/fut] {underlying}: 1 lot margin ₹{margin:,.0f} exceeds "
            f"5% cap ₹{_max_margin:,.0f} — skipping"
        )
        return None

    if direction.upper() == "BUY":
        stop   = round(entry * (1 - _STOP_PCT), 2)
        target = round(entry * (1 + _TARGET_PCT), 2)
    else:
        stop   = round(entry * (1 + _STOP_PCT), 2)
        target = round(entry * (1 - _TARGET_PCT), 2)

    return FutureTradeSpec(
        underlying=underlying.upper(), tradingsymbol=contract.tradingsymbol,
        direction=direction.upper(), expiry=contract.expiry, lot_size=lot_size,
        entry=round(entry, 2), lots=lots, qty=qty, notional=notional, margin=margin,
        stop=stop, target=target, dte=contract.dte,
    )


async def open_future_paper_trade(
    spec: FutureTradeSpec, session: AsyncSession, *, confidence: float = 0.0,
) -> PaperTrade | None:
    """Open a futures paper position; blocks the approximate SPAN margin."""
    from paper_trading.virtual_wallet import VirtualWallet

    # Hard guard: margin blocked must not exceed 5% of equity
    _max_margin = settings.AGENT_EQUITY * settings.AGENT_MAX_POSITION_WEIGHT
    if spec.margin > _max_margin * 1.10:
        logger.error(
            f"[fno/fut] HARD GUARD: {spec.tradingsymbol} margin ₹{spec.margin:,.0f} "
            f"exceeds {settings.AGENT_MAX_POSITION_WEIGHT*100:.0f}% of equity (max ₹{_max_margin:,.0f})"
        )
        return None

    # Duplicate guard: no two positions on the same underlying
    existing = (await session.execute(
        select(OpenPosition.symbol).where(
            OpenPosition.underlying_symbol == spec.underlying
        )
    )).scalars().all()
    if existing:
        logger.warning(f"[fno/fut] BLOCKED {spec.underlying} FUT — already have {existing[0]}")
        return None

    ok, msg = await _margin.can_block_margin(spec.margin, settings.AGENT_EQUITY, session)
    if not ok:
        logger.warning(f"[fno/fut] BLOCKED {spec.underlying} FUT — {msg}")
        return None

    now = datetime.utcnow()
    direction = TradeDirection(spec.direction)
    label = f"{spec.underlying} FUT {spec.expiry:%d-%b}"

    trade = PaperTrade(
        symbol=spec.tradingsymbol, direction=direction, status=TradeStatus.OPEN,
        entry_price=spec.entry, stop_loss=spec.stop, take_profit=spec.target,
        size_units=spec.qty, size_usd=spec.notional,
        instrument_type="FUTURE", underlying_symbol=spec.underlying,
        expiry_date=spec.expiry, lot_size=spec.lot_size, contract_multiplier=1.0,
        margin_blocked=spec.margin, signal_confidence=confidence,
        pattern_name="FNO_FUTURE", ai_reason=f"📥 {spec.direction} {label} | {spec.lots} lot(s)",
        news_sentiment_score=0.0, slippage_applied=0.0, opened_at=now,
    )
    session.add(trade)
    await session.flush()

    position = OpenPosition(
        symbol=spec.tradingsymbol, direction=direction,
        entry_price=spec.entry, current_price=spec.entry,
        stop_loss=spec.stop, take_profit=spec.target,
        size_units=spec.qty, size_usd=spec.notional,
        instrument_type="FUTURE", underlying_symbol=spec.underlying,
        expiry_date=spec.expiry, lot_size=spec.lot_size, contract_multiplier=1.0,
        margin_blocked=spec.margin, unrealised_pnl=0.0, unrealised_pct=0.0,
        trade_id=trade.id, opened_at=now,
    )
    session.add(position)
    await session.flush()

    # Block only the margin (not full notional) — this is the leverage.
    ok, wmsg = await VirtualWallet.deduct_margin(session, spec.margin, spec.tradingsymbol)
    if not ok:
        await session.execute(delete(OpenPosition).where(OpenPosition.id == position.id))
        await session.execute(delete(PaperTrade).where(PaperTrade.id == trade.id))
        await session.flush()
        logger.warning(f"[fno/fut] BLOCKED {label} — {wmsg}")
        return None

    await session.commit()
    logger.info(
        f"[PAPER-FNO] {spec.direction} {label} | {spec.lots} lot(s) ({spec.qty} qty) @ ₹{spec.entry:,.0f} "
        f"| SL ₹{spec.stop:,.0f} TP ₹{spec.target:,.0f} | margin ₹{spec.margin:,.0f} | {spec.dte}d"
    )

    # ── Telegram alert (F&O futures) ──────────────────────────────────────────
    try:
        if settings.telegram_available:
            from integrations.telegram_service import send
            await send(
                f"📈 <b>F&O FUTURES {spec.direction}</b>\n"
                f"<b>{spec.underlying} FUT</b>  ·  {spec.expiry:%d-%b-%Y} ({spec.dte}d)\n"
                f"Entry: <b>{spec.entry:,.0f}</b>  |  {spec.lots} lot × {spec.lot_size} = {spec.qty} qty\n"
                f"SL {spec.stop:,.0f}  ·  TP {spec.target:,.0f}\n"
                f"Margin: ₹{spec.margin:,.0f}  |  Notional: ₹{spec.notional:,.0f}\n"
                f"Conviction: {confidence:.0f}%"
            )
    except Exception as exc:
        logger.debug(f"[fno/fut] telegram alert failed: {exc}")

    return trade


async def current_future_price(pos: OpenPosition, session: AsyncSession) -> float | None:
    """Live futures price — Kite LTP (real-time) → index candle fallback."""
    # 1. Live Kite LTP for the exact futures contract.
    try:
        from crawler.zerodha_client import get_kite_client
        kite = get_kite_client()
        if kite.access_token:
            raw = await kite.get_ltp([f"NFO:{pos.symbol}"])
            d = (raw or {}).get(f"NFO:{pos.symbol}")
            if d and d.get("last_price", 0) > 0:
                return float(d["last_price"])
    except Exception:
        pass
    # 2. Fallback: latest index candle (≈ spot; basis ignored in paper).
    csym = _INDEX_CANDLE.get((pos.underlying_symbol or "").upper())
    if not csym:
        return None
    row = (await session.execute(
        select(Candle.close).where(Candle.symbol == csym, Candle.timeframe == "1d")
        .order_by(Candle.timestamp.desc()).limit(1)
    )).scalar_one_or_none()
    return float(row) if row else None


def future_pnl(pos: OpenPosition, cur: float) -> tuple[float, float]:
    """Unrealised P&L for a futures position. Returns (pnl, pct-on-margin)."""
    if pos.direction == TradeDirection.BUY:
        pnl = (cur - pos.entry_price) * pos.size_units
    else:
        pnl = (pos.entry_price - cur) * pos.size_units
    base = pos.margin_blocked or pos.size_usd or 1.0
    return round(pnl, 2), round(pnl / base * 100, 2)


async def evaluate_index_futures(session: AsyncSession, equity: float) -> list[dict]:
    """Evaluate each index for a directional futures trade (gated by ENABLE_FUTURES)."""
    if not (settings.ENABLE_FNO and settings.ENABLE_FUTURES):
        return []

    from crawler.price_feed import get_latest_candles
    from engine.fno.selection import _index_signal   # reuse the index signal

    open_unders = set((await session.execute(
        select(OpenPosition.underlying_symbol).where(
            OpenPosition.underlying_symbol != None,
            OpenPosition.instrument_type == "FUTURE",
        )
    )).scalars().all())

    threshold = float(settings.AGENT_CONFIDENCE_THRESHOLD)
    opened: list[dict] = []

    for under in settings.fno_index_symbols:
        try:
            if under in open_unders:
                continue
            csym = _INDEX_CANDLE.get(under)
            if not csym:
                continue
            candles = await get_latest_candles(csym, "1d", 60, session)
            if not candles or len(candles) < 25:
                continue
            closes = [float(c.close) for c in reversed(candles)]
            sig = _index_signal(closes)
            if sig is None:
                continue
            direction, confidence, spot = sig
            if confidence < threshold:
                continue
            spec = await select_index_future(under, direction, spot, equity, session)
            if spec is None:
                continue

            from engine.decision_router import TradeIntent, ConfidenceSource, EventDirectness, StrategyFamily, authorize_trade_intent
            _intent = TradeIntent(
                strategy="FNO_FUTURE", symbol=spec.tradingsymbol, action=direction, instrument_type="FUTURE",
                entry_price=spec.entry, stop_loss=0.0, take_profit=0.0,
                confidence=confidence, confidence_source=ConfidenceSource.CALCULATED,
                strategy_family=StrategyFamily.FNO,
                event_directness=EventDirectness.NOT_APPLICABLE,
            )
            _auth = await authorize_trade_intent(_intent, session)
            if not _auth.approved:
                logger.info(f"[fno/fut] {under} gate blocked: {_auth.reason}")
                continue

            trade = await open_future_paper_trade(spec, session, confidence=confidence)
            if trade:
                opened.append({
                    "underlying": under, "direction": direction,
                    "tradingsymbol": spec.tradingsymbol, "lots": spec.lots,
                    "entry": spec.entry, "margin": spec.margin, "confidence": confidence,
                })
        except Exception as exc:
            logger.warning(f"[fno/fut] {under} failed: {exc}")

    return opened
