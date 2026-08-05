"""Matplotlib chart image generation for Telegram alerts.

matplotlib/pillow are already project dependencies (requirements.txt) --
no new dependency for this phase. Uses the non-interactive "Agg" backend
since this runs inside Celery workers/the API server, never a display.
"""
from __future__ import annotations

import io
import logging

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

logger = logging.getLogger(__name__)

_BG = "#0a1120"
_GRID = "#1e293b"
_AXIS = "#334155"
_TEXT = "#8899aa"
_ENTRY_COLOR = "#2979FF"
_STOP_COLOR = "#FF1744"
_TARGET_COLOR = "#00E676"


def build_entry_chart(symbol: str, entry: float, stop: float, target: float, df=None) -> bytes:
    """Renders a PNG price chart with entry/stop/target reference lines.

    If `df` (a pandas DataFrame with a 'close' column, chronologically
    ordered) is given, plots the actual recent price series behind the
    reference lines. Otherwise draws a flat reference line at `entry` alone
    -- a chart can still be sent (with the trade's own levels clearly
    marked) even when no candle data is available for this symbol.
    """
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=110)

    if df is not None and not df.empty and "close" in df.columns:
        closes = df["close"].astype(float).tolist()
        xmax = max(len(closes) - 1, 1)
        ax.plot(range(len(closes)), closes, color=_ENTRY_COLOR, linewidth=1.6)
    else:
        xmax = 1
        ax.plot([0, xmax], [entry, entry], color=_ENTRY_COLOR, linewidth=1.6)

    ax.axhline(entry, color=_ENTRY_COLOR, linestyle="-", linewidth=1.1, alpha=0.85)
    ax.axhline(stop, color=_STOP_COLOR, linestyle="--", linewidth=1.1, alpha=0.85)
    ax.axhline(target, color=_TARGET_COLOR, linestyle="--", linewidth=1.1, alpha=0.85)

    for y, label, color in ((entry, "Entry", _ENTRY_COLOR), (stop, "SL", _STOP_COLOR), (target, "TP", _TARGET_COLOR)):
        ax.annotate(
            f" {label} {y:,.2f}", xy=(xmax, y), xytext=(4, 0), textcoords="offset points",
            color=color, va="center", fontsize=9, fontweight="bold",
        )

    ax.set_title(symbol, fontsize=13, fontweight="bold", loc="left", color="white")
    ax.set_facecolor(_BG)
    fig.patch.set_facecolor(_BG)
    ax.tick_params(colors=_TEXT, labelsize=8)
    ax.set_xticks([])
    for spine in ax.spines.values():
        spine.set_color(_AXIS)
    ax.grid(True, color=_GRID, linewidth=0.6, axis="y")
    # Headroom so the entry/SL/TP annotations on the right edge aren't clipped.
    ax.margins(x=0.08)

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", facecolor=_BG)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def build_equity_curve_chart(snapshots: list[tuple[object, float]]) -> bytes:
    """Renders a PNG equity curve from a chronologically-ordered list of
    (date, equity) pairs -- used by the weekly report's PDF (Phase 5).
    `snapshots` empty/None still produces a valid (near-blank) chart rather
    than raising, matching build_entry_chart's "always returns a usable
    image" contract."""
    fig, ax = plt.subplots(figsize=(8, 3.5), dpi=110)

    if snapshots:
        equities = [s[1] for s in snapshots]
        ax.plot(range(len(equities)), equities, color=_TARGET_COLOR, linewidth=1.8)
        ax.fill_between(range(len(equities)), equities, min(equities), color=_TARGET_COLOR, alpha=0.08)

    ax.set_title("Equity Curve", fontsize=13, fontweight="bold", loc="left", color="white")
    ax.set_facecolor(_BG)
    fig.patch.set_facecolor(_BG)
    ax.tick_params(colors=_TEXT, labelsize=8)
    ax.set_xticks([])
    for spine in ax.spines.values():
        spine.set_color(_AXIS)
    ax.grid(True, color=_GRID, linewidth=0.6, axis="y")

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", facecolor=_BG)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


async def fetch_equity_snapshots(limit: int = 60) -> list[tuple[object, float]]:
    """Best-effort fetch of the last `limit` days of AgentCapitalSnapshot
    equity history, oldest first. Returns [] on any failure -- an empty
    equity curve is a fine fallback, never a reason to skip the PDF."""
    try:
        from sqlalchemy import select

        from db.models import AgentCapitalSnapshot
        from tasks._db import celery_session

        async with celery_session() as session:
            rows = (await session.execute(
                select(AgentCapitalSnapshot.snapshot_date, AgentCapitalSnapshot.equity)
                .order_by(AgentCapitalSnapshot.snapshot_date.desc())
                .limit(limit)
            )).all()
        return [(r[0], r[1]) for r in reversed(rows)]
    except Exception as exc:
        logger.debug(f"[alerts.charts] equity snapshot fetch failed: {exc}")
        return []


async def fetch_recent_candles(symbol: str, timeframe: str = "1d", limit: int = 30):
    """Best-effort fetch of the last `limit` candles for `symbol`, oldest
    first (matplotlib plots left-to-right chronologically). Returns None on
    any failure -- callers must treat a missing DataFrame as "draw a flat
    reference line instead," never as a reason to skip the chart entirely."""
    try:
        import pandas as pd
        from sqlalchemy import select

        from db.models import Candle
        from tasks._db import celery_session

        async with celery_session() as session:
            rows = (await session.execute(
                select(Candle.close, Candle.timestamp)
                .where(Candle.symbol == symbol, Candle.timeframe == timeframe)
                .order_by(Candle.timestamp.desc())
                .limit(limit)
            )).all()
        if not rows:
            return None
        rows = list(reversed(rows))  # chronological order for plotting
        return pd.DataFrame({"close": [r[0] for r in rows], "timestamp": [r[1] for r in rows]})
    except Exception as exc:
        logger.debug(f"[alerts.charts] candle fetch failed for {symbol}: {exc}")
        return None
