#!/usr/bin/env python3
"""Automate Zerodha daily token refresh without a browser.

Drives the Kite Connect OAuth flow via httpx + pyotp:
  1. POST credentials → get request_id
  2. POST TOTP → session cookie
  3. Follow OAuth redirect chain → extract request_token
  4. Call our backend /callback → exchanges for access_token + persists it

Run at 8:00 AM IST (2:30 AM UTC) on weekdays, before NSE market open.

Usage:
    python3 scripts/refresh_zerodha_token.py
    python3 scripts/refresh_zerodha_token.py --backend http://localhost:8000
"""
import argparse
import logging
import os
import sys
import time
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("zerodha_refresh")

_KITE_BASE    = "https://kite.zerodha.com"
_MAX_REDIRECT = 10


def _totp(secret: str) -> str:
    import pyotp
    return pyotp.TOTP(secret).now()


def _login(client, user_id: str, password: str) -> str:
    """Password login — returns request_id needed for 2FA step."""
    r = client.post(
        f"{_KITE_BASE}/api/login",
        data={"user_id": user_id, "password": password},
    )
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "success":
        raise RuntimeError(f"Login failed: {body.get('message', body)}")
    log.info("Password login successful")
    return body["data"]["request_id"]


def _twofa(client, user_id: str, request_id: str, totp_secret: str) -> None:
    """Submit TOTP — server sets session cookie on success."""
    code = _totp(totp_secret)
    log.info("TOTP generated (code redacted for security)")
    r = client.post(
        f"{_KITE_BASE}/api/twofa",
        data={
            "user_id":      user_id,
            "request_id":   request_id,
            "twofa_value":  code,
            "twofa_type":   "totp",
            "skip_totp":    "false",
        },
    )
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "success":
        raise RuntimeError(f"2FA failed: {body.get('message', body)}")
    log.info("TOTP verified ✓")


def _get_request_token(client, api_key: str) -> str:
    """Drive OAuth redirect chain until we see request_token in the Location URL."""
    url = f"{_KITE_BASE}/connect/login?v=3&api_key={api_key}"

    for hop in range(_MAX_REDIRECT):
        r = client.get(url, follow_redirects=False)
        log.debug(f"Hop {hop+1}: {r.status_code} {url[:80]}")

        if r.status_code not in (301, 302, 303, 307, 308):
            # Zerodha returned a page instead of redirect — probably needs extra auth.
            # Check page content for clues.
            raise RuntimeError(
                f"Expected redirect at hop {hop+1}, got {r.status_code}. "
                f"Content preview: {r.text[:200]}"
            )

        location = r.headers.get("location", "")
        if not location:
            raise RuntimeError(f"No Location header at hop {hop+1}")

        # Resolve relative redirects
        if location.startswith("/"):
            location = _KITE_BASE + location

        # Check if we've reached the callback URL with request_token
        params = parse_qs(urlparse(location).query)
        if "request_token" in params:
            token = params["request_token"][0]
            log.info(f"Got request_token: {token[:8]}…")
            return token

        # Stay on Zerodha domain; stop if we hit our localhost callback
        if "localhost" in location or "127.0.0.1" in location:
            # Shouldn't have request_token missing — bad state
            raise RuntimeError(
                f"Reached callback URL without request_token: {location}"
            )

        url = location

    raise RuntimeError("Exhausted redirect hops without getting request_token")


def _exchange_token(request_token: str, backend: str) -> bool:
    """Call our backend callback — it exchanges request_token for access_token."""
    import httpx
    r = httpx.get(
        f"{backend}/api/v1/zerodha/callback",
        params={"request_token": request_token, "action": "login"},
        follow_redirects=True,
        timeout=60,
        verify=False,
    )
    # The callback returns HTTP 200 + the "Zerodha Connected" success page on
    # success, and HTTP 400 (_html_error) on failure. Match the real success
    # marker — the old check looked for "success"/"access_token", neither of
    # which appears in the success HTML, so it ALWAYS reported failure even when
    # the token was persisted correctly.
    text = r.text.lower()
    success = r.status_code == 200 and (
        "zerodha connected" in text or "prices are now active" in text
    )
    if not success:
        log.warning(f"Callback returned {r.status_code}: {r.text[:200]}")
    return success


def main(backend: str = "http://localhost:8000") -> None:
    from utils.config import settings

    user_id     = settings.ZERODHA_USER_ID
    password    = settings.ZERODHA_PASSWORD
    totp_secret = settings.ZERODHA_TOTP_SECRET
    api_key     = settings.ZERODHA_API_KEY

    missing = [k for k, v in {
        "ZERODHA_USER_ID":     user_id,
        "ZERODHA_PASSWORD":    password,
        "ZERODHA_TOTP_SECRET": totp_secret,
        "ZERODHA_API_KEY":     api_key,
    }.items() if not v]
    if missing:
        log.error(f"Missing env vars: {', '.join(missing)}")
        sys.exit(1)

    import httpx

    with httpx.Client(
        timeout=20,
        verify=False,   # network has TLS inspection (self-signed in chain)
        headers={
            "User-Agent":      (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Origin":          "https://kite.zerodha.com",
            "Referer":         "https://kite.zerodha.com/",
            "X-Kite-Version":  "3",
            "Accept":          "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        },
    ) as client:
        log.info(f"Step 1 — logging in as {user_id}...")
        request_id = _login(client, user_id, password)

        log.info("Step 2 — submitting TOTP...")
        _twofa(client, user_id, request_id, totp_secret)

        log.info("Step 3 — following OAuth redirect chain...")
        request_token = _get_request_token(client, api_key)

    log.info("Step 4 — exchanging request_token via backend callback...")
    ok = _exchange_token(request_token, backend)

    if ok:
        log.info("✓ Zerodha token refreshed successfully. Valid until 6:00 AM IST tomorrow.")
    else:
        log.error("✗ Token exchange failed — check backend logs.")
        sys.exit(1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Refresh Zerodha access token automatically")
    ap.add_argument(
        "--backend",
        default="http://localhost:8000",
        help="AutoTrade Pro backend URL (default: http://localhost:8000)",
    )
    args = ap.parse_args()
    main(args.backend)
