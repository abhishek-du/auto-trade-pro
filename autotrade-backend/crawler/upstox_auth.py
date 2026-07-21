import asyncio
import os
import sys
import time
from dotenv import load_dotenv, set_key

env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(env_path)

sys.path.insert(0, os.path.dirname(__file__))
from upstox_totp import UpstoxTOTP

def generate_and_save_upstox_token():
    # We use UPSTOX_USERNAME (mobile number) if provided, else fallback to UPSTOX_CLIENT_ID
    username = os.environ.get("UPSTOX_USERNAME") or os.environ.get("UPSTOX_CLIENT_ID")
    
    try:
        upx = UpstoxTOTP(
            username=username,
            password="dummy", # library requires this but it's often unused
            pin_code=os.environ.get("UPSTOX_PIN"),
            totp_secret=os.environ.get("UPSTOX_TOTP_SECRET"),
            client_id=os.environ.get("UPSTOX_API_KEY"),
            client_secret=os.environ.get("UPSTOX_API_SECRET"),
            redirect_uri=os.environ.get("UPSTOX_REDIRECT_URL", "http://localhost:8000/api/v1/upstox/callback"),
            debug=False
        )
        
        print(f"[*] Attempting Upstox Auto-Login for {username}...")
        response = upx.app_token.get_access_token()
        
        if response.success and response.data and response.data.access_token:
            token = response.data.access_token
            print("[+] Login Successful! Saving token to .env...")
            set_key(env_path, "UPSTOX_ACCESS_TOKEN", token)
            print("[+] UPSTOX_ACCESS_TOKEN updated successfully.")
            return True
        else:
            print("[-] Failed to generate token. Response:", response)
            return False
            
    except Exception as e:
        print("[-] Error during Upstox Auto-Login:")
        print(repr(e))
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Token reliability layer (Phase U1) — request-time freshness guard + auto-
# refresh + health status, so callers stop trusting "token string is present"
# as a proxy for "token actually works." Upstox access tokens expire daily;
# `settings.upstox_authenticated` (utils/config.py) only checks presence, never
# validity, and nothing previously refreshed the token automatically. This is
# the gap that would otherwise make any LLM-facing Upstox integration silently
# go dark for a large part of every day with no alert (see decision_engine.py
# fundamentals wiring — depends on this being solid first).
#
# State is in-memory / per-process, same tradeoff already accepted elsewhere
# in this codebase for PRICE_CACHE/SECTOR_CACHE (crawler/live_prices.py,
# crawler/sector_data.py) — each Celery worker / FastAPI process tracks its
# own view. Not shared across processes; not persisted across restarts.
# ══════════════════════════════════════════════════════════════════════════════

from utils.config import settings
from utils.logger import logger

_V2 = "https://api.upstox.com/v2"

_VERIFY_CACHE_SEC = 600   # don't re-ping Upstox more than once per 10 min per process

_state: dict = {
    "last_verified_ts":     0.0,    # last time we confirmed the token actually works
    "last_verified_ok":     False,
    "last_refresh_ts":      None,   # last successful auto-refresh (TOTP) timestamp
    "last_failure_ts":      None,
    "last_failure_reason":  None,
    "failure_count":        0,
}
_refresh_lock = asyncio.Lock()


async def verify_upstox_token() -> bool:
    """Ping Upstox with the current token to confirm it's actually still valid —
    a non-empty UPSTOX_ACCESS_TOKEN string does not mean the daily-expiring
    token still works."""
    if not settings.UPSTOX_ACCESS_TOKEN:
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                f"{_V2}/user/profile",
                headers={
                    "Authorization": f"Bearer {settings.UPSTOX_ACCESS_TOKEN}",
                    "Accept": "application/json",
                },
            )
        return r.status_code == 200
    except Exception as exc:
        logger.debug(f"[upstox/verify] token check failed: {exc}")
        return False


