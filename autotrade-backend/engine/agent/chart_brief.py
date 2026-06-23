"""Technical / chart brief — a 'veteran chartist' text read of a symbol.

build_chart_brief() distils what the agent already computes (candlestick patterns,
12 indicator groups + composite signal, support/resistance, LSTM/RF direction)
into one compact text block, so the LLM reasoning gate can weigh the *chart* and
the *ML forecast* alongside the 7 fundamental/sentiment factors — i.e. reason like
a trader who actually looked at the chart, not just at numbers.

No LLM call, no new heavy dependency — pure local synthesis. Fail-open: returns ""
on any error so it can never block the decision path.
"""
from __future__ import annotations

import math

import pandas as pd

from utils.config import settings
from utils.logger import logger


def _fmt(x, nd: int = 1) -> str:
    try:
        v = float(x)
        if math.isnan(v):
            return "n/a"
        return f"{v:.{nd}f}"
    except Exception:
        return "n/a"


def build_chart_brief(symbol: str, df: "pd.DataFrame | None", sig=None) -> str:
    """Return a short technical/chart brief for `symbol`, or "" on any failure.

    `df`  — OHLCV daily DataFrame (most-recent rows; index or a timestamp column).
    `sig` — an already-computed `IndicatorSignals` to reuse; if None, computed here.
    """
    if not getattr(settings, "AGENT_CHART_BRIEF_ENABLED", True):
        return ""
    if df is None or len(df) < 20:
        return ""
    try:
        lines: list[str] = []

        # ── 1. Candlestick read ───────────────────────────────────────────────
        try:
            from engine.candlestick import detect_patterns, get_pattern_summary
            ps = get_pattern_summary(detect_patterns(df))
            if ps.get("count"):
                lines.append(
                    f"Candles: {ps.get('direction')} "
                    f"(strongest {ps.get('strongest_pattern')}, "
                    f"score {ps.get('total_score')}, {ps.get('count')} patterns)"
                )
            else:
                lines.append("Candles: no notable pattern on the last bar")
        except Exception as exc:
            logger.debug(f"[chart_brief] candles failed {symbol}: {exc}")

        # ── 2. Indicator states + composite ───────────────────────────────────
        try:
            if sig is None:
                from engine.indicators import compute_indicators
                sig = compute_indicators(df)
            from engine.indicators import score_to_signal
            comp = getattr(sig, "composite_score", float("nan"))
            lines.append(
                f"Technicals: composite {_fmt(comp,0)} → {score_to_signal(comp)} | "
                f"RSI {_fmt(getattr(sig,'rsi',float('nan')),0)} ({getattr(sig,'rsi_signal','?')}) | "
                f"MACD {getattr(sig,'macd_cross','?')} | "
                f"trend {getattr(sig,'ema_trend','?')} / ribbon {getattr(sig,'ema_ribbon_state','?')} | "
                f"Supertrend {getattr(sig,'supertrend_direction','?')} | "
                f"ADX {_fmt(getattr(sig,'adx',float('nan')),0)} "
                f"({getattr(sig,'adx_trend_strength','?')} {getattr(sig,'adx_direction','?')}) | "
                f"Ichimoku {getattr(sig,'ichimoku_signal','?')} | "
                f"BB {getattr(sig,'bb_position','?')} | VWAP {getattr(sig,'vwap_position','?')}"
            )
            vs = getattr(sig, "volume_surge", None)
            if vs and vs >= 1.5:
                lines.append(f"Volume: surge {_fmt(vs,1)}x the 20-bar average")
        except Exception as exc:
            logger.debug(f"[chart_brief] indicators failed {symbol}: {exc}")

        # ── 3. Support / resistance from recent swings ────────────────────────
        try:
            window = df.tail(20)
            hi = float(window["high"].max())
            lo = float(window["low"].min())
            last = float(df["close"].iloc[-1])
            pos = (last - lo) / (hi - lo) * 100 if hi > lo else 50.0
            lines.append(
                f"Range(20d): support ~{_fmt(lo,2)}, resistance ~{_fmt(hi,2)}, "
                f"last {_fmt(last,2)} ({pos:.0f}% of range)"
            )
        except Exception as exc:
            logger.debug(f"[chart_brief] range failed {symbol}: {exc}")

        # ── 4. ML candle/direction forecast ───────────────────────────────────
        try:
            from engine.ml_predictor import predict_direction
            pred = predict_direction(symbol, df)
            if pred.get("error"):
                lines.append("ML forecast: no trained model for this symbol")
            else:
                lines.append(
                    f"ML forecast: next-day {pred.get('predicted_direction')} "
                    f"(conf {_fmt(100*float(pred.get('confidence',0) or 0),0)}%, "
                    f"up {_fmt(100*float(pred.get('up_prob',0) or 0),0)}% / "
                    f"down {_fmt(100*float(pred.get('down_prob',0) or 0),0)}%)"
                )
        except Exception as exc:
            logger.debug(f"[chart_brief] ml failed {symbol}: {exc}")

        return "\n".join(lines).strip()
    except Exception as exc:
        logger.debug(f"[chart_brief] build failed {symbol}: {exc}")
        return ""
