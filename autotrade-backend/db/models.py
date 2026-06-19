import uuid
from datetime import date, datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Enum, Float, ForeignKey,
    Index, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.database import Base


# ── Enums ─────────────────────────────────────────────────────────────────────

class TradeDirection(str, PyEnum):
    BUY  = "BUY"
    SELL = "SELL"


class TradeStatus(str, PyEnum):
    OPEN    = "OPEN"
    CLOSED  = "CLOSED"
    STOPPED = "STOPPED"   # stopped-out via stop-loss


class SignalType(str, PyEnum):
    BUY  = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


# ── 1. VirtualWallet ─────────────────────────────────────────────────────────

class VirtualWallet(Base):
    """Single-row table that tracks the paper-trading cash state.

    NOTE: all monetary values are VIRTUAL — no real money is represented.
    """
    __tablename__ = "virtual_wallet"

    id: Mapped[int]   = mapped_column(Integer, primary_key=True, autoincrement=True)
    balance:          Mapped[float] = mapped_column(Float, default=1000.0, nullable=False)
    equity:           Mapped[float] = mapped_column(Float, default=1000.0, nullable=False)
    realised_pnl:     Mapped[float] = mapped_column(Float, default=0.0,    nullable=False)
    unrealised_pnl:   Mapped[float] = mapped_column(Float, default=0.0,    nullable=False)
    total_trades:     Mapped[int]   = mapped_column(Integer, default=0,    nullable=False)
    winning_trades:   Mapped[int]   = mapped_column(Integer, default=0,    nullable=False)
    peak_balance:     Mapped[float] = mapped_column(Float, default=1000.0, nullable=False)
    max_drawdown:     Mapped[float] = mapped_column(Float, default=0.0,    nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<VirtualWallet id={self.id} balance=${self.balance:,.2f} "
            f"equity=${self.equity:,.2f} pnl=${self.realised_pnl:,.2f}>"
        )


# ── 2. PaperTrade ─────────────────────────────────────────────────────────────

