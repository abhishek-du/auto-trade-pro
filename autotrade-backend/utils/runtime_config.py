"""RuntimeConfig — DB-backed runtime settings with .env fallback.

All risk parameters and feature flags can be changed at runtime via
/api/v1/settings without restarting the application.  Each value is
stored as a JSON-encoded string in the runtime_settings table.

Usage
-----
    # In an async route or task:
    cfg = await RuntimeConfig.load(session)
    balance = cfg.paper_trading_balance
    risk    = cfg.max_risk_per_trade

    # Upsert a single key:
    await RuntimeConfig.set(session, "max_open_positions", 7)
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import RuntimeSettings
from utils.config import settings
from utils.logger import logger


# ── Known keys and their JSON types ──────────────────────────────────────────

_KNOWN_KEYS: dict[str, type] = {
    # Paper-trading
    "paper_trading_balance":  float,
    "max_risk_per_trade":     float,
    "max_open_positions":     int,
    "max_daily_loss":         float,
    # Risk / sizing
    "atr_multiplier":         float,
    "min_risk_reward":        float,
    "max_portfolio_risk":     float,   # total stop-loss risk across all open positions
    "min_cash_buffer":        float,   # minimum dry cash as fraction of equity
    # Diversification caps (2026-08-17) — bound correlation, not capital
    "max_concurrent_positions": int,   # 0 disables
    "max_positions_per_sector": int,   # 0 disables
    "max_sector_capital_pct":   float, # 0 disables
    "max_strategy_capital_pct": float, # 0 disables
    # Indian market
    "indian_market_max_risk": float,
    "indian_intraday_sl_pct": float,
    # Feature flags
    "enable_fii_dii_analysis": bool,
    "enable_india_vix":        bool,
    "enable_mutual_funds":     bool,
    "enable_ml_predictions":   bool,
    # Watchlists (stored as JSON arrays)
    "watchlist_forex":         list,
    "watchlist_stocks":        list,
    # Decision router — runtime-mutable mode toggle
    # paper_mode=True → use simulator;  paper_mode=False → live Zerodha
    "paper_mode":              bool,
    "paper_confidence_threshold": float,
    "live_confidence_threshold":  float,
    "agent_confidence_threshold": int,   # min confidence % to open a trade (30–100)
    # NSE/BSE product type for new positional trades:
    #   CNC = Cash & Carry (delivery, long-only, T+1 settlement, no expiry)
    #   MIS = Margin Intraday Square-off (short selling allowed, must close by 3:20 PM IST)
    # MEAN_REVERSION_SHORT strategy always uses MIS regardless of this setting.
    "agent_default_product":   str,
    # Allow SELL signals from Hub 7-factor negative scores (equity intraday / MIS only).
    # False by default — only BUY signals are acted on.
    "equity_short_enabled":    bool,
    # Scanner kill-switch: False = agent runs solo, SCAN paper trader is silent.
    "scanner_enabled":         bool,
    # Enable intraday MIS trades (required for equity short-selling).
    "intraday_enabled":        bool,
    # Global fail-safe: blocks all new entries across all strategies.
    "trading_halted":          bool,
    # Path F kill switch — DB-backed so it crosses the process boundary.
    "tactical_execution_enabled": bool,
    "technical_origination_blocked": bool,
    "news_short_enabled":            bool,
    # ── Strategy execution toggles (admin UI, 2026-08-20) ─────────────────────
    # One switch per origination path. DB-backed so a toggle takes effect in
    # every process without a restart. All default to True: a missing row means
    # "enabled", so a fresh DB or a wiped table can never silently disable
    # trading. See STRATEGY_FLAGS below for the name -> key mapping.
    "strategy_india_trade_loop_enabled":    bool,
    "strategy_news_engine_enabled":         bool,
    "strategy_pre_event_gap_enabled":       bool,
    "strategy_direct_news_enabled":         bool,
    "strategy_tactical_enabled":            bool,
    "strategy_master_intelligence_enabled": bool,
    # ── Broker provider toggles (2026-08-31) ────────────────────────────────
    # Which broker backend may be used for market data and orders. Both can be
    # on (Upstox primary, Zerodha as fallback) or either can be off.
    #
    # DB-backed rather than .env so an operator can cut a broker off MID-SESSION
    # without a restart -- which is exactly the situation these were added for:
    # Kite Connect's token expired on 2026-08-31 while the market was open, and
    # every Kite call was failing with "Invalid `api_key` or `access_token`"
    # until the tasks were gated.
    "broker_upstox_enabled":   bool,
    "broker_zerodha_enabled":  bool,
    # Transient market-shock cooldown: ISO-8601 UTC timestamp until which new
    # entries are blocked after a shock FLATTEN. Cleared automatically once past.
    "shock_cooldown_until":    str,
    # Watermark for the high-impact news alert: ISO-8601 crawled_at of the newest
    # headline already scanned, so we never re-alert the same news.
    "last_news_alert_at":      str,
}


class RuntimeConfig:
    """Typed view over runtime_settings rows with .env fallback."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    async def load(cls, session: AsyncSession) -> "RuntimeConfig":
        """Load all runtime_settings rows from DB and return a RuntimeConfig."""
        rows = (await session.execute(select(RuntimeSettings))).scalars().all()
        data: dict[str, Any] = {}
        for row in rows:
            try:
                data[row.key] = json.loads(row.value)
            except (json.JSONDecodeError, TypeError):
                data[row.key] = row.value
        return cls(data)

    # ── Writer ────────────────────────────────────────────────────────────────

    @staticmethod
    async def set(session: AsyncSession, key: str, value: Any) -> None:
        """Upsert a single key into runtime_settings."""
        if key not in _KNOWN_KEYS:
            raise ValueError(f"Unknown runtime setting key: {key!r}")

        expected_type = _KNOWN_KEYS[key]
        if not isinstance(value, expected_type):
            raise TypeError(
                f"runtime_settings[{key!r}] expects {expected_type.__name__}, "
                f"got {type(value).__name__}"
            )

        stmt = pg_insert(RuntimeSettings).values(
            key=key,
            value=json.dumps(value),
        ).on_conflict_do_update(
            index_elements=["key"],
            set_={"value": json.dumps(value)},
        )
        await session.execute(stmt)

    @staticmethod
    async def set_many(session: AsyncSession, updates: dict[str, Any]) -> None:
        """Upsert multiple keys in one call."""
        for key, value in updates.items():
            await RuntimeConfig.set(session, key, value)

    # ── Typed property accessors (fall back to .env/config.py) ───────────────

    def _get(self, key: str, default: Any) -> Any:
        return self._data.get(key, default)

    @property
    def paper_trading_balance(self) -> float:
        return float(self._get("paper_trading_balance", settings.PAPER_TRADING_BALANCE))

    @property
    def max_risk_per_trade(self) -> float:
        return float(self._get("max_risk_per_trade", settings.MAX_RISK_PER_TRADE))

    @property
    def max_open_positions(self) -> int:
        return int(self._get("max_open_positions", settings.MAX_OPEN_POSITIONS))

    @property
    def max_daily_loss(self) -> float:
        return float(self._get("max_daily_loss", settings.MAX_DAILY_LOSS))

    @property
    def atr_multiplier(self) -> float:
        return float(self._get("atr_multiplier", settings.ATR_MULTIPLIER))

    @property
    def min_risk_reward(self) -> float:
        return float(self._get("min_risk_reward", settings.MIN_RISK_REWARD))

    @property
    def max_portfolio_risk(self) -> float:
        return float(self._get("max_portfolio_risk", getattr(settings, "MAX_PORTFOLIO_RISK", 0.15)))

    @property
    def min_cash_buffer(self) -> float:
        return float(self._get("min_cash_buffer", getattr(settings, "MIN_CASH_BUFFER", 0.10)))

    # ── Diversification caps (2026-08-17 forensic post-mortem) ───────────────
    # Each returns 0 when disabled; validate_signal() skips the corresponding
    # check on 0 so these can be turned off at runtime without a redeploy.

    @property
    def max_concurrent_positions(self) -> int:
        return int(self._get("max_concurrent_positions",
                             getattr(settings, "MAX_CONCURRENT_POSITIONS", 40)))

    @property
    def max_positions_per_sector(self) -> int:
        return int(self._get("max_positions_per_sector",
                             getattr(settings, "MAX_POSITIONS_PER_SECTOR", 8)))

    @property
    def max_sector_capital_pct(self) -> float:
        return float(self._get("max_sector_capital_pct",
                               getattr(settings, "MAX_SECTOR_CAPITAL_PCT", 0.20)))

    @property
    def max_strategy_capital_pct(self) -> float:
        return float(self._get("max_strategy_capital_pct",
                               getattr(settings, "MAX_STRATEGY_CAPITAL_PCT", 0.35)))

    @property
    def indian_market_max_risk(self) -> float:
        return float(self._get("indian_market_max_risk", settings.INDIAN_MARKET_MAX_RISK))

    @property
    def indian_intraday_sl_pct(self) -> float:
        return float(self._get("indian_intraday_sl_pct", settings.INDIAN_INTRADAY_SL_PCT))

    @property
    def enable_fii_dii_analysis(self) -> bool:
        return bool(self._get("enable_fii_dii_analysis", settings.ENABLE_FII_DII_ANALYSIS))

    @property
    def enable_india_vix(self) -> bool:
        return bool(self._get("enable_india_vix", settings.ENABLE_INDIA_VIX))

    @property
    def enable_mutual_funds(self) -> bool:
        return bool(self._get("enable_mutual_funds", settings.ENABLE_MUTUAL_FUNDS))

    @property
    def enable_ml_predictions(self) -> bool:
        return bool(self._get("enable_ml_predictions", settings.ENABLE_ML_PREDICTIONS))

    @property
    def watchlist_forex(self) -> list[str]:
        return list(self._get("watchlist_forex", settings.forex_symbols))

    @property
    def watchlist_stocks(self) -> list[str]:
        return list(self._get("watchlist_stocks", settings.stock_symbols))

    @property
    def paper_mode(self) -> bool:
        return bool(self._get("paper_mode", settings.PAPER_MODE))

    @property
    def paper_confidence_threshold(self) -> float:
        return float(self._get("paper_confidence_threshold", getattr(settings, "PAPER_CONFIDENCE_THRESHOLD", 60.0)))

    @property
    def live_confidence_threshold(self) -> float:
        return float(self._get("live_confidence_threshold", getattr(settings, "LIVE_CONFIDENCE_THRESHOLD", 70.0)))

    @property
    def agent_confidence_threshold(self) -> int:
        return int(self._get("agent_confidence_threshold", getattr(settings, "AGENT_CONFIDENCE_THRESHOLD", 30)))

    @property
    def agent_default_product(self) -> str:
        val = self._get("agent_default_product", getattr(settings, "AGENT_DEFAULT_PRODUCT", "CNC"))
        return val if val in ("CNC", "MIS") else "CNC"

    @property
    def equity_short_enabled(self) -> bool:
        return bool(self._get("equity_short_enabled", getattr(settings, "EQUITY_SHORT_ENABLED", False)))

    @property
    def scanner_enabled(self) -> bool:
        return bool(self._get("scanner_enabled", getattr(settings, "SCANNER_ENABLED", False)))

    @property
    def intraday_enabled(self) -> bool:
        return bool(self._get("intraday_enabled", getattr(settings, "INTRADAY_ENABLED", False)))

    @property
    def trading_halted(self) -> bool:
        return bool(self._get("trading_halted", getattr(settings, "TRADING_HALTED", False)))

    @property
    def tactical_execution_enabled(self) -> bool:
        """Path F execution master switch. Falls back to the .env default.

        DB-backed on purpose: flipping this halts the tactical pipeline in every
        process immediately, with no restart. settings.AGENT_ENABLED does not —
        that was audit finding D4.
        """
        return bool(self._get("tactical_execution_enabled",
                              getattr(settings, "TACTICAL_EXECUTION_ENABLED", False)))


    @property
    def technical_origination_blocked(self) -> bool:
        """Cross-process kill switch for TECHNICAL origination (contract SS10b).

        Defaults to the .env value, so leaving this unset in the DB keeps
        whatever the deployment chose. Setting it True in RuntimeConfig
        re-blocks every process instantly, without a restart.
        """
        return bool(self._get("technical_origination_blocked",
                              getattr(settings, "TECHNICAL_ORIGINATION_BLOCKED", True)))
    @property
    def shock_cooldown_active(self) -> bool:
        """True while a market-shock FLATTEN cooldown is still in effect."""
        raw = self._get("shock_cooldown_until", "")
        if not raw:
            return False
        try:
            from datetime import datetime as _dt
            return _dt.utcnow() < _dt.fromisoformat(raw)
        except (ValueError, TypeError):
            return False

    def to_dict(self) -> dict[str, Any]:
        """Return all current values (DB overrides merged with .env defaults)."""
        return {
            "paper_trading_balance":   self.paper_trading_balance,
            "max_risk_per_trade":      self.max_risk_per_trade,
            "max_open_positions":      self.max_open_positions,
            "max_daily_loss":          self.max_daily_loss,
            "atr_multiplier":          self.atr_multiplier,
            "min_risk_reward":         self.min_risk_reward,
            "indian_market_max_risk":  self.indian_market_max_risk,
            "indian_intraday_sl_pct":  self.indian_intraday_sl_pct,
            "max_portfolio_risk":      self.max_portfolio_risk,
            "min_cash_buffer":         self.min_cash_buffer,
            "enable_fii_dii_analysis": self.enable_fii_dii_analysis,
            "enable_india_vix":        self.enable_india_vix,
            "enable_mutual_funds":     self.enable_mutual_funds,
            "enable_ml_predictions":   self.enable_ml_predictions,
            "watchlist_forex":         self.watchlist_forex,
            "watchlist_stocks":        self.watchlist_stocks,
            "paper_mode":              self.paper_mode,
            "paper_confidence_threshold": self.paper_confidence_threshold,
            "live_confidence_threshold":  self.live_confidence_threshold,
            "agent_default_product":       self.agent_default_product,
            "agent_confidence_threshold":  self.agent_confidence_threshold,
            "equity_short_enabled":        self.equity_short_enabled,
            "scanner_enabled":             self.scanner_enabled,
            "intraday_enabled":            self.intraday_enabled,
            "trading_halted":              self.trading_halted,
            "tactical_execution_enabled":  self.tactical_execution_enabled,
            "technical_origination_blocked": self.technical_origination_blocked,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Strategy execution toggles
# ─────────────────────────────────────────────────────────────────────────────

# Short admin-UI name -> RuntimeConfig key. The API and the frontend both speak
# the short name; only this map knows the storage key.
# UI name -> RuntimeConfig key. Mirrors STRATEGY_FLAGS below.
BROKER_FLAGS: dict[str, str] = {
    "upstox":  "broker_upstox_enabled",
    "zerodha": "broker_zerodha_enabled",
}

# Defaults if no row exists yet. Upstox ON, Zerodha OFF -- the state after the
# 2026-08-31 migration. Deliberately NOT fail-open like the strategy flags: a
# broker with an expired token that defaults to "enabled" produces a storm of
# failing calls, which is what this toggle exists to stop.
BROKER_DEFAULTS: dict[str, bool] = {"upstox": True, "zerodha": False}


async def broker_enabled(session, name: str) -> bool:
    """Is this broker allowed to be used right now?

    Reads RuntimeConfig so every process (uvicorn, 4 celery workers, the news
    engine) sees a change at its next call -- no restart.

    Fails to the DEFAULT, not to True. An unreachable DB must not silently
    re-enable a broker whose token is dead.
    """
    key = BROKER_FLAGS.get(name)
    if not key:
        return False
    default = BROKER_DEFAULTS.get(name, False)
    try:
        cfg = await RuntimeConfig.load(session)
        return bool(cfg._get(key, default))
    except Exception:
        return default


def broker_enabled_sync(name: str) -> bool:
    """Synchronous variant for call sites that have no session.

    Falls back to the .env flag, then the default. Used by the few Kite paths
    that are plain functions rather than async tasks.
    """
    default = BROKER_DEFAULTS.get(name, False)
    if name == "zerodha":
        return bool(getattr(settings, "ZERODHA_ENABLED", default))
    if name == "upstox":
        return bool(getattr(settings, "UPSTOX_ACCESS_TOKEN", "")) 
    return default


STRATEGY_FLAGS: dict[str, str] = {
    "india_trade_loop":    "strategy_india_trade_loop_enabled",
    "news_engine":         "strategy_news_engine_enabled",
    "pre_event_gap":       "strategy_pre_event_gap_enabled",
    "direct_news":         "strategy_direct_news_enabled",
    "tactical":            "strategy_tactical_enabled",
    "master_intelligence": "strategy_master_intelligence_enabled",
}


async def strategy_enabled(name: str, session: AsyncSession | None = None) -> bool:
    """Is this strategy switched on? Reads RuntimeConfig, so it is cross-process.

    FAILS OPEN by design: an unknown name, a missing row, or an unreachable DB
    all return True. These are *enable* switches, and the failure mode that
    matters is a database blip silently halting a trading system that the
    operator believes is running. Note this is the OPPOSITE posture to
    `tactical_risk`, which fails closed -- there the flag caps risk, so its
    absence must not permit more. Here its absence must not forbid everything.

    Pass `session` when the caller already has one; otherwise a short-lived
    session is opened. Callers on a hot path should pass their own.
    """
    key = STRATEGY_FLAGS.get(name)
    if key is None:
        logger.warning(f"[runtime_config] unknown strategy flag '{name}' — treating as enabled")
        return True

    try:
        if session is not None:
            cfg = await RuntimeConfig.load(session)
            return bool(cfg._get(key, True))

        from db.database import AsyncSessionLocal

        async with AsyncSessionLocal() as own:
            cfg = await RuntimeConfig.load(own)
            return bool(cfg._get(key, True))
    except Exception as exc:
        logger.warning(
            f"[runtime_config] strategy flag '{name}' unreadable ({exc}) — treating as enabled"
        )
        return True
