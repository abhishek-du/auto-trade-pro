"""Market Scanner — two-stage pipeline for full-NSE autonomous trading.

Stage 1 (this file, every 15 min):
  Score all NSE EQ symbols using pre-computed MasterIntelligenceScore + candle
  metrics (volume ratio, RSI, price vs EMA20). Pick the top N candidates and
  write them to market_shortlist.  The trade loop reads the shortlist instead
  of the hardcoded 32-stock watchlist.

Stage 2 (india_trade_loop, every 60 s):
  Read market_shortlist → run full deep-analysis + signal generation → risk
  gate → open paper trades on the best 1-3 opportunities.

This gives full-market coverage (9,600+ NSE EQ symbols) without running deep
analysis on all of them every minute.
"""
import asyncio
import datetime
from zoneinfo import ZoneInfo

from tasks.celery_app import celery_app
from utils.logger import logger

_IST = ZoneInfo("Asia/Kolkata")


def _is_scan_window() -> bool:
    """Run during NSE hours plus 30 min after close."""
    now = datetime.datetime.now(_IST)
    if now.weekday() >= 5:
        return False
    h, m = now.hour, now.minute
    return ((h, m) >= (9, 0)) and ((h, m) <= (16, 0))


async def _run_market_scanner(force: bool = False):
    import pandas as pd
    from sqlalchemy import select, delete, text

    from tasks._db import celery_session as async_session_factory
    from db.models import MarketShortlist
    from utils.config import settings

    if not force and not _is_scan_window():
        logger.info("[market_scanner] Outside trading window — skipping")
        return {"status": "skipped", "reason": "outside trading window"}

    top_n    = int(getattr(settings, "MARKET_SCANNER_TOP_N",           100))
    min_vol  = float(getattr(settings, "MARKET_SCANNER_MIN_VOLUME_RATIO", 0.0))
    min_score = float(getattr(settings, "MARKET_SCANNER_MIN_MASTER_SCORE", 0.0))

    async with async_session_factory() as session:

        # ── 1. Rank by the Master Intelligence Hub's 7-factor score ─────────────
        # The scanner now mirrors the Hub: only symbols the Hub has deep-scored
        # (technical + news + fundamentals + earnings + sector + macro + options,
        # over the ~500-name turnover universe) are eligible. Candle metrics
        # (volume/RSI/EMA) are computed only for display. Symbols without a Hub
        # score are NOT considered. Cold-start fallback: if the Hub hasn't run yet
        # (no scores at all), fall back to technical compute_indicators so the
        # scanner isn't empty before the first expanded Hub cycle.
        from collections import defaultdict
        from engine.indicators import compute_indicators, score_to_signal
        from engine.india_specific import SECTOR_MAP
        from db.models import UserWatchlist, MasterIntelligenceScore

        wl_result = await session.execute(
            select(UserWatchlist.symbol).where(UserWatchlist.is_active == True)
        )
        user_syms = set(wl_result.scalars().all())

        # Latest Hub score per symbol
        hub_subq = (
            select(
                MasterIntelligenceScore.symbol,
                MasterIntelligenceScore.master_score,
                MasterIntelligenceScore.signal,
                MasterIntelligenceScore.is_blocked,
            )
            .distinct(MasterIntelligenceScore.symbol)
            .where(
                MasterIntelligenceScore.symbol.like("%.NS") |
                MasterIntelligenceScore.symbol.like("%.BO")
            )
            .order_by(MasterIntelligenceScore.symbol, MasterIntelligenceScore.scored_at.desc())
        ).subquery()
        hub_rows = (await session.execute(select(hub_subq))).all()
        hub_map = {
            r.symbol: {"score": float(r.master_score), "signal": r.signal}
            for r in hub_rows if not r.is_blocked
        }
        use_hub = len(hub_map) >= 50   # enough Hub coverage to drive the scanner

        if use_hub:
            scan_syms = list(hub_map.keys())
        else:
            res = await session.execute(
                text("SELECT DISTINCT symbol FROM candles WHERE timeframe='1d' AND (symbol LIKE '%.NS' OR symbol LIKE '%.BO')")
            )
            scan_syms = [r.symbol for r in res.all()]
        logger.info(
            f"[market_scanner] source={'HUB-7factor' if use_hub else 'technical-fallback'} "
            f"({len(hub_map)} hub scores) → scanning {len(scan_syms)} symbols"
        )

        candidates: list[dict] = []
        scored_ok = 0
        BATCH = 400
        for i in range(0, len(scan_syms), BATCH):
            batch = scan_syms[i:i + BATCH]
            raw = await session.execute(
                text("""
                    SELECT symbol, open, high, low, close, volume, timestamp
                    FROM candles
                    WHERE symbol = ANY(:syms)
                      AND timeframe = '1d'
                    ORDER BY symbol, timestamp DESC
                """),
                {"syms": batch},
            )
            rows = raw.all()

            grouped: dict[str, list] = defaultdict(list)
            for r in rows:
                if len(grouped[r.symbol]) < 150:
                    grouped[r.symbol].append(r)

            for ns_sym, bars in grouped.items():
                if len(bars) < 15:
                    continue
                base = ns_sym.replace(".NS", "").replace(".BO", "")
                bars = list(reversed(bars))
                df = pd.DataFrame([{
                    "open": float(b.open), "high": float(b.high), "low": float(b.low),
                    "close": float(b.close), "volume": float(b.volume or 0.0),
                    "timestamp": b.timestamp,
                } for b in bars])

                try:
                    sig = compute_indicators(df)
                except Exception:
                    continue

                # Score + signal: Hub (7-factor) when available, else technical.
                if use_hub:
                    h = hub_map.get(ns_sym)
                    if h is None:
                        continue
                    score, signal = h["score"], h["signal"]
                else:
                    score = float(sig.composite_score)
                    if score != score:
                        continue
                    signal = score_to_signal(score)
                scored_ok += 1

                vols = df["volume"].tolist()
                avg_vol = sum(vols[:-1][-20:]) / max(len(vols[:-1][-20:]), 1)
                vol_ratio = (vols[-1] / avg_vol) if avg_vol > 0 else 1.0
                if vol_ratio < min_vol:
                    continue

                rsi = None if (sig.rsi != sig.rsi) else round(sig.rsi, 1)
                ema20 = sig.ema_20
                last_close = float(df["close"].iloc[-1])
                price_vs_ema20 = (
                    round((last_close - ema20) / ema20 * 100, 2)
                    if ema20 and ema20 == ema20 and ema20 != 0 else None
                )

                uc_days = getattr(sig, "upper_circuit_days", 0)
                candidates.append({
                    "symbol":              ns_sym,
                    "master_score":        round(score, 1),
                    "signal":              signal,
                    "sector":              SECTOR_MAP.get(base, ""),
                    "volume_ratio":        round(vol_ratio, 2),
                    "rsi":                 rsi,
                    "price_vs_ema20":      price_vs_ema20,
                    "upper_circuit_days":  uc_days,
                    "volume_surge":        getattr(sig, "volume_surge", 1.0),
                    # Boost rank for circuit stocks so they surface even when
                    # absolute score lags (Hub re-scores on next cycle).
                    "rank_score": (
                        abs(score)
                        + min(vol_ratio - 1.0, 2.0) * 3
                        + uc_days * 8
                    ),
                    "is_user":             ns_sym in user_syms,
                })

        logger.info(f"[market_scanner] scored {scored_ok} symbols ({'hub' if use_hub else 'technical'})")

        # Force-include user-watchlist symbols that failed scoring (no/low candles)
        existing = {c["symbol"] for c in candidates}
        for ns_sym in user_syms:
            if ns_sym not in existing:
                base = ns_sym.replace(".NS", "")
                candidates.append({
                    "symbol":         ns_sym,
                    "master_score":   0.0,
                    "signal":         "NEUTRAL",
                    "sector":         SECTOR_MAP.get(base, ""),
                    "volume_ratio":   1.0,
                    "rsi":            None,
                    "price_vs_ema20": None,
                    "rank_score":     999.0,   # pin user picks to the top
                    "is_user":        True,
                })

        # Sort: user picks first, then by conviction (|score|) + volume
        candidates.sort(key=lambda c: (c.get("is_user", False), c["rank_score"]), reverse=True)
        shortlist = candidates[:top_n]

        # ── 5. Overwrite market_shortlist table ───────────────────────────────
        await session.execute(delete(MarketShortlist))

        for rank, c in enumerate(shortlist, start=1):
            session.add(MarketShortlist(
                symbol=c["symbol"],
                master_score=c["master_score"],
                volume_ratio=c["volume_ratio"],
                rsi=c["rsi"],
                price_vs_ema20=c["price_vs_ema20"],
                signal=c["signal"],
                sector=c["sector"],
                rank=rank,
                upper_circuit_days=c.get("upper_circuit_days", 0),
                volume_surge=c.get("volume_surge", 1.0),
            ))

        await session.commit()

        # Trigger background pre-diagnosis for the Top 10 shortlisted stocks
        top_10_syms = [c["symbol"] for c in shortlist[:10]]
        from tasks.pre_diagnose import run_pre_diagnose
        run_pre_diagnose.delay(top_10_syms)

        logger.info(
            f"[market_scanner] Done — source={'hub' if use_hub else 'technical'} "
            f"scanned={len(scan_syms)} → {len(candidates)} candidates → "
            f"{len(shortlist)} in shortlist "
            f"(BUY={sum(1 for c in shortlist if 'BUY' in c['signal'])}, "
            f"SELL={sum(1 for c in shortlist if 'SELL' in c['signal'])})"
        )
        return {
            "status":          "ok",
            "source":          "hub" if use_hub else "technical",
            "scanned":         len(scan_syms),
            "scored":          scored_ok,
            "candidates":      len(candidates),
            "shortlist":       len(shortlist),
            "buy":             sum(1 for c in shortlist if "BUY" in c["signal"]),
            "sell":            sum(1 for c in shortlist if "SELL" in c["signal"]),
        }


def _simple_rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder RSI — lightweight, no pandas needed."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas[-period:]]
    losses = [-min(d, 0) for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _ema(closes: list[float], period: int) -> float | None:
    """Exponential moving average of the last `period` values."""
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
    return ema


@celery_app.task(name="tasks.market_scanner.run_market_scanner")
def run_market_scanner(force: bool = False):
    """Celery task: score all NSE EQ symbols and write top-N to market_shortlist.

    Runs every 15 min during market hours.  The india_trade_loop reads the
    shortlist so the agent automatically covers the full NSE universe.
    """
    logger.info("[market_scanner] Starting scan")
    result = asyncio.run(_run_market_scanner(force=force))
    logger.info(f"[market_scanner] Result: {result}")
    return result
