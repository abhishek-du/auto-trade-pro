"""Zerodha KiteConnect v3 — raw HTTP client.

Uses httpx directly (no kiteconnect library) so every request is fully
observable and controllable.  All endpoints target https://api.kite.trade.

BASE URL:  https://api.kite.trade
Required header on every request:  X-Kite-Version: 3
Auth header:  Authorization: token {api_key}:{access_token}

Public singletons
-----------------
get_kite_client()    → KiteClient
update_kite_token()  → persists new access_token to .env + settings
"""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

import httpx

from utils.config import settings
from utils.logger import logger


class KiteClient:
    """Thin async wrapper around the KiteConnect v3 REST API."""

    BASE    = "https://api.kite.trade"
    HEADERS = {"X-Kite-Version": "3"}

    def __init__(self, api_key: str, access_token: str = "") -> None:
        self.api_key      = api_key
        self.access_token = access_token

    # ── Auth headers ──────────────────────────────────────────────────────────

    @property
    def auth_header(self) -> dict:
        return {"Authorization": f"token {self.api_key}:{self.access_token}"}

    @property
    def headers(self) -> dict:
        return {**self.HEADERS, **self.auth_header}

    # ── Low-level transport ───────────────────────────────────────────────────

    async def _get(self, path: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{self.BASE}{path}",
                headers=self.headers,
                params=params or {},
            )
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "success":
                raise ValueError(f"Kite error: {data.get('message', 'Unknown error')}")
            return data["data"]

    async def _post(self, path: str, data: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{self.BASE}{path}",
                headers=self.headers,
                data=data or {},
            )
            r.raise_for_status()
            result = r.json()
            if result.get("status") != "success":
                raise ValueError(f"Kite error: {result.get('message', 'Unknown error')}")
            return result["data"]

    async def _delete(self, path: str, params: dict | None = None) -> dict | None:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.delete(
                f"{self.BASE}{path}",
                headers=self.headers,
                params=params or {},
            )
            r.raise_for_status()
            return r.json().get("data")

    async def _post_no_auth(self, path: str, data: dict) -> dict:
        """POST without Authorization header — used only for session/token exchange."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{self.BASE}{path}",
                headers=self.HEADERS,
                data=data,
            )
            r.raise_for_status()
            result = r.json()
            if result.get("status") != "success":
                raise ValueError(f"Kite login error: {result.get('message')}")
            return result["data"]

    # ── Authentication ────────────────────────────────────────────────────────

    def get_login_url(self) -> str:
        return f"https://kite.zerodha.com/connect/login?v=3&api_key={self.api_key}"

    async def generate_session(self, request_token: str) -> dict:
        """Exchange request_token for access_token.

        Kite requires a SHA-256 checksum of api_key + request_token + api_secret.
        """
        checksum = hashlib.sha256(
            f"{self.api_key}{request_token}{settings.ZERODHA_API_SECRET}".encode()
        ).hexdigest()
        data = await self._post_no_auth("/session/token", {
            "api_key":       self.api_key,
            "request_token": request_token,
            "checksum":      checksum,
        })
        self.access_token = data["access_token"]
        return data

    async def invalidate_session(self) -> bool:
        await self._delete(
            "/session/token",
            params={"api_key": self.api_key, "access_token": self.access_token},
        )
        return True

    # ── User ──────────────────────────────────────────────────────────────────

    async def get_profile(self) -> dict:
        return await self._get("/user/profile")

    async def get_margins(self, segment: str | None = None) -> dict:
        path = f"/user/margins/{segment}" if segment else "/user/margins"
        return await self._get(path)

    # ── Market data ───────────────────────────────────────────────────────────

    async def get_quote(self, instruments: list[str]) -> dict:
        """Full quote for a list of instruments ('NSE:RELIANCE', etc.)."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{self.BASE}/quote",
                headers=self.headers,
                params={"i": instruments},
            )
            r.raise_for_status()
            return r.json()["data"]

    async def get_ohlc(self, instruments: list[str]) -> dict:
        """Lightweight OHLC + last price."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{self.BASE}/quote/ohlc",
                headers=self.headers,
                params={"i": instruments},
            )
            r.raise_for_status()
            return r.json()["data"]

    async def get_ltp(self, instruments: list[str]) -> dict:
        """Last traded price only — lightest endpoint."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{self.BASE}/quote/ltp",
                headers=self.headers,
                params={"i": instruments},
            )
            r.raise_for_status()
            return r.json()["data"]

    async def get_historical_data(
        self,
        instrument_token: int,
        from_date: str,      # "2024-01-01"
        to_date: str,        # "2024-12-31"
        interval: str,       # "minute", "60minute", "day", etc.
        continuous: bool = False,
        oi: bool = False,
    ) -> list[dict]:
        params = {
            "from":       from_date,
            "to":         to_date,
            "interval":   interval,
            "continuous": 1 if continuous else 0,
            "oi":         1 if oi else 0,
        }
        data = await self._get(
            f"/instruments/historical/{instrument_token}/{interval}",
            params=params,
        )
        candles = []
        for c in data.get("candles", []):
            candles.append({
                "timestamp": c[0],
                "open":   c[1],
                "high":   c[2],
                "low":    c[3],
                "close":  c[4],
                "volume": c[5],
            })
        return candles

    async def get_instruments(self, exchange: str | None = None) -> list[dict]:
        """Download the full instrument CSV (~30 MB). exchange: NSE, BSE, NFO, MCX or None."""
        path = f"/instruments/{exchange}" if exchange else "/instruments"
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.get(f"{self.BASE}{path}", headers=self.headers)
            reader = csv.DictReader(io.StringIO(r.text))
            return list(reader)

    # ── Portfolio ─────────────────────────────────────────────────────────────

    async def get_holdings(self) -> list[dict]:
        return await self._get("/portfolio/holdings")

    async def get_positions(self) -> dict:
        return await self._get("/portfolio/positions")

    async def convert_position(
        self,
        tradingsymbol: str,
        exchange: str,
        transaction_type: str,
        position_type: str,
        quantity: int,
        old_product: str,
        new_product: str,
    ) -> bool:
        await self._post("/portfolio/positions", {
            "tradingsymbol":    tradingsymbol,
            "exchange":         exchange,
            "transaction_type": transaction_type,
            "position_type":    position_type,
            "quantity":         quantity,
            "old_product":      old_product,
            "new_product":      new_product,
        })
        return True

    # ── Orders ────────────────────────────────────────────────────────────────

    async def place_order(
        self,
        tradingsymbol: str,
        exchange: str,
        transaction_type: str,        # "BUY" | "SELL"
        quantity: int,
        order_type: str = "MARKET",   # "MARKET" | "LIMIT" | "SL" | "SL-M"
        product: str = "CNC",         # "CNC" | "MIS" | "NRML"
        price: float = 0.0,
        trigger_price: float = 0.0,
        validity: str = "DAY",
        variety: str = "regular",
        tag: str = "AUTOTRADE_PRO",
    ) -> str:
        params: dict = {
            "tradingsymbol":    tradingsymbol,
            "exchange":         exchange,
            "transaction_type": transaction_type,
            "quantity":         quantity,
            "order_type":       order_type,
            "product":          product,
            "validity":         validity,
            "tag":              tag,
        }
        if price > 0:
            params["price"] = price
        if trigger_price > 0:
            params["trigger_price"] = trigger_price
        data = await self._post(f"/orders/{variety}", params)
        return data["order_id"]

    async def modify_order(
        self,
        order_id: str,
        variety: str = "regular",
        quantity: int | None = None,
        price: float | None = None,
        order_type: str | None = None,
        trigger_price: float | None = None,
        validity: str | None = None,
    ) -> str:
        params: dict = {}
        if quantity is not None:     params["quantity"]      = quantity
        if price is not None:        params["price"]         = price
        if order_type is not None:   params["order_type"]    = order_type
        if trigger_price is not None: params["trigger_price"] = trigger_price
        if validity is not None:     params["validity"]      = validity
        data = await self._post(f"/orders/{variety}/{order_id}", params)
        return data["order_id"]

    async def cancel_order(self, order_id: str, variety: str = "regular") -> str:
        data = await self._delete(f"/orders/{variety}/{order_id}")
        return data["order_id"]

    async def get_orders(self) -> list[dict]:
        return await self._get("/orders")

    async def get_order_history(self, order_id: str) -> list[dict]:
        return await self._get(f"/orders/{order_id}")

    async def get_trades(self) -> list[dict]:
        return await self._get("/trades")

    # ── Mutual Funds ──────────────────────────────────────────────────────────

    async def get_mf_orders(self) -> list[dict]:
        return await self._get("/mf/orders")

    async def get_mf_holdings(self) -> list[dict]:
        return await self._get("/mf/holdings")

    async def get_mf_instruments(self) -> list[dict]:
        return await self._get("/mf/instruments")