class PaperTrade(Base):
    """Full lifecycle record of a single paper trade (entry → exit)."""
    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    symbol:    Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    direction: Mapped[TradeDirection] = mapped_column(Enum(TradeDirection), nullable=False)
    status:    Mapped[TradeStatus]    = mapped_column(
        Enum(TradeStatus), default=TradeStatus.OPEN, nullable=False, index=True
    )

    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price:  Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss:   Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    size_units:  Mapped[float] = mapped_column(Float, nullable=False)
    size_usd:    Mapped[float] = mapped_column(Float, nullable=False)

    # ── F&O fields (EQUITY for cash; populated for FUTURE/CE/PE) ──────────────
    instrument_type:     Mapped[str]          = mapped_column(String(10), nullable=False, default="EQUITY")
    underlying_symbol:   Mapped[str | None]   = mapped_column(String(30), nullable=True)
    strike_price:        Mapped[float | None] = mapped_column(Float, nullable=True)
    option_type:         Mapped[str | None]   = mapped_column(String(2),  nullable=True)  # CE | PE
    expiry_date:         Mapped[date | None]  = mapped_column(Date, nullable=True)
    lot_size:            Mapped[int]          = mapped_column(Integer, nullable=False, default=1)
    contract_multiplier: Mapped[float]        = mapped_column(Float, nullable=False, default=1.0)
    margin_blocked:      Mapped[float]        = mapped_column(Float, nullable=False, default=0.0)

    pnl:         Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_percent: Mapped[float | None] = mapped_column(Float, nullable=True)

    ai_reason:             Mapped[str]        = mapped_column(Text,    nullable=False, default="")
    signal_confidence:     Mapped[float]      = mapped_column(Float,   nullable=False, default=0.0)
    pattern_name:          Mapped[str]        = mapped_column(String(80), nullable=False, default="")
    indicator_snapshot:    Mapped[dict | None] = mapped_column(JSON,   nullable=True)
    news_sentiment_score:  Mapped[float]      = mapped_column(Float,   nullable=False, default=0.0)
    slippage_applied:      Mapped[float]      = mapped_column(Float,   nullable=False, default=0.0)

    opened_at: Mapped[datetime]      = mapped_column(DateTime, server_default=func.now(), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ── Trade attribution (Phase 1 — added 2026-06-17) ───────────────────────
    # Entry snapshot — populated when the trade opens
    strategy_name:      Mapped[str | None]   = mapped_column(String(40),  nullable=True)
    regime_at_entry:    Mapped[str | None]   = mapped_column(String(20),  nullable=True)
    entry_reason:       Mapped[str | None]   = mapped_column(String(40),  nullable=True)
    confidence_bucket:  Mapped[str | None]   = mapped_column(String(8),   nullable=True)
    instrument_segment: Mapped[str | None]   = mapped_column(String(12),  nullable=True)
    initial_risk_inr:   Mapped[float | None] = mapped_column(Float, nullable=True)
    # Exit snapshot — populated when the trade closes
    exit_reason:        Mapped[str | None]   = mapped_column(String(20),  nullable=True)
    regime_at_exit:     Mapped[str | None]   = mapped_column(String(20),  nullable=True)
    r_multiple:         Mapped[float | None] = mapped_column(Float, nullable=True)
    holding_bars:       Mapped[int | None]   = mapped_column(Integer, nullable=True)
    holding_hours:      Mapped[float | None] = mapped_column(Float, nullable=True)
    # Excursion summary — populated at close from running peak/trough in trade_mgmt
    mfe_abs:            Mapped[float | None] = mapped_column(Float, nullable=True)
    mfe_pct:            Mapped[float | None] = mapped_column(Float, nullable=True)
    mfe_r:              Mapped[float | None] = mapped_column(Float, nullable=True)
    mae_abs:            Mapped[float | None] = mapped_column(Float, nullable=True)
    mae_pct:            Mapped[float | None] = mapped_column(Float, nullable=True)
    mae_r:              Mapped[float | None] = mapped_column(Float, nullable=True)
    max_open_profit:    Mapped[float | None] = mapped_column(Float, nullable=True)

    open_position: Mapped["OpenPosition | None"] = relationship(
        "OpenPosition", back_populates="trade", uselist=False
    )

    def __repr__(self) -> str:
        return (
            f"<PaperTrade id={self.id} {self.direction.value} {self.symbol} "
            f"@{self.entry_price} status={self.status.value}>"
        )


# ── 3. OpenPosition ──────────────────────────────────────────────────────────

class OpenPosition(Base):
    """Live snapshot of a currently open position; deleted on close."""
    __tablename__ = "open_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    symbol:        Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    direction:     Mapped[TradeDirection] = mapped_column(Enum(TradeDirection), nullable=False)
    entry_price:   Mapped[float] = mapped_column(Float, nullable=False)
    current_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss:     Mapped[float] = mapped_column(Float, nullable=False)
    take_profit:   Mapped[float] = mapped_column(Float, nullable=False)
    size_units:    Mapped[float] = mapped_column(Float, nullable=False)
    size_usd:      Mapped[float] = mapped_column(Float, nullable=False)

    # ── F&O fields ────────────────────────────────────────────────────────────
    instrument_type:     Mapped[str]          = mapped_column(String(10), nullable=False, default="EQUITY")
    underlying_symbol:   Mapped[str | None]   = mapped_column(String(30), nullable=True)
    strike_price:        Mapped[float | None] = mapped_column(Float, nullable=True)
    option_type:         Mapped[str | None]   = mapped_column(String(2),  nullable=True)
    expiry_date:         Mapped[date | None]  = mapped_column(Date, nullable=True)
    lot_size:            Mapped[int]          = mapped_column(Integer, nullable=False, default=1)
    contract_multiplier: Mapped[float]        = mapped_column(Float, nullable=False, default=1.0)
    margin_blocked:      Mapped[float]        = mapped_column(Float, nullable=False, default=0.0)

    # CNC = delivery positional; MIS = intraday (must squareoff by 15:20 IST)
    product: Mapped[str] = mapped_column(String(10), nullable=False, default="CNC")

    unrealised_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unrealised_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    trade_id: Mapped[int] = mapped_column(
        ForeignKey("paper_trades.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    trade: Mapped["PaperTrade"] = relationship("PaperTrade", back_populates="open_position")

    opened_at:    Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        sign = "+" if self.unrealised_pnl >= 0 else ""
        return (
            f"<OpenPosition id={self.id} {self.direction.value} {self.symbol} "
            f"pnl={sign}{self.unrealised_pnl:,.2f} ({sign}{self.unrealised_pct:.2f}%)>"
        )


# ── 4. Candle ─────────────────────────────────────────────────────────────────

class Candle(Base):
    """OHLCV candle bar cached from yfinance / Twelve Data."""
    __tablename__ = "candles"
    __table_args__ = (
        Index("ix_candles_symbol_timestamp", "symbol", "timestamp"),
        Index("ix_candles_symbol_timeframe",  "symbol", "timeframe"),
        UniqueConstraint("symbol", "timeframe", "timestamp", name="uq_candle_bar"),
    )

    id:        Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol:    Mapped[str]      = mapped_column(String(20),  nullable=False)
    timeframe: Mapped[str]      = mapped_column(String(10),  nullable=False)
    open:      Mapped[float]    = mapped_column(Float, nullable=False)
    high:      Mapped[float]    = mapped_column(Float, nullable=False)
    low:       Mapped[float]    = mapped_column(Float, nullable=False)
    close:     Mapped[float]    = mapped_column(Float, nullable=False)
    volume:    Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<Candle {self.symbol}/{self.timeframe} "
            f"O={self.open} H={self.high} L={self.low} C={self.close} "
            f"@{self.timestamp}>"
        )


# ── 5. Signal ─────────────────────────────────────────────────────────────────

class Signal(Base):
    """Confluence signal generated by the AI engine."""
    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_symbol_created", "symbol", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    symbol:          Mapped[str]        = mapped_column(String(20),  nullable=False, index=True)
    timeframe:       Mapped[str]        = mapped_column(String(10),  nullable=False)
    signal_type:     Mapped[SignalType] = mapped_column(Enum(SignalType), nullable=False)
    confidence:      Mapped[float]      = mapped_column(Float, nullable=False)   # 0–100
    pattern_name:    Mapped[str]        = mapped_column(String(80),  nullable=False, default="")
    indicators_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    news_sentiment:  Mapped[float]      = mapped_column(Float, nullable=False, default=0.0)
    final_score:     Mapped[float]      = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<Signal id={self.id} {self.signal_type.value} {self.symbol}/{self.timeframe} "
            f"score={self.final_score:.1f} conf={self.confidence:.1f}%>"
        )


# ── 6. NewsItem ───────────────────────────────────────────────────────────────

class NewsItem(Base):
    """Crawled news headline with FinBERT sentiment score."""
    __tablename__ = "news_items"
    __table_args__ = (
        Index("ix_news_published", "published_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    headline:         Mapped[str]         = mapped_column(Text,      nullable=False)
    source:           Mapped[str]         = mapped_column(String(80), nullable=False)
    url:              Mapped[str | None]  = mapped_column(Text,      nullable=True)
    sentiment:        Mapped[str | None]  = mapped_column(String(20), nullable=True)   # positive/negative/neutral
    score:            Mapped[float]       = mapped_column(Float, nullable=False, default=0.0)  # -1 to 1
    # JSONB so the @> containment operator can use the ix_news_tickers_gin
    # GIN index. Migrated from JSON in commit "post-ticker-expansion cleanup".
    tickers_affected: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    published_at:     Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    crawled_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<NewsItem id={self.id} source={self.source!r} "
            f"sentiment={self.sentiment} score={self.score:+.2f} "
            f"headline={self.headline[:40]!r}>"
        )


# ── 7. FIIDIIFlow ─────────────────────────────────────────────────────────────

class FIIDIIFlow(Base):
    """Daily institutional flow data from NSE, values in INR Crores."""
    __tablename__ = "fii_dii_flows"
    __table_args__ = (
        Index("ix_fii_dii_flows_date", "date", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    date: Mapped[date] = mapped_column(Date, nullable=False)
    fii_net_buy: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    dii_net_buy: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fii_gross_buy: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fii_gross_sell: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    dii_gross_buy: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    dii_gross_sell: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    market_direction: Mapped[str] = mapped_column(String(10), nullable=False, default="NEUTRAL")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<FIIDIIFlow date={self.date} "
            f"fii={self.fii_net_buy:+,.2f}Cr dii={self.dii_net_buy:+,.2f}Cr "
            f"direction={self.market_direction}>"
        )


# ── 8. OptionsChainSnapshot ───────────────────────────────────────────────────

class OptionsChainSnapshot(Base):
    """NSE index options-chain snapshot for PCR, max pain, and OI levels."""
    __tablename__ = "options_chain_snapshots"
    __table_args__ = (
        Index("ix_options_chain_symbol_expiry", "symbol", "expiry_date"),
        Index("ix_options_chain_snapshot_at", "snapshot_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    atm_strike: Mapped[float] = mapped_column(Float, nullable=False)
    pcr: Mapped[float] = mapped_column(Float, nullable=False)
    max_pain: Mapped[float] = mapped_column(Float, nullable=False)
    total_call_oi: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_put_oi: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    support_levels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    resistance_levels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<OptionsChainSnapshot {self.symbol} expiry={self.expiry_date} "
            f"atm={self.atm_strike} pcr={self.pcr:.2f} max_pain={self.max_pain}>"
        )


# ── 8b. OptionContractSnapshot — per-strike with Greeks/IV ────────────────────

class OptionContractSnapshot(Base):
    """Per-strike option snapshot with computed IV + Greeks (Black-Scholes).

    Append-only: one row per strike per option_type per analysis tick. Powers the
    symbol-aware options factor, the chain viewer, and volatility strategies.
    """
    __tablename__ = "option_contract_snapshots"
    __table_args__ = (
        Index("ix_option_contract_under_expiry", "underlying", "expiry_date"),
        Index("ix_option_contract_snapshot_at", "snapshot_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    underlying:   Mapped[str]   = mapped_column(String(30), nullable=False)   # "NIFTY", "RELIANCE"
    expiry_date:  Mapped[date]  = mapped_column(Date, nullable=False)
    strike:       Mapped[float] = mapped_column(Float, nullable=False)
    option_type:  Mapped[str]   = mapped_column(String(2), nullable=False)    # "CE" | "PE"
    spot:         Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ltp:          Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    oi:           Mapped[int]   = mapped_column(BigInteger, nullable=False, default=0)
    oi_change:    Mapped[int]   = mapped_column(BigInteger, nullable=False, default=0)
    volume:       Mapped[int]   = mapped_column(BigInteger, nullable=False, default=0)
    iv:           Mapped[float | None] = mapped_column(Float, nullable=True)
    delta:        Mapped[float | None] = mapped_column(Float, nullable=True)
    gamma:        Mapped[float | None] = mapped_column(Float, nullable=True)
    theta:        Mapped[float | None] = mapped_column(Float, nullable=True)
    vega:         Mapped[float | None] = mapped_column(Float, nullable=True)
    rho:          Mapped[float | None] = mapped_column(Float, nullable=True)
    snapshot_at:  Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<OptionContractSnapshot {self.underlying} {self.strike}{self.option_type} "
            f"exp={self.expiry_date} iv={self.iv} delta={self.delta}>"
        )


# ── 8c. IVHistory — daily ATM IV for IV-Rank / IV-Percentile ──────────────────

class IVHistory(Base):
    """Daily ATM implied-volatility per underlying, for IV-Rank / IV-Percentile.

    One row per underlying per trading day (the latest tick of the day wins).
    """
    __tablename__ = "iv_history"
    __table_args__ = (
        UniqueConstraint("underlying", "trade_date", name="uq_iv_history_under_date"),
        Index("ix_iv_history_under", "underlying"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    underlying:  Mapped[str]   = mapped_column(String(30), nullable=False)
    trade_date:  Mapped[date]  = mapped_column(Date, nullable=False)
    atm_iv:      Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at:  Mapped[datetime] = mapped_column(DateTime, server_default=func.now(),
                                                  onupdate=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<IVHistory {self.underlying} {self.trade_date} atm_iv={self.atm_iv:.3f}>"


# ── 9. SimulationLog ──────────────────────────────────────────────────────────

class SimulationLog(Base):
    """Append-only audit log of every AI decision for post-analysis."""
    __tablename__ = "simulation_logs"
    __table_args__ = (
        Index("ix_simlog_symbol_ts", "symbol", "timestamp"),
    )

    id:         Mapped[int]          = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str]          = mapped_column(String(30), nullable=False, index=True)
    symbol:     Mapped[str]          = mapped_column(String(50), nullable=False)
    message:    Mapped[str]          = mapped_column(Text,       nullable=False)
    data:       Mapped[dict | None]  = mapped_column(JSON,       nullable=True)
    timestamp:  Mapped[datetime]     = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<SimulationLog id={self.id} [{self.event_type}] "
            f"{self.symbol} @{self.timestamp} — {self.message[:60]!r}>"
        )


# ── 10. PerformanceSnapshot ───────────────────────────────────────────────────

class PerformanceSnapshot(Base):
    """Daily equity-curve data point, saved once per calendar day."""
    __tablename__ = "performance_snapshots"
    __table_args__ = (
        UniqueConstraint("date", name="uq_perf_date"),
    )

    id:             Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    date:           Mapped[date]     = mapped_column(Date,  nullable=False, index=True)
    balance:        Mapped[float]    = mapped_column(Float, nullable=False)
    equity:         Mapped[float]    = mapped_column(Float, nullable=False)
    daily_pnl:      Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)
    trades_today:   Mapped[int]      = mapped_column(Integer, nullable=False, default=0)
    win_rate_today: Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)
    snapshot_at:    Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<PerformanceSnapshot date={self.date} balance=${self.balance:,.2f} "
            f"equity=${self.equity:,.2f} daily_pnl={self.daily_pnl:+,.2f} "
            f"trades={self.trades_today} win_rate={self.win_rate_today:.1f}%>"
        )


# ── 11. RuntimeSettings ───────────────────────────────────────────────────────

class RuntimeSettings(Base):
    """Key-value store for runtime-configurable parameters.

    Updated via /api/v1/settings; read by API workers and Celery tasks.
    Values are JSON-encoded so any scalar, list, or bool is supported.
    """
    __tablename__ = "runtime_settings"

    key:        Mapped[str]      = mapped_column(String(80),  primary_key=True)
    value:      Mapped[str]      = mapped_column(Text,         nullable=False)   # JSON
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<RuntimeSettings key={self.key!r} value={self.value!r}>"


# ── 12. MutualFundNAV ─────────────────────────────────────────────────────────

class MutualFundNAV(Base):
    """Daily NAV snapshot for a mutual fund scheme with computed returns."""
    __tablename__ = "mutual_fund_navs"
    __table_args__ = (
        Index("ix_mf_nav_scheme_recorded", "scheme_code", "recorded_at"),
    )

    id:                 Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    scheme_code:        Mapped[str]           = mapped_column(String(20),  nullable=False)
    scheme_name:        Mapped[str]           = mapped_column(String(200), nullable=False)
    nav:                Mapped[float]         = mapped_column(Float, nullable=False)
    prev_nav:           Mapped[float]         = mapped_column(Float, nullable=False, default=0.0)
    change:             Mapped[float]         = mapped_column(Float, nullable=False, default=0.0)
    change_pct:         Mapped[float]         = mapped_column(Float, nullable=False, default=0.0)
    category:           Mapped[str]           = mapped_column(String(120), nullable=False, default="")
    one_month_return:   Mapped[float | None]  = mapped_column(Float, nullable=True)
    three_month_return: Mapped[float | None]  = mapped_column(Float, nullable=True)
    one_year_return:    Mapped[float | None]  = mapped_column(Float, nullable=True)
    three_year_return:  Mapped[float | None]  = mapped_column(Float, nullable=True)
    recorded_at:        Mapped[datetime]      = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<MutualFundNAV {self.scheme_code} nav={self.nav} "
            f"1y={self.one_year_return}% @{self.recorded_at.date()}>"
        )


# ── 13. FundamentalData ───────────────────────────────────────────────────────

class FundamentalData(Base):
    """Weekly fundamental snapshot for an NSE-listed stock.

    One row per symbol — updated in-place each weekly run.
    Sources: yfinance (PE/ROE/D·E) + Screener.in (ROCE/promoter/pledged/growth).
    """
    __tablename__ = "fundamental_data"

    id:                 Mapped[int]          = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol:             Mapped[str]          = mapped_column(String(30),  nullable=False, unique=True)
    company_name:       Mapped[str]          = mapped_column(String(200), nullable=False, default="")
    pe_ratio:           Mapped[float | None] = mapped_column(Float, nullable=True)
    pb_ratio:           Mapped[float | None] = mapped_column(Float, nullable=True)
    roe:                Mapped[float | None] = mapped_column(Float, nullable=True)   # %
    roce:               Mapped[float | None] = mapped_column(Float, nullable=True)   # %
    debt_to_equity:     Mapped[float | None] = mapped_column(Float, nullable=True)
    current_ratio:      Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_growth_3yr: Mapped[float | None] = mapped_column(Float, nullable=True)  # %
    profit_growth_3yr:  Mapped[float | None] = mapped_column(Float, nullable=True)  # %
    promoter_holding:   Mapped[float | None] = mapped_column(Float, nullable=True)  # %
    fii_holding:        Mapped[float | None] = mapped_column(Float, nullable=True)  # %
    pledged_pct:        Mapped[float | None] = mapped_column(Float, nullable=True)  # %
    market_cap_cr:      Mapped[float | None] = mapped_column(Float, nullable=True)  # INR Crores
    dividend_yield:     Mapped[float | None] = mapped_column(Float, nullable=True)  # %
    fundamental_score:  Mapped[float | None] = mapped_column(Float, nullable=True)  # 0–100
    last_updated:       Mapped[datetime]     = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<FundamentalData {self.symbol} score={self.fundamental_score} "
            f"pe={self.pe_ratio} roe={self.roe}% roce={self.roce}%>"
        )


# ── 14. KiteSession ───────────────────────────────────────────────────────────

class KiteSession(Base):
    """Zerodha KiteConnect OAuth session — read-only portfolio tracking.

    One active row per user (default='default').  Access tokens expire daily
    at 06:00 IST; the `is_active` flag is set to False on expiry or disconnect.
    """
    __tablename__ = "kite_sessions"

    id:           Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id:      Mapped[str]      = mapped_column(String(50),  nullable=False, default="default")
    access_token: Mapped[str]      = mapped_column(String(500), nullable=False)
    public_token: Mapped[str | None] = mapped_column(String(500), nullable=True)
    login_time:   Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at:   Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_active:    Mapped[bool]     = mapped_column(Boolean,  nullable=False, default=True)
    created_at:   Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<KiteSession id={self.id} user={self.user_id!r} "
            f"active={self.is_active} expires={self.expires_at}>"
        )


