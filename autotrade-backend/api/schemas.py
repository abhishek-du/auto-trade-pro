# Pydantic response models for all AutoTrade Pro API routes.
# Used as response_model= in FastAPI decorators for automatic docs + validation.

from datetime import date, datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, field_serializer


# ── Timestamp serialisation (2026-08-17) ─────────────────────────────────────
# Every timestamp in this database is stored NAIVE UTC: 62 columns default to
# func.now() on a UTC-configured Postgres, and app-side writers use
# datetime.utcnow(). Pydantic serialised those naive values as-is —
# "2026-08-17T13:26:02.926", with no offset — so a browser parsed them as LOCAL
# time and rendered UTC values as if they were IST. Every timestamp in the UI
# read 5h30m early.
#
# Fixed by tagging the offset the values actually carry rather than by
# converting anything: naive values are marked +00:00 on the way out, so
# JS `new Date(...)` converts to the viewer's zone (IST here) automatically.
#
# Deliberately NOT done by rewriting stored data to IST: the app clock
# (datetime.utcnow()) is compared against candle timestamps in the SL/TP
# freshness gates (paper_trading/trade_simulator.py, engine/agent/execution.py).
# Shifting either side of that comparison by 5.5h would make every candle look
# stale during market hours and silently disable stop-loss monitoring. Storage
# stays uniformly UTC; only the wire format becomes explicit.
_UTC = timezone.utc


class _UtcAwareTimestamps(BaseModel):
    """Mixin: emit naive datetimes as UTC-aware ISO 8601."""

    @field_serializer("*", when_used="json", check_fields=False)
    def _tag_utc(self, value, _info):
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=_UTC).isoformat()
        return value


# ── Wallet / Portfolio ────────────────────────────────────────────────────────

class WalletSummary(_UtcAwareTimestamps):
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


class OpenPositionOut(_UtcAwareTimestamps):
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
    # Trade-management state (from PaperTrade.indicator_snapshot.trade_mgmt)
    target_1:       float | None = None   # first checkpoint / trailing trigger
    target_2:       float | None = None   # final target (= take_profit)
    atr:            float | None = None
    trailing:       bool         = False  # True once T1 hit → stop trails by 1×ATR
    level_source:   str | None   = None   # 'dynamic' | 'atr' | 'static'
    # Sector grouping (2026-08-04, /trades redesign): real lookup via
    # utils.sector_cache.get_sector(), not a frontend mock -- see that
    # module's docstring for the cache/live-fallback chain. 'GENERAL' on miss.
    sector:         str | None   = None


class PerformanceSnapshotOut(_UtcAwareTimestamps):
    id:             int
    date:           date
    balance:        float
    equity:         float
    daily_pnl:      float
    trades_today:   int
    win_rate_today: float
    snapshot_at:    datetime


class PortfolioStatsOut(_UtcAwareTimestamps):
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

class PaperTradeOut(_UtcAwareTimestamps):
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
    # Confidence transparency (2026-07-22): the factor breakdown behind
    # `signal_confidence` -- bull/bear/key_risk/thesis/tools_used/grounding/
    # model_reasoning for a DIRECT LLM verdict, or event_strength/
    # relationship_strength/company_exposure/market_confirmation for a
    # SECOND_ORDER cascade formula result. {} for legacy trades predating
    # this field or non-event-driven (TECHNICAL/FNO) strategies.
    confidence_factors:   dict = {}


class TradeSummaryOut(_UtcAwareTimestamps):
    total:      int
    open:       int
    closed:     int
    stopped:    int
    wins:       int
    losses:     int
    win_rate:   float
    total_pnl:  float


# ── Signals ───────────────────────────────────────────────────────────────────

class SignalOut(_UtcAwareTimestamps):
    id:             int
    symbol:         str
    timeframe:      str
    signal_type:    str
    confidence:     float
    pattern_name:   str
    news_sentiment: float
    final_score:    float
    created_at:     datetime


class SignalDetail(_UtcAwareTimestamps):
    symbol:           str
    action:           str
    confidence:       float
    final_score:      float
    reasoning_points: list[str]


class TriggerResult(_UtcAwareTimestamps):
    signals_generated: int
    actionable:        int
    symbols:           list[str]
    signal_details:    list[SignalDetail] = []


# ── News ──────────────────────────────────────────────────────────────────────

class NewsItemOut(_UtcAwareTimestamps):
    id:               int
    headline:         str
    source:           str
    url:              Optional[str]
    sentiment:        str
    score:            float
    tickers_affected: Optional[list[str]]
    published_at:     Optional[datetime]
    crawled_at:       datetime
    high_impact:      bool = False
    category:         Optional[str] = None
    company:          Optional[str] = None

