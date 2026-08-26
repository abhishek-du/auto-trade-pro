"""Settings API — read/write runtime config from the runtime_settings DB table.

All values fall back to .env / config.py defaults when not set in the DB,
so the system works out of the box without any manual seeding.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from api.auth import require_auth
from utils.config import settings
from utils.logger import logger
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
    # enable_options_chain removed 2026-08-19: an F&O leftover. It was still
    # REQUIRED here but RuntimeConfig.to_dict() never supplied it (and it is not
    # in _KNOWN_KEYS), so GET /api/v1/settings/ returned 500 on every call since
    # the F&O removal in 91457d7 -- the Settings page was simply broken.
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
    dependencies=[Depends(require_auth)],
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
    dependencies=[Depends(require_auth)],
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


# ─────────────────────────────────────────────────────────────────────────────
# Strategy execution toggles
# ─────────────────────────────────────────────────────────────────────────────

class StrategyFlagsUpdate(BaseModel):
    """One or more strategy toggles. Unknown names are rejected, not ignored.

    Silently dropping an unknown key would let the UI believe it disabled a
    strategy that is still trading -- the worst possible failure for this
    screen. See `update_strategy_flags`.
    """

    flags: dict[str, bool] = Field(
        ...,
        description="Short strategy name -> enabled. e.g. {\"tactical\": false}",
    )


@router.get(
    "/strategies",
    summary="Current on/off state of every strategy execution toggle",
)
async def get_strategy_flags(db: AsyncSession = Depends(get_db)):
    """Read-only, so no auth -- consistent with GET /settings/.

    Values come from RuntimeConfig (DB-backed), so this reflects what every
    process sees, not this uvicorn worker's in-memory settings.
    """
    from utils.runtime_config import STRATEGY_FLAGS, RuntimeConfig

    cfg = await RuntimeConfig.load(db)
    return {
        "flags": {name: bool(cfg._get(key, True)) for name, key in STRATEGY_FLAGS.items()},
        # The UI shows this so an operator is not left guessing whether a
        # toggle needs a deploy to take hold.
        "effective": "immediate",
        "note": "Stored in RuntimeConfig (DB). Every process picks the change up "
                "on its next decision; no restart required.",
    }


@router.post(
    "/strategies",
    summary="Enable or disable strategies at runtime",
    dependencies=[Depends(require_auth)],
)
async def update_strategy_flags(
    payload: StrategyFlagsUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Flip one or more strategy toggles. Takes effect immediately.

    AUTHENTICATED, unlike its sibling `PATCH /settings/` (audit D4). Disabling
    every strategy halts automatic trading, which is exactly the kind of action
    that must not be anonymous.
    """
    from utils.runtime_config import STRATEGY_FLAGS, RuntimeConfig

    unknown = sorted(set(payload.flags) - set(STRATEGY_FLAGS))
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown strategy name(s): {', '.join(unknown)}. "
                f"Valid names: {', '.join(sorted(STRATEGY_FLAGS))}"
            ),
        )

    updates = {STRATEGY_FLAGS[name]: bool(val) for name, val in payload.flags.items()}
    await RuntimeConfig.set_many(db, updates)

    cfg = await RuntimeConfig.load(db)
    current = {name: bool(cfg._get(key, True)) for name, key in STRATEGY_FLAGS.items()}
    logger.warning(
        "[settings] strategy toggles changed: "
        + ", ".join(f"{n}={'ON' if v else 'OFF'}" for n, v in sorted(payload.flags.items()))
    )
    if not any(current.values()):
        logger.warning("[settings] ALL strategies are now disabled — no path can originate a trade")

    return {"status": "updated", "flags": current,
            "all_disabled": not any(current.values())}