# ── 15. PortfolioHolding ──────────────────────────────────────────────────────

class PortfolioHolding(Base):
    """Zerodha Kite portfolio holding — synced from the user's real Demat account.

    NOTE: This stores READ-ONLY reference data from a real account for
    analysis and display purposes.  No orders are ever placed by this system.
    """
    __tablename__ = "portfolio_holdings"
    __table_args__ = (
        UniqueConstraint("tradingsymbol", "exchange", name="uq_holding"),
        Index("ix_holding_symbol", "tradingsymbol"),
    )

    id:              Mapped[int]          = mapped_column(Integer, primary_key=True, autoincrement=True)
    tradingsymbol:   Mapped[str]          = mapped_column(String(30),  nullable=False)
    exchange:        Mapped[str]          = mapped_column(String(10),  nullable=False)
    isin:            Mapped[str | None]   = mapped_column(String(20),  nullable=True)
    quantity:        Mapped[int]          = mapped_column(Integer,     nullable=False, default=0)
    avg_price:       Mapped[float]        = mapped_column(Float,       nullable=False, default=0.0)
    last_price:      Mapped[float]        = mapped_column(Float,       nullable=False, default=0.0)
    current_value:   Mapped[float]        = mapped_column(Float,       nullable=False, default=0.0)
    pnl:             Mapped[float]        = mapped_column(Float,       nullable=False, default=0.0)
    pnl_pct:         Mapped[float]        = mapped_column(Float,       nullable=False, default=0.0)
    day_change:      Mapped[float]        = mapped_column(Float,       nullable=False, default=0.0)
    day_change_pct:  Mapped[float]        = mapped_column(Float,       nullable=False, default=0.0)
    sector:          Mapped[str]          = mapped_column(String(80),  nullable=False, default="")
    buy_date:        Mapped[date | None]  = mapped_column(Date,        nullable=True)
    xirr:            Mapped[float | None] = mapped_column(Float,       nullable=True)
    synced_at:       Mapped[datetime]     = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<PortfolioHolding {self.tradingsymbol} qty={self.quantity} "
            f"avg={self.avg_price:.2f} ltp={self.last_price:.2f} "
            f"pnl={self.pnl:+.2f} ({self.pnl_pct:+.1f}%)>"
        )


# ── 16. ZerodhaPosition ───────────────────────────────────────────────────────

class ZerodhaPosition(Base):
    """Intraday and overnight positions from Zerodha KiteConnect API.

    Synced from GET /portfolio/positions. One row per tradingsymbol/product.
    Completely replaced on each sync (positions change through the day).
    """
    __tablename__ = "zerodha_positions"
    __table_args__ = (
        UniqueConstraint("tradingsymbol", "exchange", "product", "position_type", name="uq_zerodha_pos"),
        Index("ix_zerodha_pos_symbol", "tradingsymbol"),
    )

    id:              Mapped[int]   = mapped_column(Integer, primary_key=True, autoincrement=True)
    tradingsymbol:   Mapped[str]   = mapped_column(String(30),  nullable=False)
    exchange:        Mapped[str]   = mapped_column(String(10),  nullable=False)
    product:         Mapped[str]   = mapped_column(String(10),  nullable=False)  # CNC, MIS, NRML
    position_type:   Mapped[str]   = mapped_column(String(10),  nullable=False)  # day | net
    quantity:        Mapped[int]   = mapped_column(Integer,     nullable=False, default=0)
    buy_quantity:    Mapped[int]   = mapped_column(Integer,     nullable=False, default=0)
    sell_quantity:   Mapped[int]   = mapped_column(Integer,     nullable=False, default=0)
    average_price:   Mapped[float] = mapped_column(Float,       nullable=False, default=0.0)
    last_price:      Mapped[float] = mapped_column(Float,       nullable=False, default=0.0)
    pnl:             Mapped[float] = mapped_column(Float,       nullable=False, default=0.0)
    m2m:             Mapped[float] = mapped_column(Float,       nullable=False, default=0.0)  # mark-to-market
    value:           Mapped[float] = mapped_column(Float,       nullable=False, default=0.0)
    multiplier:      Mapped[float] = mapped_column(Float,       nullable=False, default=1.0)
    synced_at:       Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<ZerodhaPosition {self.tradingsymbol} {self.position_type} "
            f"qty={self.quantity} pnl={self.pnl:+.2f}>"
        )