class CausalEventOut(_UtcAwareTimestamps):
    id:               int
    # Optional: news_discovery_engine.py's own CausalEvent writes intentionally
    # leave this None (that pipeline doesn't link back to a NewsItem row) --
    # crawler/event_pipeline.py's rows do set it. Was `int` (required), so
    # ANY row with news_id=None crashed the whole /causal endpoint with a
    # pydantic ValidationError -> 500, silently swallowed by the frontend's
    # .catch() as "no causal events" (2026-07-22, found via the News page
    # redesign's Market Events tab showing blank).
    news_id:          Optional[int] = None
    event_title:      str
    country:          str
    importance:       float
    confidence:       float
    affected_sectors: list[str]
    affected_indices: list[str]
    bullish_stocks:   list[str]
    bearish_stocks:   list[str]
    duration:         str
    created_at:       datetime
    headline:         Optional[str] = None
    source:           Optional[str] = None   # NSE-Announcements rows only, e.g. "Acquisition"
    company:          Optional[str] = None   # NSE-Announcements rows only


class SSEAnnouncementOut(_UtcAwareTimestamps):
    id:            int
    seq_id:        str
    comp_name:     Optional[str]
    symbol:        Optional[str]
    an_desc:       Optional[str]
    text:          Optional[str]
    an_attach:     Optional[str]
    att_file_size: Optional[str]
    has_xbrl:      bool
    ann_date:      Optional[datetime]
    ann_tstamp:    Optional[datetime]
    diff_time:     Optional[str]
    sentiment:     Optional[str]
    score:         float
    crawled_at:    datetime


class SentimentOut(_UtcAwareTimestamps):
    symbol:      str
    avg_score:   float
    description: str


# ── Simulation ────────────────────────────────────────────────────────────────

class SimulationLogOut(_UtcAwareTimestamps):
    id:         int
    event_type: str
    symbol:     str
    message:    str
    data:       Optional[dict[str, Any]]
    timestamp:  datetime


class AnalysisEntryOut(_UtcAwareTimestamps):
    id:              int
    timestamp:       Optional[str]
    symbol:          str
    message:         str
    action:          Optional[str]
    confidence:      Optional[float]
    final_score:     Optional[float]
    trade_taken:     Optional[bool]
    reject_reason:   Optional[str]


class ShouldGoLiveOut(_UtcAwareTimestamps):
    ready:   bool
    reason:  str
    metrics: dict[str, Any]


# ── Analytics ─────────────────────────────────────────────────────────────────

class EquityPoint(_UtcAwareTimestamps):
    date:   Any
    equity: float


class DailyPnlPoint(_UtcAwareTimestamps):
    date:      Any
    daily_pnl: float
    balance:   float


class PnlBySymbolOut(_UtcAwareTimestamps):
    symbol:    str
    trades:    int
    total_pnl: float
    win_rate:  float


class AnalyticsOut(_UtcAwareTimestamps):
    win_rate:                  float
    avg_rr:                    Optional[float]
    total_trades:              int
    total_pnl:                 float
    roi_pct:                   Optional[float]
    equity_curve:              list[EquityPoint]
    pnl_by_symbol:             list[PnlBySymbolOut]
    trades_by_direction:       dict[str, int]
    daily_pnl_chart:           list[DailyPnlPoint]
    best_trade:                Optional[dict[str, Any]]
    worst_trade:               Optional[dict[str, Any]]
    avg_trade_duration_hours:  Optional[float]


# ── Indian market ─────────────────────────────────────────────────────────────

class FIIDIIFlowOut(_UtcAwareTimestamps):
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


class OptionsSnapshotOut(_UtcAwareTimestamps):
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


class VIXScoreOut(_UtcAwareTimestamps):
    vix:   Optional[float]
    score: float
    label: str   # 'CRASH_ZONE'|'EXTREME_FEAR'|'HIGH_FEAR'|'ELEVATED'|'NORMAL'|'BULL_RUN'|'COMPLACENCY'


class SIPResultOut(_UtcAwareTimestamps):
    scheme_code:       str
    scheme_name:       str
    monthly_amount:    float
    months_invested:   int
    total_invested:    float
    current_value:     float
    absolute_return_pct: float
    cagr:              float
    units_held:        float


class MutualFundOut(_UtcAwareTimestamps):
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


class SIPProjectionIn(_UtcAwareTimestamps):
    monthly_amount:             float
    expected_annual_return_pct: float
    months:                     int


class SIPProjectionOut(_UtcAwareTimestamps):
    monthly_amount:       float
    months:               int
    assumed_cagr_pct:     float
    total_invested:       float
    projected_value:      float
    absolute_return:      float
    absolute_return_pct:  float


class FundamentalDataOut(_UtcAwareTimestamps):
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


class SectorRotationOut(_UtcAwareTimestamps):
    symbol:  str
    sector:  str
    score:   float


# ── Mutual fund (DB-backed, replaces in-memory MutualFundOut) ─────────────────

class MutualFundNAVOut(_UtcAwareTimestamps):
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


class MutualFundWithSignalOut(_UtcAwareTimestamps):
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


class SIPSimulationOut(_UtcAwareTimestamps):
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