# ── Module-level singleton ────────────────────────────────────────────────────

_kite_client: KiteClient | None = None


def get_kite_client() -> KiteClient:
    global _kite_client
    if _kite_client is None:
        _kite_client = KiteClient(
            api_key=settings.ZERODHA_API_KEY,
            access_token=settings.ZERODHA_ACCESS_TOKEN,
        )
    return _kite_client


def update_kite_token(access_token: str) -> None:
    """Update singleton access_token, write to .env, and flip ZERODHA_ENABLED."""
    global _kite_client
    settings.ZERODHA_ACCESS_TOKEN = access_token
    settings.ZERODHA_ENABLED      = True
    if _kite_client:
        _kite_client.access_token = access_token
    _update_env_file("ZERODHA_ACCESS_TOKEN", access_token)
    _update_env_file("ZERODHA_ENABLED", "true")
    logger.info("[KiteClient] access_token updated and persisted to .env")


def clear_kite_token() -> None:
    """Clear the stored access token (called on logout or expiry)."""
    global _kite_client
    settings.ZERODHA_ACCESS_TOKEN = ""
    settings.ZERODHA_ENABLED      = False
    if _kite_client:
        _kite_client.access_token = ""
    _update_env_file("ZERODHA_ACCESS_TOKEN", "")
    _update_env_file("ZERODHA_ENABLED", "false")
    logger.info("[KiteClient] access_token cleared")


def _update_env_file(key: str, value: str) -> None:
    """Idempotently set KEY=VALUE in the .env file."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    lines = env_path.read_text().splitlines()
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
            lines[i] = f"{key}={value}"
            updated   = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n")
