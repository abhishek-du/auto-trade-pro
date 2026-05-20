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
    # Indian market
    "indian_market_max_risk": float,
    "indian_intraday_sl_pct": float,
    # Feature flags
    "enable_fii_dii_analysis": bool,
    "enable_options_chain":    bool,
    "enable_india_vix":        bool,
    "enable_mutual_funds":     bool,
    "enable_ml_predictions":   bool,
    # Watchlists (stored as JSON arrays)
    "watchlist_forex":         list,
    "watchlist_stocks":        list,
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
    def indian_market_max_risk(self) -> float:
        return float(self._get("indian_market_max_risk", settings.INDIAN_MARKET_MAX_RISK))

    @property
    def indian_intraday_sl_pct(self) -> float:
        return float(self._get("indian_intraday_sl_pct", settings.INDIAN_INTRADAY_SL_PCT))

    @property
    def enable_fii_dii_analysis(self) -> bool:
        return bool(self._get("enable_fii_dii_analysis", settings.ENABLE_FII_DII_ANALYSIS))

    @property
    def enable_options_chain(self) -> bool:
        return bool(self._get("enable_options_chain", settings.ENABLE_OPTIONS_CHAIN))

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
            "enable_fii_dii_analysis": self.enable_fii_dii_analysis,
            "enable_options_chain":    self.enable_options_chain,
            "enable_india_vix":        self.enable_india_vix,
            "enable_mutual_funds":     self.enable_mutual_funds,
            "enable_ml_predictions":   self.enable_ml_predictions,
            "watchlist_forex":         self.watchlist_forex,
            "watchlist_stocks":        self.watchlist_stocks,
        }