class FundComparisonOut(_UtcAwareTimestamps):
    scheme_code:       str
    scheme_name:       str
    current_nav:       float
    one_year_return:   float
    three_year_return: float
    consistency_std:   Optional[float]
    composite_score:   float
    best_fund:         bool


# ── Market status ─────────────────────────────────────────────────────────────

class MarketIndexOut(_UtcAwareTimestamps):
    price:      Optional[float]
    change:     Optional[float]
    change_pct: Optional[float]


class MarketStatusOut(_UtcAwareTimestamps):
    nse_open:      bool
    ist_time:      str
    nifty:         MarketIndexOut
    bank_nifty:    MarketIndexOut
    sensex:        MarketIndexOut
    india_vix:     Optional[float]
    today_holiday: bool
    holiday_name:  str


# ── FII/DII summary ───────────────────────────────────────────────────────────

class FIIDIITodayOut(_UtcAwareTimestamps):
    fii_net:          float
    dii_net:          float
    market_direction: str


class FIIDIIAvgOut(_UtcAwareTimestamps):
    fii_avg: float
    dii_avg: float


class FIIDIIChartPoint(_UtcAwareTimestamps):
    date:    date
    fii_net: float
    dii_net: float


class FIIDIISummaryOut(_UtcAwareTimestamps):
    today:        Optional[FIIDIITodayOut]
    five_day_avg: Optional[FIIDIIAvgOut]
    trend:        str   # ACCUMULATION | DISTRIBUTION | MIXED
    score:        float
    chart_data:   list[FIIDIIChartPoint]


# ── Options chain detail ──────────────────────────────────────────────────────

class OptionsStrikeOut(_UtcAwareTimestamps):
    strike:   float
    call_oi:  int
    put_oi:   int
    call_ltp: Optional[float]
    put_ltp:  Optional[float]


class OptionsChainDetailOut(_UtcAwareTimestamps):
    spot_price:        Optional[float]
    expiry_date:       Optional[date]
    pcr:               Optional[float]
    max_pain:          Optional[float]
    support_levels:    Optional[list]
    resistance_levels: Optional[list]
    options_score:     Optional[float]
    chain_data:        list[OptionsStrikeOut]


# ── Mutual fund list (simplified) ─────────────────────────────────────────────

class MutualFundBriefOut(_UtcAwareTimestamps):
    scheme_code:        str
    name:               str
    nav:                float
    change_pct:         float
    one_month_return:   Optional[float]
    one_yr_return:      Optional[float]
    three_year_return:  Optional[float]
    signal:             str
    category:           str


class MutualFundListOut(_UtcAwareTimestamps):
    funds: list[MutualFundBriefOut]


# ── SIP brief ─────────────────────────────────────────────────────────────────

class SIPBriefOut(_UtcAwareTimestamps):
    total_invested:  float
    current_value:   float
    cagr:            float
    absolute_return: float


# ── Sector performance ────────────────────────────────────────────────────────

class SectorPerfItem(_UtcAwareTimestamps):
    name:         str
    return_30d:   Optional[float]
    vs_nifty_pct: Optional[float]
    signal:       str   # OUTPERFORM | UNDERPERFORM | NEUTRAL


class SectorPerfOut(_UtcAwareTimestamps):
    sectors: list[SectorPerfItem]


# ── Seed result ───────────────────────────────────────────────────────────────

class SeedResultOut(_UtcAwareTimestamps):
    status:             str
    symbols_fetched:    int
    candles_saved:      int
    signals_generated:  int
    actionable_signals: int
    duration_seconds:   float
    symbols_analysed:   Optional[int] = None
    candles_available:  Optional[int] = None


# ── Backtest ──────────────────────────────────────────────────────────────────

class BacktestRequestIn(_UtcAwareTimestamps):
    symbols:          Optional[list[str]] = None   # None → all NSE watchlist symbols
    timeframe:        str                 = "1d"
    atr_multiplier:   float               = 2.0
    risk_reward:      float               = 2.0
    commission_pct:   float               = 0.001
    slippage_pct:     float               = 0.0005
    initial_capital:  float               = 100_000.0
    lookback_candles: int                 = 200


class BacktestSymbolResultOut(_UtcAwareTimestamps):
    symbol:           str
    timeframe:        str
    total_trades:     int
    winning_trades:   int
    losing_trades:    int
    win_rate:         float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio:     Optional[float]
    avg_win_pct:      float
    avg_loss_pct:     float
    profit_factor:    Optional[float]
    equity_curve:     list[float]


class BacktestResultOut(_UtcAwareTimestamps):
    symbols_tested:   int
    timeframe:        str
    total_trades:     int
    avg_win_rate:     float
    avg_return_pct:   float
    avg_sharpe:       float
    best_symbols:     list[BacktestSymbolResultOut]
    worst_symbols:    list[BacktestSymbolResultOut]
    all_results:      list[BacktestSymbolResultOut]
    duration_seconds: float
