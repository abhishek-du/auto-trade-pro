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


# ── Indian market ─────────────────────────────────────────────────────────────

class FIIDIIFlowOut(BaseModel):
    id:              int
    date:            date
    fii_net_buy:     float
    dii_net_buy:     float
    fii_gross_buy:   float
    fii_gross_sell:  float
    dii_gross_buy:   float
    dii_gross_sell:  float
    market_direction: str
    created_at:      datetime


class OptionsSnapshotOut(BaseModel):
    id:                int
    symbol:            str
    expiry_date:       date
    atm_strike:        float
    pcr:               float
    max_pain:          float
    total_call_oi:     int
    total_put_oi:      int
    support_levels:    Optional[list]
    resistance_levels: Optional[list]
    snapshot_at:       datetime


class VIXScoreOut(BaseModel):
    vix:   Optional[float]
    score: float
    label: str   # 'CRASH_ZONE'|'EXTREME_FEAR'|'HIGH_FEAR'|'ELEVATED'|'NORMAL'|'BULL_RUN'|'COMPLACENCY'


class SIPResultOut(BaseModel):
    scheme_code:       str
    scheme_name:       str
    monthly_amount:    float
    months_invested:   int
    total_invested:    float
    current_value:     float
    absolute_return_pct: float
    cagr:              float
    units_held:        float


class MutualFundOut(BaseModel):
    scheme_code:    str
    scheme_name:    str
    fund_house:     str
    category:       str
    current_nav:    float
    nav_date:       date
    return_1y:      Optional[float]
    return_3y:      Optional[float]
    return_5y:      Optional[float]
    sip_1y:         Optional[SIPResultOut]
    sip_3y:         Optional[SIPResultOut]
    volatility:     Optional[float]
    sharpe_ratio:   Optional[float]
    analyzed_at:    datetime


class SIPProjectionIn(BaseModel):
    monthly_amount:             float
    expected_annual_return_pct: float
    months:                     int


class SIPProjectionOut(BaseModel):
    monthly_amount:       float
    months:               int
    assumed_cagr_pct:     float
    total_invested:       float
    projected_value:      float
    absolute_return:      float
    absolute_return_pct:  float


class FundamentalDataOut(BaseModel):
    symbol:             str
    market_cap_cr:      Optional[float]
    current_price:      Optional[float]
    high_52w:           Optional[float]
    low_52w:            Optional[float]
    pe_ratio:           Optional[float]
    pb_ratio:           Optional[float]
    dividend_yield_pct: Optional[float]
    roce_pct:           Optional[float]
    roe_pct:            Optional[float]
    debt_to_equity:     Optional[float]
    eps:                Optional[float]
    book_value:         Optional[float]
    face_value:         Optional[float]
    fetched_at:         datetime


class FundamentalAnalysisOut(BaseModel):
    symbol:          str
    data:            FundamentalDataOut
    pe_score:        float
    roe_score:       float
    debt_score:      float
    roce_score:      float
    composite_score: float
    valuation_label: str
    analyzed_at:     datetime


class SectorRotationOut(BaseModel):
    symbol:  str
    sector:  str
    score:   float