# ── 17. KiteInstrument ────────────────────────────────────────────────────────

class KiteInstrument(Base):
    """Instrument master downloaded daily from GET /instruments/NSE.

    Used to resolve trading_symbol → instrument_token for historical data
    and WebSocket subscriptions.  Refreshed daily at 08:00 IST before market open.
    """
    __tablename__ = "kite_instruments"
    __table_args__ = (
        UniqueConstraint("instrument_token", name="uq_kite_instrument_token"),
        Index("ix_kite_instrument_symbol", "tradingsymbol"),
    )

    id:               Mapped[int]          = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_token: Mapped[int]          = mapped_column(Integer,     nullable=False)
    exchange_token:   Mapped[int]          = mapped_column(Integer,     nullable=False, default=0)
    tradingsymbol:    Mapped[str]          = mapped_column(String(30),  nullable=False)
    name:             Mapped[str]          = mapped_column(String(200), nullable=False, default="")
    last_price:       Mapped[float]        = mapped_column(Float,       nullable=False, default=0.0)
    expiry:           Mapped[str]          = mapped_column(String(20),  nullable=False, default="")
    strike:           Mapped[float]        = mapped_column(Float,       nullable=False, default=0.0)
    tick_size:        Mapped[float]        = mapped_column(Float,       nullable=False, default=0.05)
    lot_size:         Mapped[int]          = mapped_column(Integer,     nullable=False, default=1)
    instrument_type:  Mapped[str]          = mapped_column(String(10),  nullable=False, default="EQ")
    segment:          Mapped[str]          = mapped_column(String(10),  nullable=False, default="NSE")
    exchange:         Mapped[str]          = mapped_column(String(10),  nullable=False, default="NSE")
    refreshed_at:     Mapped[datetime]     = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<KiteInstrument {self.exchange}:{self.tradingsymbol} "
            f"token={self.instrument_token} type={self.instrument_type}>"
        )


# ── 18. MarketEvent ───────────────────────────────────────────────────────────

class MarketEvent(Base):
    """Indian market calendar event — IPO, earnings, RBI MPC, F&O expiry, holidays."""
    __tablename__ = "market_events"
    __table_args__ = (
        Index("ix_market_events_date",        "event_date"),
        Index("ix_market_events_type_date",   "event_type", "event_date"),
        Index("ix_market_events_symbol_date", "symbol",     "event_date"),
    )

    id:           Mapped[str]          = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type:   Mapped[str]          = mapped_column(String(30),  nullable=False, index=True)
    title:        Mapped[str]          = mapped_column(String(200), nullable=False)
    symbol:       Mapped[str | None]   = mapped_column(String(30),  nullable=True,  index=True)
    company_name: Mapped[str | None]   = mapped_column(String(200), nullable=True)
    event_date:   Mapped[date]         = mapped_column(Date,        nullable=False)
    start_date:   Mapped[date | None]  = mapped_column(Date,        nullable=True)
    end_date:     Mapped[date | None]  = mapped_column(Date,        nullable=True)
    time_ist:     Mapped[str | None]   = mapped_column(String(20),  nullable=True)
    description:  Mapped[str | None]   = mapped_column(Text,        nullable=True)
    importance:   Mapped[str]          = mapped_column(String(10),  nullable=False, default="MEDIUM")
    source:       Mapped[str]          = mapped_column(String(30),  nullable=False, default="HARDCODED")
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    is_confirmed: Mapped[bool]         = mapped_column(Boolean,     nullable=False, default=True)
    created_at:   Mapped[datetime]     = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at:   Mapped[datetime]     = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<MarketEvent {self.event_type} {self.event_date} "
            f"{self.title[:40]!r} importance={self.importance}>"
        )


# ── 19. TrackerPortfolio ──────────────────────────────────────────────────────

