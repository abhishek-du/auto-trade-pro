"""One-time migration: seed RuntimeSettings from paper_trading_config.json and .env defaults.

Run once after initial deployment:
    python migrate_settings.py

What it does:
  1. Reads paper_trading_config.json (if present) and maps legacy keys to RuntimeSettings keys.
  2. Seeds any key that is NOT already in the DB with the effective default
     (.env / config.py value, overridden by the JSON where applicable).
  3. Skips keys that already have a DB row — safe to re-run.

PAPER TRADING ONLY — all values are for simulated trading; no real money involved.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

# Allow running from the repo root without installing the package
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from db.models import RuntimeSettings
from utils.config import settings
from utils.runtime_config import RuntimeConfig, _KNOWN_KEYS


# ── Legacy key mapping ────────────────────────────────────────────────────────

_JSON_TO_RUNTIME: dict[str, str] = {
    "starting_balance":   "paper_trading_balance",
    "max_open_positions": "max_open_positions",
    # max_daily_loss_pct is stored as a percentage in JSON (e.g. 5.0)
    # but RuntimeSettings stores it as a fraction (e.g. 0.05)
}


def _load_json_overrides() -> dict:
    """Return a dict of RuntimeSettings key → value from paper_trading_config.json."""
    json_path = os.path.join(_ROOT, "paper_trading_config.json")
    if not os.path.exists(json_path):
        print(f"[migrate] {json_path} not found — using .env defaults only")
        return {}

    with open(json_path) as f:
        raw = json.load(f)

    overrides: dict = {}
    for json_key, rt_key in _JSON_TO_RUNTIME.items():
        if json_key in raw:
            overrides[rt_key] = raw[json_key]

    # max_daily_loss_pct stored as percentage → convert to fraction
    if "max_daily_loss_pct" in raw:
        overrides["max_daily_loss"] = float(raw["max_daily_loss_pct"]) / 100.0

    print(f"[migrate] Loaded {len(overrides)} override(s) from paper_trading_config.json: "
          f"{list(overrides.keys())}")
    return overrides


def _build_defaults(json_overrides: dict) -> dict:
    """Return the full set of seed values for every known key.

    JSON overrides take priority over .env / config.py defaults.
    """
    env_defaults = {
        "paper_trading_balance":   float(settings.PAPER_TRADING_BALANCE),
        "max_risk_per_trade":      float(settings.MAX_RISK_PER_TRADE),
        "max_open_positions":      int(settings.MAX_OPEN_POSITIONS),
        "max_daily_loss":          float(settings.MAX_DAILY_LOSS),
        "atr_multiplier":          float(settings.ATR_MULTIPLIER),
        "min_risk_reward":         float(settings.MIN_RISK_REWARD),
        "indian_market_max_risk":  float(settings.INDIAN_MARKET_MAX_RISK),
        "indian_intraday_sl_pct":  float(settings.INDIAN_INTRADAY_SL_PCT),
        "enable_fii_dii_analysis": bool(settings.ENABLE_FII_DII_ANALYSIS),
        "enable_options_chain":    bool(settings.ENABLE_OPTIONS_CHAIN),
        "enable_india_vix":        bool(settings.ENABLE_INDIA_VIX),
        "enable_mutual_funds":     bool(settings.ENABLE_MUTUAL_FUNDS),
        "enable_ml_predictions":   bool(settings.ENABLE_ML_PREDICTIONS),
        "watchlist_forex":         list(settings.forex_symbols),
        "watchlist_stocks":        list(settings.stock_symbols),
    }
    merged = {**env_defaults, **json_overrides}
    return merged


async def run_migration() -> None:
    engine = create_async_engine(
        settings.DATABASE_URL,
        connect_args={"statement_cache_size": 0},
        echo=False,
    )
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    json_overrides = _load_json_overrides()
    seed_values    = _build_defaults(json_overrides)

    async with async_session() as session:
        # Fetch existing keys to avoid overwriting deliberate user edits
        existing = {
            row.key
            for row in (await session.execute(select(RuntimeSettings))).scalars().all()
        }

        inserted = 0
        skipped  = 0
        for key, value in seed_values.items():
            if key in existing:
                print(f"[migrate] SKIP  {key!r} — already in DB")
                skipped += 1
                continue
            try:
                await RuntimeConfig.set(session, key, value)
                print(f"[migrate] SEED  {key!r} = {value!r}")
                inserted += 1
            except (ValueError, TypeError) as exc:
                print(f"[migrate] ERROR {key!r}: {exc}")

        await session.commit()

    print(
        f"\n[migrate] Done — inserted={inserted}  skipped={skipped}  "
        f"total_keys={len(seed_values)}"
    )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_migration())
