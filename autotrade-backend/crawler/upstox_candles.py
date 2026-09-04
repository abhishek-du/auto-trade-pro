"""Upstox historical candles — replaces Kite's /instruments/historical.

Returns EXACTLY the dict shape save_candles_to_db() and candle_resampler
already consume, so the 1m -> 5m/15m/1h resample pipeline is untouched:

    {"symbol", "timeframe", "open", "high", "low", "close", "volume", "timestamp"}

`timestamp` is a NAIVE UTC datetime, matching every existing candle row. Upstox
returns ISO-8601 with a +05:30 offset; converting to naive UTC here is the
single most important line in this module. Getting it wrong would shift every
bar by 5h30m -- the exact class of bug that once put 4,159 news rows in the
future (see crawler/news_crawler.py::_parse_nse_announcement_dt).

INTERVAL MAPPING (Kite -> Upstox V3)
    "1m"  / "minute"    ->  unit="minutes", interval="1"
    "5m"                ->  unit="minutes", interval="5"
    "15m"               ->  unit="minutes", interval="15"
    "1h" / "60minute"   ->  unit="hours",   interval="1"
    "1d" / "day"        ->  unit="days",    interval="1"

V3 splits history across two endpoints and this module tries BOTH, newest
first, because the boundary between them moves during the session:
    /v3/historical-candle/intraday/{key}/{unit}/{interval}     today only
    /v3/historical-candle/{key}/{unit}/{interval}/{to}/{from}  past days
"""
from __future__ import annotations

import datetime as _dt

import httpx

from utils.config import settings
from utils.logger import logger

_V3 = "https://api.upstox.com/v3/historical-candle"

# Kite timeframe -> (upstox unit, upstox interval)
# Both vocabularies map here on purpose. This project speaks "15m"; the old Kite
# call sites speak "15minute". A missing alias does NOT raise — .get() below
# falls back to the DAILY default, so a caller asking for 15-minute bars would
# quietly receive daily ones. That silent downgrade is far worse than an error,
# which is why every Kite-style name is listed explicitly.
_INTERVAL_MAP: dict[str, tuple[str, str]] = {
    "1m": ("minutes", "1"),   "minute":   ("minutes", "1"),
    "3m": ("minutes", "3"),   "3minute":  ("minutes", "3"),
    "5m": ("minutes", "5"),   "5minute":  ("minutes", "5"),
    "10m": ("minutes", "10"), "10minute": ("minutes", "10"),
    "15m": ("minutes", "15"), "15minute": ("minutes", "15"),
    "30m": ("minutes", "30"), "30minute": ("minutes", "30"),
    "1h": ("hours", "1"),     "60minute": ("hours", "1"),
    "1d": ("days", "1"),      "day":      ("days", "1"),
    "1wk": ("weeks", "1"),    "week":     ("weeks", "1"),
    "1mo": ("months", "1"),   "month":    ("months", "1"),
}

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.UPSTOX_ACCESS_TOKEN}",
            "Accept": "application/json"}


def _as_date(d) -> str:
    if isinstance(d, str):
        return d[:10]
    if isinstance(d, _dt.datetime):
        return d.date().isoformat()
    return d.isoformat()


# NSE regular session opens 09:15 IST. This is the canonical anchor for a DAILY
# bar's timestamp — see _to_naive_utc().
_SESSION_OPEN_IST = _dt.time(9, 15)