class TrackerPortfolio(Base):
    """User-defined personal portfolio for tracking real stock holdings and XIRR."""
    __tablename__ = "tracker_portfolios"

    id:          Mapped[str]      = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name:        Mapped[str]      = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency:    Mapped[str]      = mapped_column(String(5),  nullable=False, default="INR")
    is_active:   Mapped[bool]     = mapped_column(Boolean,    nullable=False, default=True)
    created_at:  Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at:  Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    holdings:     Mapped[list["TrackerHolding"]]     = relationship("TrackerHolding",     back_populates="portfolio", cascade="all, delete-orphan")
    transactions: Mapped[list["TrackerTransaction"]] = relationship("TrackerTransaction", back_populates="portfolio", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<TrackerPortfolio id={self.id} name={self.name!r}>"


# ── 20. TrackerHolding ────────────────────────────────────────────────────────

class TrackerHolding(Base):
    """Current holding in a personal tracker portfolio — one row per symbol."""
    __tablename__ = "tracker_holdings"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "symbol", name="uq_tracker_holding_portfolio_symbol"),
        Index("ix_tracker_holding_portfolio", "portfolio_id"),
        Index("ix_tracker_holding_symbol",    "symbol"),
    )

    id:             Mapped[str]        = mapped_column(String(36),  primary_key=True, default=lambda: str(uuid.uuid4()))
    portfolio_id:   Mapped[str]        = mapped_column(String(36),  ForeignKey("tracker_portfolios.id", ondelete="CASCADE"), nullable=False)
    symbol:         Mapped[str]        = mapped_column(String(30),  nullable=False)
    company_name:   Mapped[str]        = mapped_column(String(200), nullable=False, default="")
    sector:         Mapped[str]        = mapped_column(String(80),  nullable=False, default="")
    quantity:       Mapped[float]      = mapped_column(Float,       nullable=False)
    avg_buy_price:  Mapped[float]      = mapped_column(Float,       nullable=False)
    first_buy_date: Mapped[date]       = mapped_column(Date,        nullable=False)
    notes:          Mapped[str | None] = mapped_column(Text,        nullable=True)
    created_at:     Mapped[datetime]   = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at:     Mapped[datetime]   = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    portfolio:    Mapped["TrackerPortfolio"]         = relationship("TrackerPortfolio", back_populates="holdings")
    transactions: Mapped[list["TrackerTransaction"]] = relationship("TrackerTransaction", back_populates="holding", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<TrackerHolding {self.symbol} qty={self.quantity} avg={self.avg_buy_price:.2f}>"


# ── 21. TrackerTransaction ────────────────────────────────────────────────────

class TrackerTransaction(Base):
    """Buy/sell transaction record for a personal portfolio holding."""
    __tablename__ = "tracker_transactions"
    __table_args__ = (
        Index("ix_tracker_tx_portfolio", "portfolio_id"),
        Index("ix_tracker_tx_symbol",    "symbol"),
        Index("ix_tracker_tx_date",      "trade_date"),
    )

    id:           Mapped[str]        = mapped_column(String(36),  primary_key=True, default=lambda: str(uuid.uuid4()))
    portfolio_id: Mapped[str]        = mapped_column(String(36),  ForeignKey("tracker_portfolios.id", ondelete="CASCADE"), nullable=False)
    holding_id:   Mapped[str | None] = mapped_column(String(36),  ForeignKey("tracker_holdings.id",   ondelete="SET NULL"), nullable=True)
    symbol:       Mapped[str]        = mapped_column(String(30),  nullable=False)
    company_name: Mapped[str]        = mapped_column(String(200), nullable=False, default="")
    tx_type:      Mapped[str]        = mapped_column(String(10),  nullable=False)   # BUY | SELL
    quantity:     Mapped[float]      = mapped_column(Float,       nullable=False)
    price:        Mapped[float]      = mapped_column(Float,       nullable=False)
    total_amount: Mapped[float]      = mapped_column(Float,       nullable=False)
    brokerage:    Mapped[float]      = mapped_column(Float,       nullable=False, default=0.0)
    stt:          Mapped[float]      = mapped_column(Float,       nullable=False, default=0.0)
    trade_date:   Mapped[date]       = mapped_column(Date,        nullable=False)
    notes:        Mapped[str | None] = mapped_column(Text,        nullable=True)
    created_at:   Mapped[datetime]   = mapped_column(DateTime, server_default=func.now(), nullable=False)

    portfolio: Mapped["TrackerPortfolio"]      = relationship("TrackerPortfolio", back_populates="transactions")
    holding:   Mapped["TrackerHolding | None"] = relationship("TrackerHolding",   back_populates="transactions")

    def __repr__(self) -> str:
        return f"<TrackerTransaction {self.tx_type} {self.symbol} qty={self.quantity} @{self.price:.2f} on {self.trade_date}>"


# ── 22. UserMutualFund ────────────────────────────────────────────────────────

class UserMutualFund(Base):
    """A mutual fund scheme the user has added to their personal tracker."""
    __tablename__ = "user_mutual_funds"

    id:          Mapped[str]      = mapped_column(String(36),  primary_key=True, default=lambda: str(uuid.uuid4()))
    scheme_code: Mapped[str]      = mapped_column(String(30),  nullable=False, unique=True)
    scheme_name: Mapped[str]      = mapped_column(String(300), nullable=False)
    category:    Mapped[str]      = mapped_column(String(80),  nullable=False, default="")
    added_at:    Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    sips: Mapped[list["UserSIP"]] = relationship("UserSIP", back_populates="fund", cascade="all, delete-orphan")


# ── 23. UserSIP ───────────────────────────────────────────────────────────────

class UserSIP(Base):
    """A Systematic Investment Plan entry linked to a user-tracked fund."""
    __tablename__ = "user_sips"

    id:             Mapped[str]        = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    fund_id:        Mapped[str]        = mapped_column(String(36), ForeignKey("user_mutual_funds.id", ondelete="CASCADE"), nullable=False)
    scheme_code:    Mapped[str]        = mapped_column(String(30), nullable=False)
    monthly_amount: Mapped[float]      = mapped_column(Float, nullable=False)
    start_date:     Mapped[date]       = mapped_column(Date,  nullable=False)
    status:         Mapped[str]        = mapped_column(String(10), nullable=False, default="active")  # active | paused
    notes:          Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at:     Mapped[datetime]   = mapped_column(DateTime, server_default=func.now(), nullable=False)

    fund: Mapped["UserMutualFund"] = relationship("UserMutualFund", back_populates="sips")


# ── 24. SIPGoal ───────────────────────────────────────────────────────────────

class SIPGoal(Base):
    """A financial goal linked to one or more SIP funds."""
    __tablename__ = "sip_goals"

    id:              Mapped[str]          = mapped_column(String(36),  primary_key=True, default=lambda: str(uuid.uuid4()))
    name:            Mapped[str]          = mapped_column(String(200), nullable=False)
    goal_type:       Mapped[str]          = mapped_column(String(30),  nullable=False, default="wealth")   # retirement | education | house | vehicle | emergency | travel | wedding | wealth
    target_amount:   Mapped[float]        = mapped_column(Float,       nullable=False)
    target_date:     Mapped[date]         = mapped_column(Date,        nullable=False)
    monthly_sip:     Mapped[float]        = mapped_column(Float,       nullable=False, default=0.0)
    expected_return: Mapped[float]        = mapped_column(Float,       nullable=False, default=12.0)  # % per annum
    sip_date:        Mapped[int]          = mapped_column(Integer,     nullable=False, default=1)     # day-of-month
    notes:           Mapped[str | None]   = mapped_column(Text,        nullable=True)
    is_active:       Mapped[bool]         = mapped_column(Boolean,     nullable=False, default=True)
    created_at:      Mapped[datetime]     = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at:      Mapped[datetime]     = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    funds:        Mapped[list["SIPFund"]]        = relationship("SIPFund",        back_populates="goal", cascade="all, delete-orphan")
    investments:  Mapped[list["SIPInvestment"]]  = relationship("SIPInvestment",  back_populates="goal", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<SIPGoal {self.name!r} target=₹{self.target_amount:,.0f} by {self.target_date}>"


# ── 25. SIPFund ───────────────────────────────────────────────────────────────

class SIPFund(Base):
    """A mutual fund allocation within a SIP Goal."""
    __tablename__ = "sip_funds"
    __table_args__ = (
        Index("ix_sip_funds_goal",   "goal_id"),
        Index("ix_sip_funds_scheme", "scheme_code"),
    )

    id:             Mapped[str]        = mapped_column(String(36),  primary_key=True, default=lambda: str(uuid.uuid4()))
    goal_id:        Mapped[str]        = mapped_column(String(36),  ForeignKey("sip_goals.id", ondelete="CASCADE"), nullable=False)
    scheme_code:    Mapped[str]        = mapped_column(String(30),  nullable=False)
    scheme_name:    Mapped[str]        = mapped_column(String(300), nullable=False)
    fund_house:     Mapped[str]        = mapped_column(String(200), nullable=False, default="")
    category:       Mapped[str]        = mapped_column(String(80),  nullable=False, default="")
    monthly_amount: Mapped[float]      = mapped_column(Float,       nullable=False)
    start_date:     Mapped[date]       = mapped_column(Date,        nullable=False)
    is_active:      Mapped[bool]       = mapped_column(Boolean,     nullable=False, default=True)
    created_at:     Mapped[datetime]   = mapped_column(DateTime, server_default=func.now(), nullable=False)

    goal:        Mapped["SIPGoal"]             = relationship("SIPGoal", back_populates="funds")
    investments: Mapped[list["SIPInvestment"]] = relationship("SIPInvestment", back_populates="fund", cascade="all, delete-orphan", foreign_keys="SIPInvestment.fund_id")

    def __repr__(self) -> str:
        return f"<SIPFund {self.scheme_name[:40]!r} ₹{self.monthly_amount:,.0f}/mo>"


# ── 26. SIPInvestment ─────────────────────────────────────────────────────────

class SIPInvestment(Base):
    """A single SIP installment — records NAV at purchase and tracks current value."""
    __tablename__ = "sip_investments"
    __table_args__ = (
        Index("ix_sip_investments_goal_date",   "goal_id",    "investment_date"),
        Index("ix_sip_investments_scheme_date", "scheme_code","investment_date"),
    )

    id:               Mapped[str]          = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    goal_id:          Mapped[str]          = mapped_column(String(36), ForeignKey("sip_goals.id",  ondelete="CASCADE"), nullable=False)
    fund_id:          Mapped[str | None]   = mapped_column(String(36), ForeignKey("sip_funds.id",  ondelete="SET NULL"), nullable=True)
    scheme_code:      Mapped[str]          = mapped_column(String(30), nullable=False)
    scheme_name:      Mapped[str]          = mapped_column(String(300),nullable=False, default="")
    investment_date:  Mapped[date]         = mapped_column(Date,       nullable=False)
    amount:           Mapped[float]        = mapped_column(Float,      nullable=False)
    nav_at_purchase:  Mapped[float]        = mapped_column(Float,      nullable=False, default=0.0)
    units_purchased:  Mapped[float]        = mapped_column(Float,      nullable=False, default=0.0)
    current_nav:      Mapped[float | None] = mapped_column(Float,      nullable=True)
    current_value:    Mapped[float | None] = mapped_column(Float,      nullable=True)
    created_at:       Mapped[datetime]     = mapped_column(DateTime, server_default=func.now(), nullable=False)

    goal: Mapped["SIPGoal"]        = relationship("SIPGoal", back_populates="investments")
    fund: Mapped["SIPFund | None"] = relationship("SIPFund", back_populates="investments", foreign_keys=[fund_id])

    def __repr__(self) -> str:
        return f"<SIPInvestment {self.scheme_code} ₹{self.amount:,.0f} on {self.investment_date} units={self.units_purchased:.4f}>"


# ── 27. PortfolioDiagnosis ────────────────────────────────────────────────────

class PortfolioDiagnosis(Base):
    """AI-powered portfolio health diagnosis snapshot."""
    __tablename__ = "portfolio_diagnoses"
    __table_args__ = (
        Index("ix_portfolio_diagnoses_portfolio_created", "portfolio_id", "created_at"),
    )

    id:            Mapped[str]      = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    portfolio_id:  Mapped[str]      = mapped_column(String(36), ForeignKey("tracker_portfolios.id", ondelete="CASCADE"), nullable=False)
    overall_score: Mapped[int]      = mapped_column(Integer,  nullable=False, default=0)
    overall_grade: Mapped[str]      = mapped_column(String(5), nullable=False, default="F")
    summary:       Mapped[str]      = mapped_column(Text,      nullable=False, default="")
    findings:      Mapped[list]     = mapped_column(JSON,      nullable=False, default=list)
    ai_narrative:  Mapped[str]      = mapped_column(Text,      nullable=False, default="")
    quick_wins:    Mapped[list]     = mapped_column(JSON,      nullable=False, default=list)
    data_snapshot: Mapped[dict]     = mapped_column(JSON,      nullable=False, default=dict)
    is_ai:         Mapped[bool]     = mapped_column(Boolean,   nullable=False, default=False)
    created_at:    Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<PortfolioDiagnosis portfolio={self.portfolio_id} score={self.overall_score} grade={self.overall_grade}>"


# ── 28. EarningsCallSummary ───────────────────────────────────────────────────

class EarningsCallSummary(Base):
    """AI-generated earnings call transcript summary for NSE-listed companies."""
    __tablename__ = "earnings_call_summaries"
    __table_args__ = (
        UniqueConstraint("symbol", "quarter", name="uq_earnings_symbol_quarter"),
        Index("ix_earnings_symbol_created", "symbol", "created_at"),
        Index("ix_earnings_symbol_quarter", "symbol", "quarter"),
    )

    id:                   Mapped[str]          = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    symbol:               Mapped[str]          = mapped_column(String(30),  nullable=False)
    company_name:         Mapped[str]          = mapped_column(String(200), nullable=False, default="")
    quarter:              Mapped[str]          = mapped_column(String(20),  nullable=False, default="")
    call_date:            Mapped[str]          = mapped_column(String(20),  nullable=False, default="")
    pdf_url:              Mapped[str]          = mapped_column(Text,        nullable=False, default="")
    source:               Mapped[str]          = mapped_column(String(20),  nullable=False, default="BSE")
    financial_highlights: Mapped[list]         = mapped_column(JSON,        nullable=False, default=list)
    management_guidance:  Mapped[list]         = mapped_column(JSON,        nullable=False, default=list)
    key_risks:            Mapped[list]         = mapped_column(JSON,        nullable=False, default=list)
    analyst_questions:    Mapped[list]         = mapped_column(JSON,        nullable=False, default=list)
    strategic_updates:    Mapped[list]         = mapped_column(JSON,        nullable=False, default=list)
    revenue_guidance:     Mapped[str | None]   = mapped_column(Text,        nullable=True)
    margin_guidance:      Mapped[str | None]   = mapped_column(Text,        nullable=True)
    capex_guidance:       Mapped[str | None]   = mapped_column(Text,        nullable=True)
    dividend_info:        Mapped[str | None]   = mapped_column(Text,        nullable=True)
    management_tone:      Mapped[str]          = mapped_column(String(20),  nullable=False, default="NEUTRAL")
    tone_reason:          Mapped[str]          = mapped_column(Text,        nullable=False, default="")
    ai_confidence:        Mapped[str]          = mapped_column(String(10),  nullable=False, default="MEDIUM")
    transcript_length:    Mapped[int]          = mapped_column(Integer,     nullable=False, default=0)
    word_count:           Mapped[int]          = mapped_column(Integer,     nullable=False, default=0)
    is_ai:                Mapped[bool]         = mapped_column(Boolean,     nullable=False, default=False)
    created_at:           Mapped[datetime]     = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<EarningsCallSummary {self.symbol} {self.quarter} tone={self.management_tone}>"


# ── IPO analysis cache ────────────────────────────────────────────────────────

class IPOAnalysisCache(Base):
    """Persists Groq/rule-based IPO analysis so we don't re-call the LLM on every request."""
    __tablename__ = "ipo_analysis_cache"
    __table_args__ = (
        Index("ix_ipo_analysis_ipo_id", "ipo_id", unique=True),
    )

    id:          Mapped[str]       = mapped_column(String(36),  primary_key=True, default=lambda: str(uuid.uuid4()))
    ipo_id:      Mapped[str]       = mapped_column(String(100), nullable=False, unique=True)
    ipo_slug:    Mapped[str]       = mapped_column(String(200), nullable=False, default="")
    company_name:Mapped[str]       = mapped_column(String(300), nullable=False, default="")
    status:      Mapped[str]       = mapped_column(String(30),  nullable=False, default="upcoming")
    verdict:     Mapped[str]       = mapped_column(String(20),  nullable=False, default="NEUTRAL")
    score:       Mapped[int]       = mapped_column(Integer,     nullable=False, default=5)
    analysis_json: Mapped[dict]    = mapped_column(JSON,        nullable=False, default=dict)
    ipo_data_json: Mapped[dict]    = mapped_column(JSON,        nullable=False, default=dict)
    source:      Mapped[str]       = mapped_column(String(20),  nullable=False, default="rule_based")
    created_at:  Mapped[datetime]  = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at:  Mapped[datetime]  = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<IPOAnalysisCache {self.ipo_slug} verdict={self.verdict} score={self.score}>"


# ── Agent tables ──────────────────────────────────────────────────────────────

class AgentDecision(Base):
    """Every evaluation the agent makes — traded, blocked, or skipped."""
    __tablename__ = "agent_decisions"
    __table_args__ = (
        Index("ix_agent_dec_symbol_ts",  "symbol", "ts"),
        Index("ix_agent_dec_action_ts",  "action", "ts"),
    )

    id:          Mapped[str]          = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ts:          Mapped[datetime]     = mapped_column(DateTime, server_default=func.now(), nullable=False)
    symbol:      Mapped[str]          = mapped_column(String(30),  nullable=False)
    action:      Mapped[str]          = mapped_column(String(10),  nullable=False)   # BUY | SELL | SKIP
    confidence:  Mapped[int]          = mapped_column(Integer,     nullable=False, default=0)
    regime:      Mapped[str]          = mapped_column(String(30),  nullable=False, default="")
    strategy:    Mapped[str]          = mapped_column(String(50),  nullable=False, default="")
    entry:       Mapped[float | None] = mapped_column(Float, nullable=True)
    stop:        Mapped[float | None] = mapped_column(Float, nullable=True)
    target:      Mapped[float | None] = mapped_column(Float, nullable=True)
    qty:         Mapped[int | None]   = mapped_column(Integer, nullable=True)
    risk_pct:    Mapped[float | None] = mapped_column(Float, nullable=True)
    # ── F&O fields ────────────────────────────────────────────────────────────
    instrument_type:   Mapped[str]          = mapped_column(String(10), nullable=False, default="EQUITY")
    underlying_symbol: Mapped[str | None]   = mapped_column(String(30), nullable=True)
    strike_price:      Mapped[float | None] = mapped_column(Float, nullable=True)
    option_type:       Mapped[str | None]   = mapped_column(String(2),  nullable=True)
    expiry_date:       Mapped[date | None]  = mapped_column(Date, nullable=True)
    lot_size:          Mapped[int]          = mapped_column(Integer, nullable=False, default=1)
    reasons:     Mapped[list]         = mapped_column(JSON,  nullable=False, default=list)
    macro_bias:  Mapped[int | None]   = mapped_column(Integer, nullable=True)
    fund_score:  Mapped[int | None]   = mapped_column(Integer, nullable=True)
    skip_reason:        Mapped[str | None]   = mapped_column(String(200), nullable=True)
    is_paper:           Mapped[bool]         = mapped_column(Boolean, nullable=False, default=True)
    order_id:           Mapped[str | None]   = mapped_column(String(60),  nullable=True)
    # Audit columns added for multiplicative-confidence pipeline (hub 7-factor)
    master_score:       Mapped[float | None] = mapped_column(Float,  nullable=True)
    confidence_factors: Mapped[dict | None]  = mapped_column(JSON,   nullable=True)
    created_at:         Mapped[datetime]     = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<AgentDecision {self.action} {self.symbol} conf={self.confidence} {self.strategy}>"


class AgentTrade(Base):
    """Open and closed agent trades with P&L tracking."""
    __tablename__ = "agent_trades"
    __table_args__ = (
        Index("ix_agent_trade_symbol_entry", "symbol", "entry_ts"),
        Index("ix_agent_trade_paper_created", "is_paper", "entry_ts"),
    )

    id:            Mapped[str]          = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    decision_id:   Mapped[str | None]   = mapped_column(String(36), ForeignKey("agent_decisions.id", ondelete="SET NULL"), nullable=True)
    symbol:        Mapped[str]          = mapped_column(String(30),  nullable=False)
    side:          Mapped[str]          = mapped_column(String(10),  nullable=False)
    qty:           Mapped[int]          = mapped_column(Integer,     nullable=False)
    # CNC = delivery (long only, T+1 settlement); MIS = intraday (short selling allowed,
    # must square off by 3:20 PM IST); NRML = overnight F&O
    product:       Mapped[str]          = mapped_column(String(10),  nullable=False, default="CNC")
    # ── F&O fields ────────────────────────────────────────────────────────────
    instrument_type:     Mapped[str]          = mapped_column(String(10), nullable=False, default="EQUITY")
    underlying_symbol:   Mapped[str | None]   = mapped_column(String(30), nullable=True)
    strike_price:        Mapped[float | None] = mapped_column(Float, nullable=True)
    option_type:         Mapped[str | None]   = mapped_column(String(2),  nullable=True)
    expiry_date:         Mapped[date | None]  = mapped_column(Date, nullable=True)
    lot_size:            Mapped[int]          = mapped_column(Integer, nullable=False, default=1)
    contract_multiplier: Mapped[float]        = mapped_column(Float, nullable=False, default=1.0)
    margin_blocked:      Mapped[float]        = mapped_column(Float, nullable=False, default=0.0)
    entry_price:   Mapped[float]        = mapped_column(Float,       nullable=False)
    exit_price:    Mapped[float | None] = mapped_column(Float,       nullable=True)
    stop_price:    Mapped[float]        = mapped_column(Float,       nullable=False)
    target_price:  Mapped[float]        = mapped_column(Float,       nullable=False)
    entry_ts:      Mapped[datetime]     = mapped_column(DateTime,    nullable=False)
    exit_ts:       Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exit_reason:   Mapped[str | None]   = mapped_column(String(50),  nullable=True)
    pnl:           Mapped[float | None] = mapped_column(Float,       nullable=True)
    pnl_pct:       Mapped[float | None] = mapped_column(Float,       nullable=True)
    strategy:      Mapped[str]          = mapped_column(String(50),  nullable=False, default="")
    regime:        Mapped[str]          = mapped_column(String(30),  nullable=False, default="")
    brokerage:     Mapped[float]        = mapped_column(Float,       nullable=False, default=0.0)
    is_paper:      Mapped[bool]         = mapped_column(Boolean,     nullable=False, default=True)
    created_at:    Mapped[datetime]     = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<AgentTrade {self.side} {self.symbol} qty={self.qty} @ {self.entry_price}>"


class AgentPosition(Base):
    """Currently open agent positions — one row per symbol."""
    __tablename__ = "agent_positions"
    __table_args__ = (
        UniqueConstraint("symbol", "is_paper", name="uq_agent_position_symbol"),
    )

    id:             Mapped[str]          = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    symbol:         Mapped[str]          = mapped_column(String(30), nullable=False)
    side:           Mapped[str]          = mapped_column(String(10), nullable=False)
    qty:            Mapped[int]          = mapped_column(Integer,    nullable=False)
    entry_price:    Mapped[float]        = mapped_column(Float,      nullable=False)
    stop_price:     Mapped[float]        = mapped_column(Float,      nullable=False)
    target_price:   Mapped[float]        = mapped_column(Float,      nullable=False)
    current_price:  Mapped[float | None] = mapped_column(Float,      nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float,      nullable=True)
    strategy:       Mapped[str]          = mapped_column(String(50), nullable=False, default="")
    regime:         Mapped[str]          = mapped_column(String(30), nullable=False, default="")
    entry_ts:       Mapped[datetime]     = mapped_column(DateTime,   nullable=False)
    is_paper:       Mapped[bool]         = mapped_column(Boolean,    nullable=False, default=True)
    updated_at:     Mapped[datetime]     = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<AgentPosition {self.side} {self.symbol} qty={self.qty} entry={self.entry_price}>"


class AgentPerformance(Base):
    """Daily performance snapshot for the agent."""
    __tablename__ = "agent_performance"
    __table_args__ = (
        UniqueConstraint("date", "is_paper", name="uq_agent_perf_date"),
    )

    id:             Mapped[str]   = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    date:           Mapped[date]  = mapped_column(Date,    nullable=False)
    total_trades:   Mapped[int]   = mapped_column(Integer, nullable=False, default=0)
    winning_trades: Mapped[int]   = mapped_column(Integer, nullable=False, default=0)
    losing_trades:  Mapped[int]   = mapped_column(Integer, nullable=False, default=0)
    gross_pnl:      Mapped[float] = mapped_column(Float,   nullable=False, default=0.0)
    net_pnl:        Mapped[float] = mapped_column(Float,   nullable=False, default=0.0)
    win_rate:       Mapped[float] = mapped_column(Float,   nullable=False, default=0.0)
    avg_win:        Mapped[float] = mapped_column(Float,   nullable=False, default=0.0)
    avg_loss:       Mapped[float] = mapped_column(Float,   nullable=False, default=0.0)
    expectancy:     Mapped[float] = mapped_column(Float,   nullable=False, default=0.0)
    max_drawdown:   Mapped[float] = mapped_column(Float,   nullable=False, default=0.0)
    sharpe:         Mapped[float] = mapped_column(Float,   nullable=False, default=0.0)
    equity_end:     Mapped[float] = mapped_column(Float,   nullable=False, default=0.0)
    is_paper:       Mapped[bool]  = mapped_column(Boolean, nullable=False, default=True)
    created_at:     Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<AgentPerformance {self.date} trades={self.total_trades} net_pnl={self.net_pnl}>"


# ── Master Intelligence Hub ────────────────────────────────────────────────────

class MasterIntelligenceScore(Base):
    """Per-symbol unified score combining technical, news, sector, macro,
    earnings, fundamental, and options signals — one row per symbol per cycle."""
    __tablename__ = "master_intelligence_scores"
    __table_args__ = (
        Index("ix_mis_symbol_scored",  "symbol", "scored_at"),
        Index("ix_mis_scored_master",  "scored_at", "master_score"),
        Index("ix_mis_symbol_bar",     "symbol", "bar_time"),
    )

    id:                Mapped[str]      = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    symbol:            Mapped[str]      = mapped_column(String(30),  nullable=False)
    scored_at:         Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    bar_time:          Mapped[datetime] = mapped_column(DateTime, nullable=False)

    technical_score:   Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)
    news_score:        Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)
    sector_score:      Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)
    macro_score:       Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)
    earnings_score:    Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)
    fundamental_score: Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)
    options_score:     Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)
    portfolio_score:   Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)

    master_score:      Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)
    rank:              Mapped[int]      = mapped_column(Integer, nullable=False, default=0)
    signal:            Mapped[str]      = mapped_column(String(15), nullable=False, default="NEUTRAL")
    regime:            Mapped[str]      = mapped_column(String(30), nullable=False, default="")
    reasoning:         Mapped[dict]     = mapped_column(JSON, nullable=False, default=dict)

    blocked_reason:    Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_blocked:        Mapped[bool]     = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<MasterIntelligenceScore {self.symbol} {self.master_score:.1f} {self.signal}>"


