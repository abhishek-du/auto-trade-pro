from datetime import date, datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger, Date, DateTime, Enum, Float, ForeignKey,
    Index, Integer, JSON, String, Text, UniqueConstraint, func,
)
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

    symbol:    Mapped[str] = mapped_column(String(20), nullable=False, index=True)
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

    symbol:        Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    direction:     Mapped[TradeDirection] = mapped_column(Enum(TradeDirection), nullable=False)
    entry_price:   Mapped[float] = mapped_column(Float, nullable=False)
    current_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss:     Mapped[float] = mapped_column(Float, nullable=False)
    take_profit:   Mapped[float] = mapped_column(Float, nullable=False)
    size_units:    Mapped[float] = mapped_column(Float, nullable=False)
    size_usd:      Mapped[float] = mapped_column(Float, nullable=False)

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
    tickers_affected: Mapped[list | None] = mapped_column(JSON, nullable=True)
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


# ── 9. SimulationLog ──────────────────────────────────────────────────────────

class SimulationLog(Base):
    """Append-only audit log of every AI decision for post-analysis."""
    __tablename__ = "simulation_logs"
    __table_args__ = (
        Index("ix_simlog_symbol_ts", "symbol", "timestamp"),
    )

    id:         Mapped[int]          = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str]          = mapped_column(String(30), nullable=False, index=True)
    symbol:     Mapped[str]          = mapped_column(String(20), nullable=False)
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