def _to_naive_utc(raw: str, *, daily: bool = False) -> _dt.datetime | None:
    """Upstox timestamp -> naive UTC, per this database's candle contract.

    THE CONTRACT
    ------------
    `candles.timestamp` is the instant the bar's interval OPENS, in naive UTC.
    For every intraday timeframe Upstox already sends exactly that, so the
    conversion is a plain offset shift:

        '2026-08-31T09:15:00+05:30'  ->  datetime(2026, 8, 31, 3, 45)

    DAILY IS DIFFERENT, AND THIS IS THE BUG THAT WAS HERE (fixed 2026-09-04)
    -----------------------------------------------------------------------
    For a daily bar Upstox sends MIDNIGHT IST of the session date:

        '2026-09-01T00:00:00+05:30'   -> session of Tuesday 01-Sep-2026

    That is a DATE LABEL, not an instant — no trading happens at 00:00 IST.
    Converting it as an instant gives 2026-08-31 18:30 UTC, whose `.date()` is
    31-Aug: **the day before the session it describes.** Verified against an
    independent NSE calendar: Monday 31-Aug's bar was stored dated Sunday
    30-Aug, which is not a trading session at all.

    The failure is quiet precisely because it is usually invisible: only a
    MONDAY session lands on a weekend date. On the other four weekdays the
    wrong date is still a plausible trading day, so a point-in-time join
    silently reads the previous session and looks entirely correct.

    So a daily label is re-anchored to the instant its session actually opened:

        '2026-09-01T00:00:00+05:30'  ->  datetime(2026, 9, 1, 3, 45)

    which makes `timestamp::date` the NSE session date directly, and puts daily
    bars on the same "interval open in naive UTC" footing as every intraday
    timeframe.
    """
    try:
        ts = _dt.datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None

    if daily:
        # .date() on an aware datetime is evaluated in ITS OWN offset (+05:30),
        # so this is the session date as Upstox labelled it — never re-derive it
        # from a UTC-shifted value, which is the original mistake.
        session_date = ts.date() if ts.tzinfo is not None else ts.date()
        open_ist = _dt.datetime.combine(session_date, _SESSION_OPEN_IST, tzinfo=_IST)
        return open_ist.astimezone(_dt.timezone.utc).replace(tzinfo=None)

    if ts.tzinfo is not None:
        ts = ts.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return ts


async def get_upstox_candles_for_range(
    symbol: str,
    from_date,
    to_date,
    interval: str = "1d",
    oi: bool = False,
) -> list[dict]:
    """Drop-in replacement for get_kite_candles_for_range().

    Same name shape, same arguments, same return. `oi` is accepted and ignored
    for signature compatibility: Upstox includes OI in the candle array for
    derivatives and this codebase trades cash equities only.
    """
    from crawler.upstox_quotes import _to_key, ensure_key_map
    from utils.symbols import normalize

    await ensure_key_map()
    key = _to_key(symbol)
    if not key:
        logger.debug(f"[upstox_candles] no instrument_key for {symbol}")
        return []

    unit, step = _INTERVAL_MAP.get(interval, _INTERVAL_MAP["1d"])
    sym_save = normalize(symbol)
    tf = interval if interval in _INTERVAL_MAP else "1d"
    # Normalise the stored timeframe label to this project's vocabulary so the
    # resampler and every downstream query keep matching on "1m"/"1d".
    tf = {"minute": "1m", "3minute": "3m", "5minute": "5m", "10minute": "10m",
          "15minute": "15m", "30minute": "30m", "60minute": "1h",
          "day": "1d", "week": "1wk", "month": "1mo"}.get(tf, tf)

    frm, to = _as_date(from_date), _as_date(to_date)
    today = _dt.datetime.now(_IST).date().isoformat()

    urls: list[str] = []
    # Intraday endpoint covers TODAY only; the historical one covers up to
    # yesterday. Asking both and merging is simpler and more robust than
    # deciding which side of the boundary a request falls on.
    if to >= today:
        urls.append(f"{_V3}/intraday/{key}/{unit}/{step}")
    if frm < today:
        hist_to = min(to, (_dt.date.fromisoformat(today) - _dt.timedelta(days=1)).isoformat())
        if frm <= hist_to:
            urls.append(f"{_V3}/{key}/{unit}/{step}/{hist_to}/{frm}")

    from crawler.upstox_limiter import acquire

    seen: set = set()
    out: list[dict] = []
    for url in urls:
        await acquire()
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(url, headers=_headers())
            if r.status_code != 200:
                logger.debug(f"[upstox_candles] {symbol} {url.rsplit('/',3)[0]} HTTP {r.status_code}")
                continue
            rows = (r.json().get("data") or {}).get("candles") or []
        except Exception as exc:
            logger.debug(f"[upstox_candles] {symbol} fetch failed: {type(exc).__name__}")
            continue

        # Upstox candle array: [timestamp, open, high, low, close, volume, oi]
        for c in rows:
            if not c or len(c) < 6:
                continue
            ts = _to_naive_utc(c[0], daily=(unit == "days"))
            if ts is None or ts in seen:
                continue
            seen.add(ts)
            out.append({
                "symbol":    sym_save,
                "timeframe": tf,
                "open":      float(c[1] or 0.0),
                "high":      float(c[2] or 0.0),
                "low":       float(c[3] or 0.0),
                "close":     float(c[4] or 0.0),
                "volume":    float(c[5] or 0),
                "timestamp": ts,
            })

    out.sort(key=lambda x: x["timestamp"])
    return out