class HubCycleLog(Base):
    """One row per master-intelligence cycle for observability."""
    __tablename__ = "hub_cycle_logs"

    id:               Mapped[str]          = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    cycle_start:      Mapped[datetime]     = mapped_column(DateTime, nullable=False)
    cycle_end:        Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    bar_time:         Mapped[datetime]     = mapped_column(DateTime, nullable=False)
    symbols_scored:   Mapped[int]          = mapped_column(Integer, nullable=False, default=0)
    top_buys:         Mapped[list]         = mapped_column(JSON, nullable=False, default=list)
    top_sells:        Mapped[list]         = mapped_column(JSON, nullable=False, default=list)
    macro_context:    Mapped[dict]         = mapped_column(JSON, nullable=False, default=dict)
    decisions_made:   Mapped[int]          = mapped_column(Integer, nullable=False, default=0)
    skipped_count:    Mapped[int]          = mapped_column(Integer, nullable=False, default=0)
    status:           Mapped[str]          = mapped_column(String(15), nullable=False, default="running")
    error_msg:        Mapped[str | None]   = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at:       Mapped[datetime]     = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<HubCycleLog {self.bar_time} scored={self.symbols_scored} status={self.status}>"


class MFIntelligenceScore(Base):
    """Mutual-fund scoring output from the hub MF engine."""
    __tablename__ = "mf_intelligence_scores"
    __table_args__ = (
        Index("ix_mfis_scheme_scored", "scheme_code", "scored_at"),
    )

    id:               Mapped[str]      = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    scheme_code:      Mapped[str]      = mapped_column(String(30),  nullable=False)
    scheme_name:      Mapped[str]      = mapped_column(String(300), nullable=False, default="")
    category:         Mapped[str]      = mapped_column(String(80),  nullable=False, default="")
    scored_at:        Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    nav_trend_score:  Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)
    sector_alignment: Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)
    category_score:   Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)
    master_score:     Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)
    signal:           Mapped[str]      = mapped_column(String(15), nullable=False, default="HOLD")
    reasoning:        Mapped[dict]     = mapped_column(JSON, nullable=False, default=dict)

    def __repr__(self) -> str:
        return f"<MFIntelligenceScore {self.scheme_code} {self.master_score:.1f} {self.signal}>"


