"""F&O option selection, lot-rounded sizing, paper execution, and mark-to-market.

Phase 3 scope: defined-risk OPTION BUYING only.
  BUY signal  → buy a CE (call)
  SELL signal → buy a PE (put)

Margin for a bought option = premium debit (max loss is the premium), so this
path needs no SPAN model. Trades are written to the same paper_trades /
open_positions tables as equity (with the F&O columns populated) so they show on
the Trades page. The equity execution path is left completely untouched.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, date

from sqlalchemy import select, func as sqlfunc, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    PaperTrade, OpenPosition, OptionContractSnapshot,
    TradeDirection, TradeStatus,
)
from engine.fno import contracts as _contracts
from engine.fno import options_pricing as _bs
from utils.config import settings
from utils.logger import logger

# Long-option exit ladder (fractions of entry premium).
_STOP_FRACTION   = 0.50   # cut at -50% premium
_TARGET_FRACTION = 1.00   # book at +100% premium


@dataclass
class OptionTradeSpec:
    underlying:    str
    tradingsymbol: str
    option_type:   str       # CE | PE
    strike:        float
    expiry:        date
    lot_size:      int
    premium:       float     # entry premium (per unit)
    lots:          int
    qty:           int       # lots × lot_size
    notional:      float     # qty × premium  (= premium debit = margin)
    stop:          float     # premium stop
    target:        float     # premium target
    dte:           int


# ── Premium lookup ───────────────────────────────────────────────────────────

async def _latest_premium(
    underlying: str, strike: float, option_type: str, expiry: date,
    session: AsyncSession,
) -> float | None:
    """Most recent traded premium for a specific contract from the snapshot table."""
    row = (await session.execute(
        select(OptionContractSnapshot.ltp)
        .where(
            OptionContractSnapshot.underlying == underlying.upper(),
            OptionContractSnapshot.strike == strike,
            OptionContractSnapshot.option_type == option_type,
            OptionContractSnapshot.expiry_date == expiry,
        )
        .order_by(OptionContractSnapshot.snapshot_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    return float(row) if row and row > 0 else None


# ── Selection + sizing ───────────────────────────────────────────────────────

async def select_index_option(
    underlying: str,
    direction: str,          # "BUY" | "SELL"
    spot: float,
    equity: float,
    session: AsyncSession,
) -> OptionTradeSpec | None:
    """Resolve a directional signal to a concrete option + lot-rounded size.

    Returns None when no instrument master / premium is available.
    """
    option_type = "CE" if direction.upper() == "BUY" else "PE"
    # 1. Try the Kite instrument master (real broker contracts).
    contract = await _contracts.resolve_option(underlying, option_type, spot, session)
    # 2. PAPER fallback: build from the live NSE chain + standard lot sizes
    #    (no Kite login needed). Always used in paper mode when Kite is empty.
    if contract is None:
        contract = await _contracts.resolve_option_from_snapshot(
            underlying, option_type, spot, session
        )
    if contract is None:
        logger.debug(f"[fno/select] {underlying} {option_type}: no resolvable contract")
        return None

    premium = await _latest_premium(
        underlying, contract.strike, option_type, contract.expiry, session
    )
    if (premium is None or premium <= 0):
        # Kite master may resolve a next-month expiry whose chain isn't in
        # our snapshot yet — retry with the snapshot resolver (weekly expiry).
        snap_contract = await _contracts.resolve_option_from_snapshot(
            underlying, option_type, spot, session
        )
        if snap_contract and snap_contract.expiry != contract.expiry:
            premium = await _latest_premium(
                underlying, snap_contract.strike, option_type, snap_contract.expiry, session
            )
            if premium and premium > 0:
                contract = snap_contract
    if premium is None or premium <= 0:
        logger.debug(f"[fno/select] {underlying} {contract.strike}{option_type}: no premium")
        return None

    lot_size = contract.lot_size or 1
    # Size so the 50%-premium stop risks ≈ AGENT_MAX_RISK_PER_TRADE of equity.
    risk_budget = equity * settings.AGENT_MAX_RISK_PER_TRADE
    risk_per_lot = premium * lot_size * _STOP_FRACTION
    lots = int(risk_budget // risk_per_lot) if risk_per_lot > 0 else 0
    lots = max(1, min(lots, settings.FNO_MAX_LOTS_PER_TRADE))

    qty = lots * lot_size
    notional = round(qty * premium, 2)
    # Never deploy more premium than available equity.
    if notional > equity:
        lots = max(1, int(equity // (premium * lot_size)))
        qty = lots * lot_size
        notional = round(qty * premium, 2)

    return OptionTradeSpec(
        underlying=underlying.upper(),
        tradingsymbol=contract.tradingsymbol,
        option_type=option_type,
        strike=contract.strike,
        expiry=contract.expiry,
        lot_size=lot_size,
        premium=round(premium, 2),
        lots=lots,
        qty=qty,
        notional=notional,
        stop=round(premium * (1 - _STOP_FRACTION), 2),
        target=round(premium * (1 + _TARGET_FRACTION), 2),
        dte=contract.dte,
    )


# ── Paper execution ──────────────────────────────────────────────────────────

async def open_option_paper_trade(
    spec: OptionTradeSpec,
    session: AsyncSession,
    *,
    confidence: float = 0.0,
    ai_reason: str = "",
) -> PaperTrade | None:
    """Open an F&O option paper position (PaperTrade + OpenPosition).

    Long options only: direction is always BUY (we bought the CE/PE). The
    premium debit is deducted from the virtual wallet as margin.
    """
    from paper_trading.virtual_wallet import VirtualWallet

    # Hard guard: premium cost must not exceed 5% of equity
    _max = settings.AGENT_EQUITY * settings.AGENT_MAX_POSITION_WEIGHT
    if spec.notional > _max * 1.10:
        logger.error(
            f"[fno/opt] HARD GUARD: {spec.tradingsymbol} cost ₹{spec.notional:,.0f} "
            f"exceeds {settings.AGENT_MAX_POSITION_WEIGHT*100:.0f}% of equity (max ₹{_max:,.0f})"
        )
        return None

    # Duplicate guard: no two options on the same underlying+type
    existing = (await session.execute(
        select(OpenPosition.symbol).where(
            OpenPosition.underlying_symbol == spec.underlying,
            OpenPosition.option_type == spec.option_type,
        )
    )).scalars().all()
    if existing:
        logger.warning(
            f"[fno/opt] BLOCKED {spec.tradingsymbol} — already have {existing[0]}"
        )
        return None

    now = datetime.utcnow()
    label = f"{spec.underlying} {spec.strike:.0f}{spec.option_type} {spec.expiry:%d-%b}"

    trade = PaperTrade(
        symbol=spec.tradingsymbol,
        direction=TradeDirection.BUY,          # long option
        status=TradeStatus.OPEN,
        entry_price=spec.premium,
        stop_loss=spec.stop,
        take_profit=spec.target,
        size_units=spec.qty,
        size_usd=spec.notional,
        instrument_type=spec.option_type,       # "CE" | "PE"
        underlying_symbol=spec.underlying,
        strike_price=spec.strike,
        option_type=spec.option_type,
        expiry_date=spec.expiry,
        lot_size=spec.lot_size,
        contract_multiplier=1.0,
        margin_blocked=spec.notional,
        signal_confidence=confidence,
        pattern_name="FNO_OPTION_BUY",
        ai_reason=ai_reason or f"📥 BUY {label} | {spec.lots} lot(s) × {spec.lot_size} @ ₹{spec.premium}",
        news_sentiment_score=0.0,
        slippage_applied=0.0,
        opened_at=now,
    )
    session.add(trade)
    await session.flush()

    position = OpenPosition(
        symbol=spec.tradingsymbol,
        direction=TradeDirection.BUY,
        entry_price=spec.premium,
        current_price=spec.premium,
        stop_loss=spec.stop,
        take_profit=spec.target,
        size_units=spec.qty,
        size_usd=spec.notional,
        instrument_type=spec.option_type,
        underlying_symbol=spec.underlying,
        strike_price=spec.strike,
        option_type=spec.option_type,
        expiry_date=spec.expiry,
        lot_size=spec.lot_size,
        contract_multiplier=1.0,
        margin_blocked=spec.notional,
        unrealised_pnl=0.0,
        unrealised_pct=0.0,
        trade_id=trade.id,
        opened_at=now,
    )
    session.add(position)
    await session.flush()

    ok, msg = await VirtualWallet.deduct_margin(session, spec.notional, spec.tradingsymbol)
    if not ok:
        await session.execute(delete(OpenPosition).where(OpenPosition.id == position.id))
        await session.execute(delete(PaperTrade).where(PaperTrade.id == trade.id))
        await session.flush()
        logger.warning(f"[fno/exec] BLOCKED {label} — {msg} (need ₹{spec.notional:,.0f})")
        return None

    await session.commit()
    logger.info(
        f"[PAPER-FNO] BUY {label} | {spec.lots} lot(s) ({spec.qty} qty) @ ₹{spec.premium} "
        f"| SL ₹{spec.stop} TP ₹{spec.target} | debit ₹{spec.notional:,.0f} | {spec.dte}d to expiry"
    )

    # ── Telegram alert (F&O option buy) ───────────────────────────────────────
    try:
        if settings.telegram_available:
            from integrations.telegram_service import send
            be = (spec.strike + spec.premium) if spec.option_type == "CE" else (spec.strike - spec.premium)
            await send(
                f"🎯 <b>F&O OPTION BUY</b>\n"
                f"<b>{spec.underlying} {spec.strike:.0f} {spec.option_type}</b>\n"
                f"Premium: <b>₹{spec.premium}</b>  |  {spec.lots} lot × {spec.lot_size} = {spec.qty} qty\n"
                f"Expiry: {spec.expiry:%d-%b-%Y} ({spec.dte}d)\n"
                f"SL ₹{spec.stop}  ·  TP ₹{spec.target}  ·  Breakeven {be:,.0f}\n"
                f"Premium paid (max loss): ₹{spec.notional:,.0f}\n"
                f"Conviction: {confidence:.0f}%"
            )
    except Exception as exc:
        logger.debug(f"[fno/exec] telegram alert failed: {exc}")

    return trade


# ── Mark-to-market ───────────────────────────────────────────────────────────

async def _kite_ltp_nfo(tradingsymbol: str) -> float | None:
    """Live last-traded price for an NFO contract via Kite (real-time)."""
    try:
        from crawler.zerodha_client import get_kite_client
        kite = get_kite_client()
        if not kite.access_token:
            return None
        raw = await kite.get_ltp([f"NFO:{tradingsymbol}"])
        d = (raw or {}).get(f"NFO:{tradingsymbol}")
        if d and d.get("last_price", 0) > 0:
            return float(d["last_price"])
    except Exception:
        return None
    return None


async def current_option_premium(pos: OpenPosition, session: AsyncSession) -> float | None:
    """Current premium for an open option position.

    Order: WebSocket PRICE_CACHE → Kite REST LTP → latest snapshot → Black-Scholes.

    The symbol-based lookups (1, 2) only need pos.symbol — they must run even
    when underlying_symbol/strike_price/expiry_date are missing. Only the
    strike/expiry-dependent snapshot lookup (3) and Black-Scholes reprice (4)
    actually require those fields. A prior version returned None up front
    whenever expiry_date was empty, which silently blocked ALL pricing —
    including the cheap by-symbol lookups — for the position's entire life.
    Observed in production: two BANKNIFTY option positions with a null
    expiry_date never got a single price update in 4 days, so the eventual
    squareoff fell back to a stale current_price equal to the entry price,
    reporting a fake ₹0.00 P&L on what was actually a ~₹59,490 loss.
    """
    # 1. WebSocket live feed — fastest, no API call, already subscribed via zerodha_ticker.
    try:
        from crawler.live_prices import PRICE_CACHE
        cached = PRICE_CACHE.get(pos.symbol) or PRICE_CACHE.get(pos.symbol.replace(".NS", ""))
        if cached and cached.get("price", 0) > 0:
            return float(cached["price"])
    except Exception:
        pass
    # 2. Live Kite REST LTP for the exact contract.
    live = await _kite_ltp_nfo(pos.symbol)
    if live is not None:
        return live

    if not pos.underlying_symbol or not pos.strike_price or not pos.expiry_date:
        return None

    prem = await _latest_premium(
        pos.underlying_symbol, pos.strike_price, pos.option_type, pos.expiry_date, session
    )
    if prem is not None:
        return prem

    # Fallback: reprice from latest snapshot spot + IV at this strike.
    snap = (await session.execute(
        select(OptionContractSnapshot)
        .where(
            OptionContractSnapshot.underlying == pos.underlying_symbol,
            OptionContractSnapshot.strike == pos.strike_price,
            OptionContractSnapshot.option_type == pos.option_type,
        )
        .order_by(OptionContractSnapshot.snapshot_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if snap is None or not snap.iv or not snap.spot:
        return None
    dte = (pos.expiry_date - date.today()).days
    T = _bs.years_to_expiry(dte)
    flag = "c" if pos.option_type == "CE" else "p"
    return round(_bs.bs_price(snap.spot, pos.strike_price, T, settings.RISK_FREE_RATE, snap.iv, flag), 2)


def option_pnl(pos: OpenPosition, cur_premium: float) -> tuple[float, float]:
    """Unrealised P&L for an option position (long or short). Returns (pnl, pct)."""
    from db.models import TradeDirection
    if pos.direction == TradeDirection.SELL:
        # Short option: seller profits when premium falls
        pnl = (pos.entry_price - cur_premium) * pos.size_units
    else:
        # Long option: buyer profits when premium rises
        pnl = (cur_premium - pos.entry_price) * pos.size_units
    cost_basis = pos.entry_price * pos.size_units
    pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0.0
    return round(pnl, 2), round(pct, 2)


# ── Index directional signal + evaluation loop ───────────────────────────────

# Underlying → index candle symbol (yfinance).
_INDEX_CANDLE = {
    "NIFTY":     "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "FINNIFTY":  "^NSEBANK",   # proxy — FINNIFTY is dominated by financial/bank stocks
}


def _index_signal(closes: list[float]) -> tuple[str, float, float] | None:
    """Lightweight EMA-trend + momentum signal on an index close series.

    Returns (direction 'BUY'/'SELL', confidence 0..90, spot) or None if NEUTRAL.
    """
    if len(closes) < 25:
        return None
    spot = closes[-1]
    # EMA20 via simple recursive smoothing.
    k = 2 / (20 + 1)
    ema = closes[0]
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
    trend = (spot - ema) / ema if ema else 0.0
    mom   = (spot - closes[-5]) / closes[-5] if closes[-5] else 0.0
    if trend > 0 and mom > 0:
        direction = "BUY"
    elif trend < 0 and mom < 0:
        direction = "SELL"
    else:
        return None
    confidence = min(90.0, (abs(trend) * 1500) + (abs(mom) * 1000))
    return direction, round(confidence, 1), spot


async def composite_index_signal(underlying: str, session: AsyncSession) -> dict | None:
    """Multi-factor directional signal for an index — uses ALL available data.

    Blends, into a single score in [-100, +100] (sign = direction, |.| = conviction):
      • Price trend + momentum (index 1d candles)        weight 35
      • Options positioning (PCR contrarian)             weight 15
      • Max-Pain gravity (spot vs max-pain)              weight 10
      • Macro flows (FII/DII net)                        weight 15
      • Market breadth (advances vs declines)            weight 10
      • News mood (recent sentiment)                     weight 10
      • Volatility regime (India VIX) — risk scaler      weight  5
    IV-Rank is attached for sizing/strategy (buy options only when not over-rich).

    Returns dict {direction, confidence, score, spot, factors[]} or None.
    """
    from crawler.price_feed import get_latest_candles
    from db.models import NewsItem, FIIDIIFlow, IVHistory
    from sqlalchemy import desc as _desc

    csym = _INDEX_CANDLE.get(underlying.upper())
    candles = await get_latest_candles(csym, "1d", 60, session) if csym else []
    if not candles or len(candles) < 25:
        return None
    closes = [float(c.close) for c in reversed(candles)]
    candle_spot = closes[-1]
    factors: list[dict] = []
    score = 0.0

    # 1. Price trend + momentum (35) — computed from proxy candle closes (normalised %)
    k = 2 / 21
    ema = closes[0]
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
    trend = (candle_spot - ema) / ema if ema else 0.0
    mom = (candle_spot - closes[-5]) / closes[-5] if closes[-5] else 0.0
    price_s = max(-1, min(1, trend * 40 + mom * 30)) * 35
    score += price_s
    factors.append({"factor": "price_trend", "score": round(price_s, 1),
                    "detail": f"trend {trend*100:+.1f}% mom {mom*100:+.1f}%"})

    # Latest per-strike snapshot batch → PCR, Max-Pain, IV.
    last_at = (await session.execute(
        select(sqlfunc.max(OptionContractSnapshot.snapshot_at))
        .where(OptionContractSnapshot.underlying == underlying.upper())
    )).scalar()
    rows = []
    if last_at is not None:
        rows = (await session.execute(
            select(OptionContractSnapshot).where(
                OptionContractSnapshot.underlying == underlying.upper(),
                OptionContractSnapshot.snapshot_at == last_at,
            )
        )).scalars().all()

    # Actual underlying spot — from snapshot (correct even for FINNIFTY with proxy candle).
    spot = float(rows[0].spot) if rows and rows[0].spot else candle_spot

    # 2. PCR contrarian (15)
    call_oi = sum(r.oi for r in rows if r.option_type == "CE")
    put_oi  = sum(r.oi for r in rows if r.option_type == "PE")
    pcr = round(put_oi / call_oi, 2) if call_oi else None
    if pcr is not None:
        pcr_s = max(-1, min(1, (pcr - 1.0) / 0.4)) * 15   # high PCR = contrarian bullish
        score += pcr_s
        factors.append({"factor": "pcr", "score": round(pcr_s, 1), "detail": f"PCR {pcr}"})

    # 3. Max-Pain gravity (10)
    max_pain = None
    strikes = sorted({r.strike for r in rows})
    if strikes:
        max_pain = min(strikes, key=lambda E: (
            sum(max(E - r.strike, 0) * r.oi for r in rows if r.option_type == "CE") +
            sum(max(r.strike - E, 0) * r.oi for r in rows if r.option_type == "PE")))
        dev = (max_pain - spot) / spot if spot else 0.0   # spot below max-pain → bullish pull
        mp_s = max(-1, min(1, dev * 50)) * 10
        score += mp_s
        factors.append({"factor": "max_pain", "score": round(mp_s, 1),
                        "detail": f"max_pain {max_pain:.0f} vs spot {spot:.0f}"})

    # 4. Macro flows — FII/DII net (15)
    fd = (await session.execute(select(FIIDIIFlow).order_by(_desc(FIIDIIFlow.date)).limit(1))).scalar_one_or_none()
    if fd:
        net = (fd.fii_net_buy or 0) + (fd.dii_net_buy or 0)
        flow_s = max(-1, min(1, net / 5000.0)) * 15       # ±₹5000 Cr saturates
        score += flow_s
        factors.append({"factor": "fii_dii", "score": round(flow_s, 1),
                        "detail": f"FII {fd.fii_net_buy:+.0f} DII {fd.dii_net_buy:+.0f} Cr"})

    # 5. Market breadth (10)
    try:
        from crawler.market_breadth import get_breadth_cache
        nse = (get_breadth_cache() or {}).get("nse", {})
        adv, dec = nse.get("advances") or 0, nse.get("declines") or 0
        if adv + dec > 0:
            br_s = max(-1, min(1, (adv - dec) / (adv + dec))) * 10
            score += br_s
            factors.append({"factor": "breadth", "score": round(br_s, 1), "detail": f"{adv}adv/{dec}dec"})
    except Exception:
        pass

    # 6. News mood (10) — amplified when a real narrative-engine catalyst backs it
    news = (await session.execute(
        select(NewsItem.score).order_by(_desc(NewsItem.crawled_at)).limit(20)
    )).scalars().all()
    if news:
        avg = sum(float(x or 0) for x in news) / len(news)
        news_s = max(-1, min(1, avg * 2)) * 10

        # Narrative boost cache — same "Eagle Eyes style" RSS+Telegram-derived
        # sector-heat signal used for equity scoring. BANKNIFTY maps directly
        # to the Banking sector; NIFTY/FINNIFTY use the strongest currently-hot
        # sector as a market-wide narrative-heat proxy. Only strengthens an
        # already-positive reading (a hot narrative shouldn't invent a signal
        # that isn't there) — this is what lets a genuine catalyst day (e.g.
        # an MoU, a PLI announcement) actually push the index-option
        # confidence score higher instead of the news factor staying muted at
        # raw sentiment-average noise levels.
        try:
            from engine.narrative_engine import NARRATIVE_BOOST_CACHE, get_narrative_boost
            if underlying.upper() == "BANKNIFTY":
                boost = get_narrative_boost("Banking")
            else:
                boost = max((d.get("boost", 0) for d in NARRATIVE_BOOST_CACHE.values()), default=0.0)
            if boost > 0 and news_s > 0:
                news_s = min(10.0, news_s + boost / 40.0 * 4.0)  # up to +4 extra at boost=40
        except Exception:
            pass

        score += news_s
        factors.append({"factor": "news", "score": round(news_s, 1), "detail": f"avg sentiment {avg:+.2f}"})

    # 7. Volatility regime — India VIX scales conviction (5) + risk note
    vix = None
    try:
        from crawler.india_price_feed import fetch_india_vix
        import asyncio as _aio
        vix = await _aio.get_event_loop().run_in_executor(None, fetch_india_vix)
        vix = float(vix) if vix else None
    except Exception:
        pass
    if vix is not None:
        # High VIX = risk-off → dampen score slightly toward neutral.
        damp = 1.0 if vix < 18 else 0.8 if vix < 24 else 0.6
        score *= damp
        factors.append({"factor": "vix", "score": 0.0, "detail": f"VIX {vix:.1f} (×{damp} conviction)"})

    # IV-Rank for sizing/strategy.
    hist = (await session.execute(
        select(IVHistory.atm_iv).where(IVHistory.underlying == underlying.upper())
    )).scalars().all()
    iv_rank = None
    if len(hist) >= 5:
        lo, hi = min(hist), max(hist)
        iv_rank = round(100 * (hist[-1] - lo) / (hi - lo), 1) if hi > lo else 50.0

    direction = "BUY" if score >= 12 else "SELL" if score <= -12 else "NEUTRAL"
    return {
        "direction": direction,
        "confidence": round(min(90.0, abs(score)), 1),
        "score": round(score, 1),
        "spot": round(spot, 2),
        "pcr": pcr, "max_pain": max_pain, "iv_rank": iv_rank, "vix": vix,
        "factors": factors,
    }


async def evaluate_index_options(session: AsyncSession, equity: float) -> list[dict]:
    """Evaluate each index for a directional option SPREAD and open paper positions.

    Additive to the equity agent — only runs when ENABLE_OPTIONS is set. Skips an
    underlying if a position on it is already open (by tradingsymbol prefix).
    Returns a list of opened-trade summaries.
    """
    if not (settings.ENABLE_FNO and settings.ENABLE_OPTIONS):
        return []

    # Pre-market / post-market guard: never open F&O outside NSE hours.
    from crawler.india_price_feed import is_nse_market_open
    if not is_nse_market_open():
        logger.info("[fno/evaluate] NSE closed — skipping F&O evaluation")
        return []

    # Regime gate: fail-CLOSED — unknown/error = treat as WEAK_BEAR, not pass-through.
    try:
        from engine.agent.market_regime import get_market_regime
        regime_result = await get_market_regime(session)
        regime = regime_result.state.upper()
    except Exception as _re:
        logger.warning(f"[fno/evaluate] regime check failed: {_re} — blocking (fail-closed)")
        regime = "WEAK_BEAR"

    # Already-open option underlyings (avoid stacking).
    open_unders = set((await session.execute(
        select(OpenPosition.underlying_symbol).where(OpenPosition.underlying_symbol != None)
    )).scalars().all())

    # F&O options require higher conviction than equity signals.
    # Use FNO_CONFIDENCE_THRESHOLD if set, else fall back to 55 (not the equity default of 30).
    fno_threshold = float(getattr(settings, "FNO_CONFIDENCE_THRESHOLD", None) or 55.0)
    opened: list[dict] = []

    for under in settings.fno_index_symbols:
        try:
            if under in open_unders:
                continue
            # Multi-factor decision: price + PCR + max-pain + FII/DII + breadth + news + VIX.
            sig = await composite_index_signal(under, session)
            if sig is None or sig["direction"] == "NEUTRAL":
                continue
            direction, confidence, spot = sig["direction"], sig["confidence"], sig["spot"]
            if confidence < fno_threshold:
                logger.info(f"[fno/evaluate] {under} conf {confidence:.1f} < fno_threshold {fno_threshold} — skipping")
                continue

            # Regime alignment gate: don't open spreads that fight the market regime.
            if direction == "BUY" and regime in ("WEAK_BEAR", "STRONG_BEAR"):
                logger.info(f"[fno/evaluate] {under} BULL CALL SPREAD blocked — regime={regime}")
                continue
            if direction == "SELL" and regime in ("MODERATE_BULL", "STRONG_BULL"):
                logger.info(f"[fno/evaluate] {under} BEAR PUT SPREAD blocked — regime={regime}")
                continue

            spec = await select_index_spread(under, direction, spot, equity, session)
            if spec is None:
                continue
            # Carry the factor rationale into the trade note.
            rationale = "; ".join(f"{f['factor']}={f['score']:+.0f}" for f in sig["factors"])
            spread_name = "BULL CALL SPREAD" if spec.option_type == "CE" else "BEAR PUT SPREAD"
            trades = await open_spread_paper_trade(
                spec, session, confidence=confidence,
                ai_reason=f"📊 {spread_name} {spec.underlying} | score {sig['score']:+.0f} | {rationale}",
            )
            if trades:
                opened.append({
                    "underlying": under, "direction": direction,
                    "tradingsymbol": spec.tradingsymbol_buy, "lots": spec.lots,
                    "premium": spec.premium_buy, "confidence": confidence,
                    "score": sig["score"], "factors": sig["factors"],
                })
        except Exception as exc:
            logger.warning(f"[fno/evaluate] {under} failed: {exc}")

    return opened


async def fno_signal_preview(underlying: str, session: AsyncSession) -> dict | None:
    """Compute the F&O directional signal + suggested option for one index,
    WITHOUT executing. Powers the Signals & Predictions UI panel.

    Combines: price trend/momentum (direction), PCR + Max-Pain (positioning),
    IV-Rank (vol regime) → a plain recommendation + the option the agent would buy.
    """
    from crawler.price_feed import get_latest_candles

    csym = _INDEX_CANDLE.get(underlying.upper())
    candles = await get_latest_candles(csym, "1d", 60, session) if csym else []
    closes = [float(c.close) for c in reversed(candles)] if candles else []
    sig = _index_signal(closes) if len(closes) >= 25 else None

    # Latest per-strike snapshot batch → PCR, Max-Pain, spot, ATM IV.
    last_at = (await session.execute(
        select(sqlfunc.max(OptionContractSnapshot.snapshot_at))
        .where(OptionContractSnapshot.underlying == underlying.upper())
    )).scalar()
    rows = []
    if last_at is not None:
        rows = (await session.execute(
            select(OptionContractSnapshot).where(
                OptionContractSnapshot.underlying == underlying.upper(),
                OptionContractSnapshot.snapshot_at == last_at,
            )
        )).scalars().all()

    spot = rows[0].spot if rows else (closes[-1] if closes else 0.0)
    call_oi = sum(r.oi for r in rows if r.option_type == "CE")
    put_oi  = sum(r.oi for r in rows if r.option_type == "PE")
    pcr = round(put_oi / call_oi, 2) if call_oi else None

    # Max-Pain: strike minimising total intrinsic payout.
    max_pain = None
    strikes = sorted({r.strike for r in rows})
    if strikes:
        def payout(E):
            return sum(max(E - r.strike, 0) * r.oi for r in rows if r.option_type == "CE") + \
                   sum(max(r.strike - E, 0) * r.oi for r in rows if r.option_type == "PE")
        max_pain = min(strikes, key=payout)

    # IV-Rank from history.
    from db.models import IVHistory
    hist = (await session.execute(
        select(IVHistory.atm_iv).where(IVHistory.underlying == underlying.upper())
    )).scalars().all()
    atm_iv = hist[-1] if hist else None
    iv_rank = None
    if len(hist) >= 5 and atm_iv:
        lo, hi = min(hist), max(hist)
        iv_rank = round(100 * (atm_iv - lo) / (hi - lo), 1) if hi > lo else 50.0

    # Direction: full multi-factor composite (price + PCR + max-pain + FII/DII +
    # breadth + news + VIX), the same signal the agent trades on.
    comp = await composite_index_signal(underlying, session)
    direction = comp["direction"] if comp else (sig[0] if sig else "NEUTRAL")
    confidence = comp["confidence"] if comp else (sig[1] if sig else 0.0)
    comp_factors = comp["factors"] if comp else []
    comp_score = comp["score"] if comp else 0.0
    pcr_bias = None
    if pcr is not None:
        pcr_bias = "BULLISH" if pcr >= 1.3 else "BEARISH" if pcr <= 0.7 else "NEUTRAL"

    # Suggested option (no execution).
    suggestion = None
    if direction in ("BUY", "SELL"):
        opt_type = "CE" if direction == "BUY" else "PE"
        atm = min(strikes, key=lambda k: abs(k - spot)) if strikes else None
        prem = None
        if atm is not None:
            prem = next((r.ltp for r in rows if r.strike == atm and r.option_type == opt_type), None)
        suggestion = {
            "action": f"BUY {opt_type}", "option_type": opt_type,
            "strike": atm, "premium": prem,
            "stop": round(prem * 0.5, 2) if prem else None,
            "target": round(prem * 2.0, 2) if prem else None,
        }

    # Plain-English recommendation.
    if direction == "BUY":
        rec = f"Bullish — buy a {suggestion['strike']:.0f} Call. Trend up"
    elif direction == "SELL":
        rec = f"Bearish — buy a {suggestion['strike']:.0f} Put. Trend down"
    else:
        rec = "No clear directional edge — stay flat or trade volatility."
    if iv_rank is not None:
        rec += f" · IV-Rank {iv_rank:.0f} ({'cheap' if iv_rank < 30 else 'rich' if iv_rank > 70 else 'fair'})"

    return {
        "underlying": underlying.upper(),
        "spot": round(spot, 2) if spot else None,
        "direction": direction,
        "confidence": round(confidence, 1),
        "composite_score": comp_score,
        "factors": comp_factors,
        "pcr": pcr, "pcr_bias": pcr_bias,
        "max_pain": max_pain,
        "atm_iv": round(atm_iv, 4) if atm_iv else None,
        "iv_rank": iv_rank,
        "suggestion": suggestion,
        "recommendation": rec,
    }


async def evaluate_portfolio_hedge(session: AsyncSession, equity: float) -> dict | None:
    """Buy a protective index PUT when the market turns bearish, sized to the
    open EQUITY book's exposure (not a directional bet).

    Trigger: NIFTY index signal is bearish AND open equity notional is material
    AND no hedge PUT is already open. Gated by FNO_HEDGE_ENABLED.
    """
    if not (settings.ENABLE_FNO and settings.ENABLE_OPTIONS and settings.FNO_HEDGE_ENABLED):
        return None

    from crawler.price_feed import get_latest_candles

    # Already holding a NIFTY PUT (directional or hedge)? Don't stack.
    existing = (await session.execute(
        select(OpenPosition.id).where(
            OpenPosition.instrument_type == "PE",
            OpenPosition.underlying_symbol == "NIFTY",
        )
    )).first()
    if existing:
        return None

    # Open equity notional (cash positions only).
    eq_notional = (await session.execute(
        select(sqlfunc.coalesce(sqlfunc.sum(OpenPosition.size_usd), 0.0))
        .where(OpenPosition.instrument_type == "EQUITY")
    )).scalar() or 0.0
    if eq_notional < equity * 0.10:        # nothing material to hedge
        return None

    candles = await get_latest_candles("^NSEI", "1d", 60, session)
    if not candles or len(candles) < 25:
        return None
    closes = [float(c.close) for c in reversed(candles)]
    sig = _index_signal(closes)
    if sig is None or sig[0] != "SELL":     # only hedge when bearish
        return None
    spot = sig[2]

    contract = await _contracts.resolve_option("NIFTY", "PE", spot, session)
    if contract is None:
        return None
    premium = await _latest_premium("NIFTY", contract.strike, "PE", contract.expiry, session)
    if not premium or premium <= 0:
        return None

    # Lots to cover FNO_HEDGE_RATIO of equity notional.
    hedge_notional = eq_notional * settings.FNO_HEDGE_RATIO
    lots = max(1, min(settings.FNO_MAX_LOTS_PER_TRADE,
                      int(hedge_notional // (spot * contract.lot_size))))
    qty = lots * contract.lot_size
    debit = round(qty * premium, 2)

    spec = OptionTradeSpec(
        underlying="NIFTY", tradingsymbol=contract.tradingsymbol, option_type="PE",
        strike=contract.strike, expiry=contract.expiry, lot_size=contract.lot_size,
        premium=round(premium, 2), lots=lots, qty=qty, notional=debit,
        stop=0.0, target=0.0, dte=contract.dte,     # hedge has no fixed SL/TP
    )
    trade = await open_option_paper_trade(
        spec, session, confidence=sig[1],
        ai_reason=f"🛡️ HEDGE: NIFTY {contract.strike:.0f}PE | {lots} lot(s) protecting "
                  f"₹{hedge_notional:,.0f} of equity exposure",
    )
    if trade is None:
        return None
    # Tag as a hedge for idempotency.
    trade.pattern_name = "FNO_HEDGE_PUT"
    await session.commit()
    logger.info(f"[fno/hedge] bought NIFTY {contract.strike:.0f}PE × {lots} lot(s) | debit ₹{debit:,.0f}")
    return {"tradingsymbol": spec.tradingsymbol, "lots": lots, "debit": debit}


# ── Spread Net P&L Monitor (SL/TP) ───────────────────────────────────────────

# Fraction of max profit to book at (50% → take half the max gain)
_SPREAD_TP_FRACTION = 0.50
# Fraction of net debit to cut at (80% → tolerate 80% loss of premium paid)
_SPREAD_SL_FRACTION = 0.80


async def monitor_spread_exits(session: AsyncSession) -> list[dict]:
    """Check all open spreads for net P&L SL/TP and close qualifying pairs.

    Groups BUY + SELL legs of the same underlying + option_type as a spread pair.
    For each pair:
      - Computes net P&L from current market premiums (same cascade as MTM)
      - Books profit when net P&L ≥ 50% of max profit
      - Cuts loss when net P&L ≤ −80% of net premium debit paid
    Closes both legs atomically and returns margin to the virtual wallet.
    """
    from paper_trading.virtual_wallet import VirtualWallet
    from db.models import TradeStatus

    opt_positions = (await session.execute(
        select(OpenPosition).where(
            OpenPosition.instrument_type.in_(["CE", "PE"]),
        )
    )).scalars().all()

    # Group by (underlying, option_type) → expect one BUY + one SELL per pair
    pairs: dict[tuple, dict] = {}
    for pos in opt_positions:
        key = (pos.underlying_symbol, pos.option_type)
        bucket = pairs.setdefault(key, {"buy": None, "sell": None})
        from db.models import TradeDirection
        if pos.direction == TradeDirection.BUY:
            bucket["buy"] = pos
        elif pos.direction == TradeDirection.SELL:
            bucket["sell"] = pos

    closed: list[dict] = []

    for (underlying, opt_type), bucket in pairs.items():
        buy_pos  = bucket["buy"]
        sell_pos = bucket["sell"]
        if buy_pos is None or sell_pos is None:
            continue  # lone leg (naked option) — skip spread monitor

        # Current premiums via same 4-tier cascade
        cur_buy  = await current_option_premium(buy_pos,  session)
        cur_sell = await current_option_premium(sell_pos, session)
        if cur_buy is None or cur_sell is None:
            continue

        # Net P&L: buy leg gain/loss + sell leg gain/loss (seller profits when premium falls)
        pnl_buy  = (cur_buy  - buy_pos.entry_price)  *  buy_pos.size_units
        pnl_sell = (sell_pos.entry_price - cur_sell)  * sell_pos.size_units
        net_pnl  = round(pnl_buy + pnl_sell, 2)

        # Spread geometry
        net_debit_per_unit = buy_pos.entry_price - sell_pos.entry_price
        if net_debit_per_unit <= 0:
            continue  # credit spread — different logic, skip
        spread_width = abs(
            float(buy_pos.strike_price or 0) - float(sell_pos.strike_price or 0)
        )
        max_profit_per_unit = spread_width - net_debit_per_unit
        max_loss_total = round(net_debit_per_unit * buy_pos.size_units, 2)
        max_profit_total = round(max(0.0, max_profit_per_unit) * buy_pos.size_units, 2)

        tp_trigger = max_profit_total * _SPREAD_TP_FRACTION
        sl_trigger = -max_loss_total  * _SPREAD_SL_FRACTION

        reason = None
        if max_profit_total > 0 and net_pnl >= tp_trigger:
            reason = f"TARGET +₹{net_pnl:,.0f} ≥ {_SPREAD_TP_FRACTION*100:.0f}% of max ₹{max_profit_total:,.0f}"
        elif net_pnl <= sl_trigger:
            reason = f"STOP −₹{abs(net_pnl):,.0f} ≥ {_SPREAD_SL_FRACTION*100:.0f}% loss of ₹{max_loss_total:,.0f}"

        if reason is None:
            continue

        # Close both legs
        now = datetime.utcnow()
        margin_to_return = float(buy_pos.margin_blocked or 0.0)

        for pos, cur_prem, leg_pnl in [
            (buy_pos,  cur_buy,  pnl_buy),
            (sell_pos, cur_sell, pnl_sell),
        ]:
            trade = (await session.execute(
                select(PaperTrade).where(PaperTrade.id == pos.trade_id)
            )).scalar_one_or_none()
            if trade:
                trade.exit_price  = round(cur_prem, 2)
                trade.closed_at   = now
                trade.status      = TradeStatus.CLOSED
                trade.pnl         = round(leg_pnl, 2)
                trade.pnl_percent = round(leg_pnl / max(pos.margin_blocked or 1, 1) * 100, 2)
            await session.execute(delete(OpenPosition).where(OpenPosition.id == pos.id))

        await VirtualWallet.return_margin(session, margin_to_return, net_pnl, f"SPREAD_{underlying}")
        await session.commit()

        logger.info(
            f"[fno/spread-exit] {underlying} {opt_type} CLOSED | {reason} | "
            f"net_pnl ₹{net_pnl:,.0f} | margin returned ₹{margin_to_return:,.0f}"
        )

        try:
            if settings.telegram_available:
                from integrations.telegram_service import send
                emoji = "✅" if net_pnl >= 0 else "🛑"
                await send(
                    f"{emoji} <b>SPREAD EXIT — {underlying} {opt_type}</b>\n"
                    f"Reason: {reason}\n"
                    f"BUY  leg: ₹{buy_pos.entry_price} → ₹{cur_buy:.1f}  ({pnl_buy:+,.0f})\n"
                    f"SELL leg: ₹{sell_pos.entry_price} → ₹{cur_sell:.1f}  ({pnl_sell:+,.0f})\n"
                    f"<b>Net P&L: ₹{net_pnl:+,.0f}</b>"
                )
        except Exception:
            pass

        closed.append({
            "underlying": underlying, "option_type": opt_type,
            "net_pnl": net_pnl, "reason": reason,
        })

    return closed


# ── Spread Execution Logic ───────────────────────────────────────────────────

@dataclass
class SpreadTradeSpec:
    underlying:    str
    option_type:   str       # CE | PE
    expiry:        date
    lot_size:      int
    strike_buy:    float
    strike_sell:   float
    premium_buy:   float
    premium_sell:  float
    tradingsymbol_buy: str
    tradingsymbol_sell: str
    lots:          int
    qty:           int
    net_premium:   float
    margin_blocked: float
    dte:           int

def get_spread_width(underlying: str) -> float:
    under = underlying.upper()
    if "BANK" in under or "SENSEX" in under:
        return 500.0
    return 200.0


def _spread_margin_approx(premium_buy: float, qty: int, spot: float, lot_size: int, lots: int) -> float:
    """Realistic margin for a Bull/Bear spread matching NSE/broker methodology.

    Brokers charge:
      • BUY leg: full premium paid upfront
      • SELL leg SPAN (with spread hedge): ~2% of underlying notional
        (empirically verified against Groww/Zerodha for BANKNIFTY spreads)
    """
    buy_cost = premium_buy * qty
    sell_span = spot * lot_size * lots * 0.02
    return round(buy_cost + sell_span, 2)


async def _kite_spread_margin(sym_buy: str, sym_sell: str, qty: int) -> float:
    """Get exact spread margin from Zerodha basket_order_margins API.

    Returns 0.0 if Kite is not connected or the call fails.
    """
    import asyncio as _asyncio
    try:
        from crawler.zerodha_ticker import CONNECTED
        if not CONNECTED:
            return 0.0
        from crawler.zerodha_kite_lib import get_basket_margins
        orders = [
            {"exchange": "NFO", "tradingsymbol": sym_buy, "transaction_type": "BUY",
             "variety": "regular", "product": "NRML", "order_type": "MARKET", "quantity": int(qty)},
            {"exchange": "NFO", "tradingsymbol": sym_sell, "transaction_type": "SELL",
             "variety": "regular", "product": "NRML", "order_type": "MARKET", "quantity": int(qty)},
        ]
        basket = await _asyncio.to_thread(get_basket_margins, orders, False)
        total = float((basket.get("initial") or {}).get("total", 0))
        return total if total > 0 else 0.0
    except Exception:
        return 0.0

async def select_index_spread(
    underlying: str,
    direction: str,
    spot: float,
    equity: float,
    session: AsyncSession,
) -> SpreadTradeSpec | None:
    """Resolve a directional signal to a Spread (Bull Call / Bear Put) + lot-rounded size."""
    option_type = "CE" if direction.upper() == "BUY" else "PE"
    
    contract_buy = await _contracts.resolve_option(underlying, option_type, spot, session)
    if contract_buy is None:
        contract_buy = await _contracts.resolve_option_from_snapshot(underlying, option_type, spot, session)
    if contract_buy is None:
        return None

    premium_buy = await _latest_premium(underlying, contract_buy.strike, option_type, contract_buy.expiry, session)
    if not premium_buy or premium_buy <= 0:
        return None

    width = get_spread_width(underlying)
    strike_sell = contract_buy.strike + width if option_type == "CE" else contract_buy.strike - width
    
    contract_sell = await _contracts.resolve_option(underlying, option_type, strike_sell, session)
    if contract_sell is None:
        contract_sell = await _contracts.resolve_option_from_snapshot(underlying, option_type, strike_sell, session)
    if contract_sell is None or contract_sell.expiry != contract_buy.expiry:
        return None

    premium_sell = await _latest_premium(underlying, contract_sell.strike, option_type, contract_sell.expiry, session)
    if not premium_sell or premium_sell <= 0:
        return None

    net_premium = premium_buy - premium_sell
    if net_premium <= 0:
        return None

    lot_size = contract_buy.lot_size or 1

    risk_budget = equity * settings.AGENT_MAX_RISK_PER_TRADE
    risk_per_lot = net_premium * lot_size
    lots = int(risk_budget // risk_per_lot) if risk_per_lot > 0 else 0
    lots = max(1, min(lots, settings.FNO_MAX_LOTS_PER_TRADE))
    qty = lots * lot_size

    # Realistic margin: BUY premium + SELL SPAN with hedge offset (~2% of notional)
    margin_blocked = _spread_margin_approx(premium_buy, qty, spot, lot_size, lots)

    # If Zerodha is connected, use its exact basket margin (most accurate)
    kite_margin = await _kite_spread_margin(contract_buy.tradingsymbol, contract_sell.tradingsymbol, qty)
    if kite_margin > 0:
        margin_blocked = kite_margin

    if margin_blocked > equity:
        margin_per_lot = _spread_margin_approx(premium_buy, lot_size, spot, lot_size, 1)
        lots = max(1, int(equity // margin_per_lot))
        qty = lots * lot_size
        margin_blocked = _spread_margin_approx(premium_buy, qty, spot, lot_size, lots)
        kite_margin = await _kite_spread_margin(contract_buy.tradingsymbol, contract_sell.tradingsymbol, qty)
        if kite_margin > 0:
            margin_blocked = kite_margin

    return SpreadTradeSpec(
        underlying=underlying.upper(), option_type=option_type, expiry=contract_buy.expiry,
        lot_size=lot_size, strike_buy=contract_buy.strike, strike_sell=contract_sell.strike,
        premium_buy=round(premium_buy, 2), premium_sell=round(premium_sell, 2),
        tradingsymbol_buy=contract_buy.tradingsymbol, tradingsymbol_sell=contract_sell.tradingsymbol,
        lots=lots, qty=qty, net_premium=round(net_premium, 2), margin_blocked=margin_blocked,
        dte=contract_buy.dte
    )

async def open_spread_paper_trade(
    spec: SpreadTradeSpec, session: AsyncSession, *, confidence: float = 0.0, ai_reason: str = "",
) -> list[PaperTrade]:
    """Open an F&O option spread position (Buy leg + Sell leg)."""
    from paper_trading.virtual_wallet import VirtualWallet

    _max = settings.AGENT_EQUITY * settings.AGENT_MAX_POSITION_WEIGHT
    if spec.margin_blocked > _max * 1.10:
        logger.error(f"[fno/spread] HARD GUARD: margin {spec.margin_blocked} > max {_max}")
        return []

    existing = (await session.execute(
        select(OpenPosition.symbol).where(
            OpenPosition.underlying_symbol == spec.underlying,
            OpenPosition.option_type == spec.option_type,
        )
    )).scalars().all()
    if existing:
        logger.warning(f"[fno/spread] BLOCKED {spec.underlying} — already have positions")
        return []

    now = datetime.utcnow()
    spread_name = "BULL CALL SPREAD" if spec.option_type == "CE" else "BEAR PUT SPREAD"
    label = f"{spec.underlying} {spread_name} {spec.expiry:%d-%b}"
    ai_reason = ai_reason or f"📊 {spread_name} | {spec.lots} lot(s) | Net Debit ₹{spec.net_premium}"

    trades = []
    positions = []

    trade_buy = PaperTrade(
        symbol=spec.tradingsymbol_buy, direction=TradeDirection.BUY, status=TradeStatus.OPEN,
        entry_price=spec.premium_buy, stop_loss=0, take_profit=0, size_units=spec.qty,
        size_usd=spec.premium_buy * spec.qty, instrument_type=spec.option_type,
        underlying_symbol=spec.underlying, strike_price=spec.strike_buy, option_type=spec.option_type,
        expiry_date=spec.expiry, lot_size=spec.lot_size, contract_multiplier=1.0,
        margin_blocked=spec.margin_blocked, signal_confidence=confidence,
        pattern_name=f"FNO_{spread_name.replace(' ', '_')}", ai_reason=ai_reason, opened_at=now
    )
    session.add(trade_buy)
    await session.flush()
    trades.append(trade_buy)

    pos_buy = OpenPosition(
        symbol=spec.tradingsymbol_buy, direction=TradeDirection.BUY, entry_price=spec.premium_buy,
        current_price=spec.premium_buy, stop_loss=0, take_profit=0, size_units=spec.qty,
        size_usd=spec.premium_buy * spec.qty, instrument_type=spec.option_type,
        underlying_symbol=spec.underlying, strike_price=spec.strike_buy, option_type=spec.option_type,
        expiry_date=spec.expiry, lot_size=spec.lot_size, contract_multiplier=1.0,
        margin_blocked=spec.margin_blocked, unrealised_pnl=0.0, unrealised_pct=0.0,
        trade_id=trade_buy.id, opened_at=now
    )
    session.add(pos_buy)
    positions.append(pos_buy)

    trade_sell = PaperTrade(
        symbol=spec.tradingsymbol_sell, direction=TradeDirection.SELL, status=TradeStatus.OPEN,
        entry_price=spec.premium_sell, stop_loss=0, take_profit=0, size_units=spec.qty,
        size_usd=spec.premium_sell * spec.qty, instrument_type=spec.option_type,
        underlying_symbol=spec.underlying, strike_price=spec.strike_sell, option_type=spec.option_type,
        expiry_date=spec.expiry, lot_size=spec.lot_size, contract_multiplier=1.0, margin_blocked=0,
        signal_confidence=confidence, pattern_name=f"FNO_{spread_name.replace(' ', '_')}",
        ai_reason=ai_reason, opened_at=now
    )
    session.add(trade_sell)
    await session.flush()
    trades.append(trade_sell)

    pos_sell = OpenPosition(
        symbol=spec.tradingsymbol_sell, direction=TradeDirection.SELL, entry_price=spec.premium_sell,
        current_price=spec.premium_sell, stop_loss=0, take_profit=0, size_units=spec.qty,
        size_usd=spec.premium_sell * spec.qty, instrument_type=spec.option_type,
        underlying_symbol=spec.underlying, strike_price=spec.strike_sell, option_type=spec.option_type,
        expiry_date=spec.expiry, lot_size=spec.lot_size, contract_multiplier=1.0,
        margin_blocked=0, unrealised_pnl=0.0, unrealised_pct=0.0, trade_id=trade_sell.id, opened_at=now
    )
    session.add(pos_sell)
    positions.append(pos_sell)

    await session.flush()

    ok, msg = await VirtualWallet.deduct_margin(session, spec.margin_blocked, f"SPREAD_{spec.underlying}")
    if not ok:
        for p in positions: await session.execute(delete(OpenPosition).where(OpenPosition.id == p.id))
        for t in trades: await session.execute(delete(PaperTrade).where(PaperTrade.id == t.id))
        await session.flush()
        logger.warning(f"[fno/exec] BLOCKED {label} — {msg}")
        return []

    await session.commit()
    logger.info(f"[PAPER-FNO] {spread_name} {label} | {spec.lots} lot(s) | Net Debit ₹{spec.net_premium} | Margin ₹{spec.margin_blocked:,.0f}")

    try:
        if settings.telegram_available:
            from integrations.telegram_service import send
            max_profit = (abs(spec.strike_sell - spec.strike_buy) - spec.net_premium) * spec.qty
            max_loss = spec.net_premium * spec.qty
            await send(
                f"🎯 <b>F&O {spread_name}</b>\n"
                f"<b>{spec.underlying}</b> ({spec.expiry:%d-%b-%Y})\n"
                f"BUY  {spec.strike_buy:.0f}{spec.option_type} @ ₹{spec.premium_buy}\n"
                f"SELL {spec.strike_sell:.0f}{spec.option_type} @ ₹{spec.premium_sell}\n"
                f"Net Premium: <b>₹{spec.net_premium}</b>  |  {spec.lots} lot(s)\n"
                f"Max Profit: ₹{max_profit:,.0f}  |  Max Loss: ₹{max_loss:,.0f}\n"
                f"Margin Blocked: ₹{spec.margin_blocked:,.0f}\n"
                f"Conviction: {confidence:.0f}%"
            )
    except Exception as exc:
        logger.debug(f"[fno/exec] telegram alert failed: {exc}")

    return trades
