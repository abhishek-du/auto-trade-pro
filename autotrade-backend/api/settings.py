"""
Settings API — read/write paper-trading runtime config from a JSON file.
Separate from .env so users can change risk params without restarting.
"""
import json
import os
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["Settings"])

_CONFIG_FILE = Path(__file__).parent.parent / "paper_trading_config.json"

_DEFAULTS = {
    "starting_balance":   1000.0,
    "max_position_size":  10.0,
    "stop_loss_pct":      2.0,
    "take_profit_pct":    4.0,
    "max_daily_loss_pct": 5.0,
    "max_open_positions": 5,
    "watchlist": ["BTC/USD", "ETH/USD", "AAPL", "TSLA", "NVDA", "EUR/USD"],
}


class SettingsPayload(BaseModel):
    starting_balance:   float = 1000.0
    max_position_size:  float = 10.0
    stop_loss_pct:      float = 2.0
    take_profit_pct:    float = 4.0
    max_daily_loss_pct: float = 5.0
    max_open_positions: int   = 5
    watchlist:          list[str] = []


def _load() -> dict:
    if _CONFIG_FILE.exists():
        try:
            return {**_DEFAULTS, **json.loads(_CONFIG_FILE.read_text())}
        except Exception:
            pass
    return _DEFAULTS.copy()


def _save(data: dict) -> None:
    _CONFIG_FILE.write_text(json.dumps(data, indent=2))


@router.get("/", summary="Get current paper-trading settings")
async def get_settings():
    return _load()


@router.post("/", summary="Update paper-trading settings")
async def save_settings(payload: SettingsPayload):
    data = payload.model_dump()
    _save(data)
    return {"saved": True, **data}