def _reload_token_from_env() -> None:
    """generate_and_save_upstox_token() only writes .env — a separate live
    process (Celery worker vs FastAPI backend) needs its own in-memory reload,
    the same gap tasks.zerodha_token_refresh works around for Kite."""
    try:
        from dotenv import dotenv_values
        env = dotenv_values(env_path)
        token = (env or {}).get("UPSTOX_ACCESS_TOKEN", "")
        if token:
            settings.UPSTOX_ACCESS_TOKEN = token
    except Exception as exc:
        logger.warning(f"[upstox] could not reload token from .env: {exc}")


def _mark_refresh_success() -> None:
    _state["last_refresh_ts"]     = time.time()
    _state["failure_count"]       = 0
    _state["last_failure_reason"] = None


def _mark_refresh_failure(reason: str) -> None:
    _state["last_failure_ts"]     = time.time()
    _state["last_failure_reason"] = reason
    _state["failure_count"]      += 1


async def refresh_upstox_token_with_retry(retries: int = 3) -> bool:
    """Run the headless TOTP auto-login with backoff, then propagate the fresh
    token into THIS process's in-memory settings. Does not send alerts itself —
    callers (the scheduled task) decide whether/how to notify on failure."""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            ok = await asyncio.to_thread(generate_and_save_upstox_token)
            if ok:
                _reload_token_from_env()
                _mark_refresh_success()
                return True
            last_exc = RuntimeError("generate_and_save_upstox_token returned False")
        except Exception as exc:
            last_exc = exc
        if attempt < retries:
            await asyncio.sleep(5 * attempt)   # polite backoff between attempts
    _mark_refresh_failure(str(last_exc))
    return False


async def ensure_upstox_token_fresh() -> bool:
    """Request-time freshness guard — call this instead of trusting
    settings.upstox_authenticated before any Upstox API call.

    - Trusts a recent successful verification for _VERIFY_CACHE_SEC, so a full
      candidate scan doesn't ping Upstox once per symbol.
    - Otherwise actually verifies the token against the API. On failure,
      attempts ONE auto-refresh, serialized behind a lock so concurrent
      callers (e.g. several LLM tool calls in the same cycle) don't each try
      to refresh independently.
    """
    if not settings.UPSTOX_ACCESS_TOKEN and not (settings.UPSTOX_API_KEY and settings.UPSTOX_API_SECRET):
        return False   # nothing to verify and no way to obtain a token

    now = time.time()
    if _state["last_verified_ok"] and (now - _state["last_verified_ts"]) < _VERIFY_CACHE_SEC:
        return True

    if await verify_upstox_token():
        _state["last_verified_ts"] = time.time()
        _state["last_verified_ok"] = True
        return True

    async with _refresh_lock:
        # Re-check inside the lock: another caller may have refreshed already
        # while this one was waiting.
        now2 = time.time()
        if _state["last_verified_ok"] and (now2 - _state["last_verified_ts"]) < _VERIFY_CACHE_SEC:
            return True
        ok = await refresh_upstox_token_with_retry(retries=2)
        _state["last_verified_ts"] = time.time()
        _state["last_verified_ok"] = ok
        return ok


def get_upstox_status() -> dict:
    """Health snapshot for /api/v1/upstox/status — replaces the old bare
    "token string present?" check with an honest healthy/stale/unavailable."""
    now = time.time()
    verified_recently = _state["last_verified_ok"] and (now - _state["last_verified_ts"]) < _VERIFY_CACHE_SEC
    if not settings.UPSTOX_ACCESS_TOKEN:
        status = "unavailable"
    elif verified_recently:
        status = "healthy"
    elif _state["last_verified_ts"] and not _state["last_verified_ok"]:
        status = "unavailable"
    else:
        status = "stale"   # not yet verified in this process, or cache expired
    return {
        "status":               status,
        "authenticated":        settings.upstox_authenticated,
        "last_verified_ts":     _state["last_verified_ts"] or None,
        "last_refresh_ts":      _state["last_refresh_ts"],
        "last_failure_ts":      _state["last_failure_ts"],
        "last_failure_reason":  _state["last_failure_reason"],
        "failure_count":        _state["failure_count"],
    }


if __name__ == "__main__":
    generate_and_save_upstox_token()
