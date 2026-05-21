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
    company_name:       str
    pe_ratio:           Optional[float]
    pb_ratio:           Optional[float]
    roe:                Optional[float]
    roce:               Optional[float]
    debt_to_equity:     Optional[float]
    current_ratio:      Optional[float]
    revenue_growth_3yr: Optional[float]
    profit_growth_3yr:  Optional[float]
    promoter_holding:   Optional[float]
    fii_holding:        Optional[float]
    pledged_pct:        Optional[float]
    market_cap_cr:      Optional[float]
    dividend_yield:     Optional[float]
    fundamental_score:  Optional[float]
    last_updated:       datetime


class SectorRotationOut(BaseModel):
    symbol:  str
    sector:  str
    score:   float


# ── Mutual fund (DB-backed, replaces in-memory MutualFundOut) ─────────────────

class MutualFundNAVOut(BaseModel):
    id:                 int
    scheme_code:        str
    scheme_name:        str
    nav:                float
    prev_nav:           float
    change:             float
    change_pct:         float
    category:           str
    one_month_return:   Optional[float]
    three_month_return: Optional[float]
    one_year_return:    Optional[float]
    three_year_return:  Optional[float]
    recorded_at:        datetime


class MutualFundWithSignalOut(BaseModel):
    scheme_code:        str
    scheme_name:        str
    current_nav:        float
    one_month_return:   Optional[float]
    three_month_return: Optional[float]
    one_year_return:    Optional[float]
    three_year_return:  Optional[float]
    change_pct:         float
    category:           str
    recorded_at:        datetime
    # Signal fields
    signal:             str            # 'BUY' | 'HOLD'
    reason:             str
    high_52w:           Optional[float]
    dip_from_high_pct:  Optional[float]
    vix:                Optional[float]


class SIPSimulationOut(BaseModel):
    scheme_code:     str
    monthly_amount:  float
    months:          int
    total_invested:  float
    current_value:   float
    total_units:     float
    avg_nav:         float
    absolute_return: float
    cagr_percent:    float
    best_month:      Optional[dict]
    worst_month:     Optional[dict]


class FundComparisonOut(BaseModel):
    scheme_code:       str
    scheme_name:       str
    current_nav:       float
    one_year_return:   float
    three_year_return: float
    consistency_std:   Optional[float]
    composite_score:   float
    best_fund:         bool


# ── Market status ─────────────────────────────────────────────────────────────

class MarketIndexOut(BaseModel):
    price:      Optional[float]
    change:     Optional[float]
    change_pct: Optional[float]


class MarketStatusOut(BaseModel):
    nse_open:      bool
    ist_time:      str
    nifty:         MarketIndexOut
    bank_nifty:    MarketIndexOut
    sensex:        MarketIndexOut
    india_vix:     Optional[float]
    today_holiday: bool
    holiday_name:  str


# ── FII/DII summary ───────────────────────────────────────────────────────────

class FIIDIITodayOut(BaseModel):
    fii_net:          float
    dii_net:          float
    market_direction: str


class FIIDIIAvgOut(BaseModel):
    fii_avg: float
    dii_avg: float


class FIIDIIChartPoint(BaseModel):
    date:    date
    fii_net: float
    dii_net: float


class FIIDIISummaryOut(BaseModel):
    today:        Optional[FIIDIITodayOut]
    five_day_avg: Optional[FIIDIIAvgOut]
    trend:        str   # ACCUMULATION | DISTRIBUTION | MIXED
    score:        float
    chart_data:   list[FIIDIIChartPoint]


# ── Options chain detail ──────────────────────────────────────────────────────

class OptionsStrikeOut(BaseModel):
    strike:   float
    call_oi:  int
    put_oi:   int
    call_ltp: Optional[float]
    put_ltp:  Optional[float]


class OptionsChainDetailOut(BaseModel):
    spot_price:        Optional[float]
    expiry_date:       Optional[date]
    pcr:               Optional[float]
    max_pain:          Optional[float]
    support_levels:    Optional[list]
    resistance_levels: Optional[list]
    options_score:     Optional[float]
    chain_data:        list[OptionsStrikeOut]


# ── Mutual fund list (simplified) ─────────────────────────────────────────────

class MutualFundBriefOut(BaseModel):
    scheme_code:   str
    name:          str
    nav:           float
    change_pct:    float
    one_yr_return: Optional[float]
    signal:        str
    category:      str


class MutualFundListOut(BaseModel):
    funds: list[MutualFundBriefOut]


# ── SIP brief ─────────────────────────────────────────────────────────────────

class SIPBriefOut(BaseModel):
    total_invested:  float
    current_value:   float
    cagr:            float
    absolute_return: float


# ── Sector performance ────────────────────────────────────────────────────────

class SectorPerfItem(BaseModel):
    name:         str
    return_30d:   Optional[float]
    vs_nifty_pct: Optional[float]
    signal:       str   # OUTPERFORM | UNDERPERFORM | NEUTRAL


class SectorPerfOut(BaseModel):
    sectors: list[SectorPerfItem]


# ── Seed result ───────────────────────────────────────────────────────────────

class SeedResultOut(BaseModel):
    status:             str
    symbols_fetched:    int
    candles_saved:      int
    signals_generated:  int
    actionable_signals: int
    duration_seconds:   float