# ── User Watchlist ─────────────────────────────────────────────────────────────

class MarketShortlist(Base):
    """Top-N NSE symbols the market scanner selected in the latest 15-min cycle.

    Overwritten each cycle — always reflects the current best opportunities.
    The trade loop reads this instead of the hardcoded watchlist so the agent
    covers the full NSE universe without having to deep-analyse 9,600 stocks
    every 60 seconds.
    """
    __tablename__ = "market_shortlist"
    __table_args__ = (
        Index("ix_msl_rank",       "rank"),
        Index("ix_msl_created_at", "created_at"),
    )

    id:                  Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol:              Mapped[str]      = mapped_column(String(30), nullable=False)
    master_score:        Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)
    volume_ratio:        Mapped[float]    = mapped_column(Float, nullable=False, default=1.0)
    rsi:                 Mapped[float | None] = mapped_column(Float, nullable=True)
    price_vs_ema20:      Mapped[float | None] = mapped_column(Float, nullable=True)
    signal:              Mapped[str]      = mapped_column(String(15), nullable=False, default="HOLD")
    sector:              Mapped[str]      = mapped_column(String(80), nullable=False, default="")
    rank:                Mapped[int]      = mapped_column(Integer, nullable=False, default=0)
    upper_circuit_days:  Mapped[int | None]   = mapped_column(Integer, nullable=True, default=0)
    volume_surge:        Mapped[float | None] = mapped_column(Float, nullable=True, default=1.0)
    created_at:          Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<MarketShortlist #{self.rank} {self.symbol} score={self.master_score:.1f}>"


class UserWatchlist(Base):
    """Symbols the user has manually added to the agent scan universe.

    These are appended to the hardcoded WATCHLIST_NSE_LARGE_CAP/MID_CAP lists
    so the automated paper-trade loop also analyses and trades them.
    """
    __tablename__ = "user_watchlist"

    id:         Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol:     Mapped[str]      = mapped_column(String(30), nullable=False, unique=True)
    added_at:   Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    is_active:  Mapped[bool]     = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:
        return f"<UserWatchlist {self.symbol}>"


