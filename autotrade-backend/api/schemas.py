# Pydantic response models for all AutoTrade Pro API routes.
# Used as response_model= in FastAPI decorators for automatic docs + validation.

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel


# ── Wallet / Portfolio ────────────────────────────────────────────────────────

class WalletSummary(BaseModel):
    balance:        float
    equity:         float
    realised_pnl:   float
    unrealised_pnl: float
    total_trades:   int
    winning_trades: int
    win_rate:       float
    max_drawdown:   float
    peak_balance:   float
    roi_percent:    float
    mode:           str


class OpenPositionOut(BaseModel):
    id:             int
    symbol:         str
    direction:      str
    entry_price:    float
    current_price:  float
    stop_loss:      float
    take_profit:    float
    size_units:     float
    size_usd:       float
    unrealised_pnl: float
    unrealised_pct: float
    trade_id:       int
    opened_at:      datetime
    last_updated:   datetime


class PerformanceSnapshotOut(BaseModel):
    id:             int
    date:           date
    balance:        float
    equity:         float
    daily_pnl:      float
    trades_today:   int
    win_rate_today: float
    snapshot_at:    datetime


class PortfolioStatsOut(BaseModel):
    total_signals_generated:  int
    trades_taken:             int
    trades_rejected:          int
    win_rate:                 float
    avg_pnl:                  float
    best_trade:               float
    worst_trade:              float
    roi_percent:              float
    avg_confidence_on_wins:   float
    avg_confidence_on_losses: float


# ── Trades ────────────────────────────────────────────────────────────────────

class PaperTradeOut(BaseModel):
    id:                   int
    symbol:               str
    direction:            str
    status:               str
    entry_price:          float
    exit_price:           Optional[float]
    stop_loss:            float
    take_profit:          float
    size_units:           float
    size_usd:             float
    pnl:                  Optional[float]
    pnl_percent:          Optional[float]
    ai_reason:            str
    signal_confidence:    float
    pattern_name:         str
    news_sentiment_score: float
    slippage_applied:     float
    opened_at:            datetime
    closed_at:            Optional[datetime]


class TradeSummaryOut(BaseModel):
    total:      int
    open:       int
    closed:     int
    stopped:    int
    wins:       int
    losses:     int
    win_rate:   float
    total_pnl:  float


# ── Signals ───────────────────────────────────────────────────────────────────

class SignalOut(BaseModel):
    id:             int
    symbol:         str
    timeframe:      str
    signal_type:    str
    confidence:     float
    pattern_name:   str
    news_sentiment: float
    final_score:    float
    created_at:     datetime


class TriggerResult(BaseModel):
    signals_generated: int
    actionable:        int
    symbols:           list[str]


# ── News ──────────────────────────────────────────────────────────────────────

class NewsItemOut(BaseModel):
    id:               int
    headline:         str
    source:           str
    url:              Optional[str]
    sentiment:        Optional[str]
    score:            float
    tickers_affected: Optional[list]
    published_at:     Optional[datetime]
    crawled_at:       datetime


class SentimentOut(BaseModel):
    symbol:      str
    avg_score:   float
    description: str


# ── Simulation ────────────────────────────────────────────────────────────────

class SimulationLogOut(BaseModel):
    id:         int
    event_type: str
    symbol:     str
    message:    str
    data:       Optional[dict[str, Any]]
    timestamp:  datetime


class AnalysisEntryOut(BaseModel):
    id:              int
    timestamp:       Optional[str]
    symbol:          str
    message:         str
    action:          Optional[str]
    confidence:      Optional[float]
    final_score:     Optional[float]
    trade_taken:     Optional[bool]
    reject_reason:   Optional[str]


class ShouldGoLiveOut(BaseModel):
    ready:   bool
    reason:  str
    metrics: dict[str, Any]


# ── Analytics ─────────────────────────────────────────────────────────────────

class EquityPoint(BaseModel):
    date:   Any
    equity: float


class DailyPnlPoint(BaseModel):
    date:      Any
    daily_pnl: float
    balance:   float


class PnlBySymbolOut(BaseModel):
    symbol:    str
    trades:    int
    total_pnl: float
    win_rate:  float


class AnalyticsOut(BaseModel):
    win_rate:                  float
    avg_rr:                    Optional[float]
    total_trades:              int
    total_pnl:                 float
    equity_curve:              list[EquityPoint]
    pnl_by_symbol:             list[PnlBySymbolOut]
    trades_by_direction:       dict[str, int]
    daily_pnl_chart:           list[DailyPnlPoint]
    best_trade:                Optional[dict[str, Any]]
    worst_trade:               Optional[dict[str, Any]]
    avg_trade_duration_hours:  Optional[float]
