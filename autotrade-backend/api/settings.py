"""Settings API — read/write runtime config from the runtime_settings DB table.

All values fall back to .env / config.py defaults when not set in the DB,
so the system works out of the box without any manual seeding.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from api.auth import require_auth
from utils.config import settings
from utils.runtime_config import RuntimeConfig, _KNOWN_KEYS

router = APIRouter(tags=["Settings"])


# ── Pydantic models ───────────────────────────────────────────────────────────

class RuntimeSettingsOut(BaseModel):
    paper_trading_balance:   float
    max_risk_per_trade:      float
    max_open_positions:      int
    max_daily_loss:          float
    atr_multiplier:          float
    min_risk_reward:         float
    indian_market_max_risk:  float
    indian_intraday_sl_pct:  float
    enable_fii_dii_analysis: bool
    enable_options_chain:    bool
    enable_india_vix:        bool
    enable_mutual_funds:     bool
    enable_ml_predictions:   bool
    watchlist_forex:         list[str]
    watchlist_stocks:        list[str]
    paper_mode:              bool
    paper_confidence_threshold: float
    live_confidence_threshold:  float
    max_portfolio_risk:      float
    min_cash_buffer:         float
    agent_default_product:      str   # "CNC" | "MIS"
    agent_confidence_threshold: int   # 0–100
    equity_short_enabled:       bool  # allow SELL signals (MIS intraday only)
    intraday_enabled:           bool  # enable intraday MIS trades (required for shorts)


class SettingsPatch(BaseModel):
    """Partial update — only supplied keys are written to the DB."""
    paper_trading_balance:   float | None = None
    max_risk_per_trade:      float | None = None
    max_open_positions:      int   | None = None
    max_daily_loss:          float | None = None
    atr_multiplier:          float | None = None
    min_risk_reward:         float | None = None
    indian_market_max_risk:  float | None = None
    indian_intraday_sl_pct:  float | None = None
    enable_fii_dii_analysis: bool  | None = None
    enable_options_chain:    bool  | None = None
    enable_india_vix:        bool  | None = None
    enable_mutual_funds:     bool  | None = None
    enable_ml_predictions:   bool  | None = None
    watchlist_forex:         list[str] | None = None
    watchlist_stocks:        list[str] | None = None
    paper_mode:              bool  | None = None
    paper_confidence_threshold: float | None = None
    live_confidence_threshold:  float | None = None
    max_portfolio_risk:      float | None = None
    min_cash_buffer:         float | None = None
    agent_default_product:      str  | None = None   # "CNC" | "MIS"
    agent_confidence_threshold: int  | None = None   # 0–100
    equity_short_enabled:       bool | None = None   # allow SELL signals
    intraday_enabled:           bool | None = None   # enable intraday MIS trades


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "/",
    response_model=RuntimeSettingsOut,
    summary="Get all runtime settings (DB values merged with .env defaults)",
)
async def get_settings(db: AsyncSession = Depends(get_db)):
    cfg = await RuntimeConfig.load(db)
    return RuntimeSettingsOut(**cfg.to_dict())


@router.patch(
    "/",
    response_model=RuntimeSettingsOut,
    summary="Partially update runtime settings — only provided keys are changed",
)
async def patch_settings(
    payload: SettingsPatch,
    db: AsyncSession = Depends(get_db),
):
    updates: dict[str, Any] = {
        k: v for k, v in payload.model_dump().items() if v is not None
    }
    if not updates:
        raise HTTPException(status_code=400, detail="No settings provided to update")

    try:
        await RuntimeConfig.set_many(db, updates)
        await db.commit()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    cfg = await RuntimeConfig.load(db)
    return RuntimeSettingsOut(**cfg.to_dict())


@router.delete(
    "/{key}",
    summary="Reset a single setting to its .env / config.py default",
)
async def reset_setting(key: str, db: AsyncSession = Depends(get_db)):
    if key not in _KNOWN_KEYS:
        raise HTTPException(status_code=404, detail=f"Unknown setting key: {key!r}")

    from sqlalchemy import delete
    from db.models import RuntimeSettings as RS

    await db.execute(delete(RS).where(RS.key == key))
    await db.commit()
    return {"reset": key, "message": "Setting removed; .env default will be used"}


@router.get(
    "/keys",
    summary="List all known configurable setting keys and their value types",
)
async def list_setting_keys():
    return {k: t.__name__ for k, t in _KNOWN_KEYS.items()}


# ── Trade mode toggle (paper ↔ live) ─────────────────────────────────────────

@router.get("/mode", summary="Get current trade mode (PAPER | LIVE | DRY_RUN)")
async def get_trade_mode(db: AsyncSession = Depends(get_db)):
    from engine.decision_router import resolve_mode
    mode = await resolve_mode(db)
    return {
        "mode":      mode.value,
        "is_paper":  mode.value == "PAPER",
        "is_live":   mode.value == "LIVE",
        "is_dry_run": mode.value == "DRY_RUN",
    }


class ModeToggle(BaseModel):
    paper_mode: bool
    confirm:    str | None = None  # must equal "I_UNDERSTAND_REAL_MONEY" to go live


@router.post("/mode", summary="Switch between paper and live trading at runtime")
async def set_trade_mode(
    body: ModeToggle,
    db: AsyncSession = Depends(get_db),
    _admin: str = Depends(require_auth),   # security: PAPER↔LIVE switch requires admin JWT
):
    # Safety gate — going live requires explicit confirmation string
    if body.paper_mode is False:
        if body.confirm != "I_UNDERSTAND_REAL_MONEY":
            raise HTTPException(
                status_code=400,
                detail=(
                    "Switching to LIVE mode requires explicit confirmation. "
                    "POST {'paper_mode': false, 'confirm': 'I_UNDERSTAND_REAL_MONEY'}"
                ),
            )
        # Also verify Zerodha is actually connected
        from utils.config import settings as _s
        if not (_s.ZERODHA_ENABLED and _s.ZERODHA_ACCESS_TOKEN):
            raise HTTPException(
                status_code=409,
                detail="Cannot go LIVE: Zerodha is not connected. Login first.",
            )

    await RuntimeConfig.set(db, "paper_mode", body.paper_mode)
    await db.commit()
    return {"mode": "PAPER" if body.paper_mode else "LIVE", "updated": True}