class HubDailyHistory(Base):
    """Daily flight-recorder for the Hub 7-factor engine.

    One row per (date, symbol) — captures every score and the pre-trade
    research gate outcome BEFORE any trade decision is made.  This is the
    historical dataset that enables accurate Hub-replay backtesting: instead
    of re-computing scores from scratch, the backtest queries this table by
    (date, symbol) and replays the exact intelligence the live agent saw.

    Populated by persist_daily_history() called at the end of each Hub cycle.
    """
    __tablename__ = "hub_daily_history"
    __table_args__ = (
        Index("ix_hdh_symbol_date", "symbol", "date"),
        Index("ix_hdh_date_score",  "date",   "master_score"),
    )

    # ── Identity ───────────────────────────────────────────────────────────────
    date:   Mapped[date]  = mapped_column(Date,      primary_key=True, nullable=False)
    symbol: Mapped[str]   = mapped_column(String(30), primary_key=True, nullable=False)

    # ── Hub 7-factor sub-scores (same scale as MasterIntelligenceScore) ────────
    technical_score:   Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    news_score:        Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sector_score:      Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    macro_score:       Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    earnings_score:    Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fundamental_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    options_score:     Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    master_score:      Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Signal + state ────────────────────────────────────────────────────────
    signal:        Mapped[str]      = mapped_column(String(15),  nullable=False, default="HOLD")
    regime:        Mapped[str]      = mapped_column(String(30),  nullable=False, default="UNKNOWN")
    sector:        Mapped[str]      = mapped_column(String(40),  nullable=True)
    fund_grade:    Mapped[str]      = mapped_column(String(15),  nullable=True)
    is_blocked:    Mapped[bool]     = mapped_column(Boolean,     nullable=False, default=False)
    blocked_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # ── Macro snapshot at score time (hard to reconstruct later) ─────────────
    india_vix:     Mapped[float | None] = mapped_column(Float, nullable=True)
    fii_net_3d:    Mapped[float | None] = mapped_column(Float, nullable=True)
    dii_net_3d:    Mapped[float | None] = mapped_column(Float, nullable=True)
    nse_mood:      Mapped[str | None]   = mapped_column(String(25), nullable=True)
    ad_ratio:      Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Pre-trade research gate outcome (NULL = not researched / HOLD signal) ─
    # Populated only for BUY/STRONG_BUY signals (mirrors live agent behavior).
    web_veto:         Mapped[bool | None]  = mapped_column(Boolean, nullable=True)
    web_veto_reason:  Mapped[str | None]   = mapped_column(String(300), nullable=True)
    web_confidence:   Mapped[float | None] = mapped_column(Float, nullable=True)
    research_note:    Mapped[str | None]   = mapped_column(Text, nullable=True)
    research_source:  Mapped[str | None]   = mapped_column(String(40), nullable=True)

    # ── Rich reasoning JSON (full breakdown for debugging + analysis) ─────────
    reasoning: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    scored_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        veto_str = "" if self.web_veto is None else f" veto={self.web_veto}"
        return (
            f"<HubDailyHistory {self.date} {self.symbol} "
            f"score={self.master_score:.1f} signal={self.signal}{veto_str}>"
        )


class HubUniverse(Base):
    """The configurable universe the Master Intelligence Hub deep-scores each
    cycle (7-factor). Rebuilt daily as the top-N NSE equities by average daily
    turnover (₹ volume × close), excluding bonds/SME. Replaces the old hardcoded
    ~22-symbol list so news/fundamentals/earnings/macro apply to ~500 liquid names.
    """
    __tablename__ = "hub_universe"
    __table_args__ = (
        Index("ix_hub_universe_rank", "rank"),
    )

    id:          Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol:      Mapped[str]      = mapped_column(String(30), nullable=False, unique=True)
    turnover_cr: Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)
    rank:        Mapped[int]      = mapped_column(Integer, nullable=False, default=0)
    updated_at:  Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<HubUniverse #{self.rank} {self.symbol} ₹{self.turnover_cr:.1f}Cr/day>"


# ── Portfolio Capital Model ────────────────────────────────────────────────────

class PortfolioPolicy(Base):
    """Agent paper-portfolio risk policy — single live row (id=1).

    Controls position sizing caps, sector concentration limits, and rebalancing
    thresholds so the trade loop implements Modern Portfolio Theory constraints
    rather than deploying capital ad-hoc.
    """
    __tablename__ = "portfolio_policy"

    id:                     Mapped[int]   = mapped_column(Integer, primary_key=True, autoincrement=True)
    risk_tolerance:         Mapped[str]   = mapped_column(String(20),  nullable=False, default="MODERATE")   # LOW | MODERATE | HIGH
    target_annual_return:   Mapped[float] = mapped_column(Float, nullable=False, default=15.0)   # %
    max_single_stock_weight: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)  # % of equity
    max_sector_weight:      Mapped[float] = mapped_column(Float, nullable=False, default=25.0)   # % of equity
    min_cash_reserve:       Mapped[float] = mapped_column(Float, nullable=False, default=10.0)   # % of equity
    rebalance_threshold:    Mapped[float] = mapped_column(Float, nullable=False, default=5.0)    # drift % before rebalance trigger
    risk_free_rate:         Mapped[float] = mapped_column(Float, nullable=False, default=7.1)    # India 10Y GSec %
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<PortfolioPolicy risk={self.risk_tolerance} "
            f"max_stock={self.max_single_stock_weight}% "
            f"max_sector={self.max_sector_weight}% "
            f"cash_floor={self.min_cash_reserve}%>"
        )


class AgentCapitalSnapshot(Base):
    """Daily capital model snapshot — position weights, sector weights, beta,
    Sharpe/Treynor/Jensen.  One row per day, inserted by the nightly performance task.

    Used by the Portfolio Analytics page and the weekly AI Telegram report.
    """
    __tablename__ = "agent_capital_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_date", name="uq_capital_snapshot_date"),
        Index("ix_capital_snapshot_date", "snapshot_date"),
    )

    id:             Mapped[str]          = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    snapshot_date:  Mapped[date]         = mapped_column(Date, nullable=False)

    # Capital allocation
    equity:         Mapped[float]        = mapped_column(Float, nullable=False, default=0.0)
    cash:           Mapped[float]        = mapped_column(Float, nullable=False, default=0.0)
    cash_pct:       Mapped[float]        = mapped_column(Float, nullable=False, default=0.0)
    num_positions:  Mapped[int]          = mapped_column(Integer, nullable=False, default=0)

    # Performance metrics (annualized)
    portfolio_return: Mapped[float | None] = mapped_column(Float, nullable=True)   # %
    benchmark_return: Mapped[float | None] = mapped_column(Float, nullable=True)   # NIFTY %
    portfolio_beta:   Mapped[float | None] = mapped_column(Float, nullable=True)
    portfolio_stddev: Mapped[float | None] = mapped_column(Float, nullable=True)   # annualized
    sharpe_ratio:     Mapped[float | None] = mapped_column(Float, nullable=True)
    treynor_ratio:    Mapped[float | None] = mapped_column(Float, nullable=True)
    jensens_alpha:    Mapped[float | None] = mapped_column(Float, nullable=True)

    # Allocation JSON blobs
    sector_weights:   Mapped[dict | None] = mapped_column(JSON, nullable=True)    # {sector: pct}
    position_weights: Mapped[dict | None] = mapped_column(JSON, nullable=True)    # {symbol: pct}
    rebalance_needed: Mapped[bool]        = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<AgentCapitalSnapshot {self.snapshot_date} "
            f"equity={self.equity:.0f} sharpe={self.sharpe_ratio} "
            f"alpha={self.jensens_alpha}>"
        )


# ── Trade Excursion Samples ───────────────────────────────────────────────────

class TradeExcursionSample(Base):
    """Append-only per-tick unrealised P&L samples for MFE/MAE calculation.

    One row per mark-to-market update per open trade.  After the trade closes
    the running peak/trough is summarised into paper_trades.mfe_*/mae_* and
    these rows can be pruned.  Gated by ENABLE_EXCURSION_SAMPLES (default off)
    to avoid write amplification in high-frequency cycles.
    """
    __tablename__ = "trade_excursion_samples"
    __table_args__ = (
        Index("ix_excursion_trade_ts", "trade_id", "ts"),
    )

    id:             Mapped[int]          = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_id:       Mapped[int]          = mapped_column(
        ForeignKey("paper_trades.id", ondelete="CASCADE"), nullable=False
    )
    ts:             Mapped[datetime]     = mapped_column(DateTime, nullable=False)
    price:          Mapped[float]        = mapped_column(Float, nullable=False)
    unrealised_pnl: Mapped[float]        = mapped_column(Float, nullable=False)
    unrealised_r:   Mapped[float | None] = mapped_column(Float, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<TradeExcursionSample trade={self.trade_id} "
            f"@{self.ts} pnl={self.unrealised_pnl:+.2f} r={self.unrealised_r}>"
        )
