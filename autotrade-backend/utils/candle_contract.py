"""THE canonical contract for daily NSE equity candles. One implementation.

WHY THIS MODULE EXISTS
----------------------
Step 2C.1 found THREE live daily timestamp conventions writing to one table:

    03:45 UTC   crawler/upstox_candles.py            (canonical)
    18:30 UTC   india_price_feed.fetch_nse_candles   (Upstox branch)
    00:00 UTC   india_price_feed.fetch_nse_candles   (yfinance fallback)

Eleven code paths can persist a candle. Fixing them one by one leaves the next
new writer free to invent a fourth convention, so the rule lives HERE and is
enforced at the single persistence choke point (price_feed.save_candles_to_db)
rather than restated at each call site.

THE CONTRACT — NSE EQUITY, timeframe '1d'
-----------------------------------------
Upstox sends a daily bar as a DATE LABEL at midnight IST:

    '2026-08-31T00:00:00+05:30'   ->  the session of Monday 31-Aug-2026

The canonical internal form is the instant that session OPENED:

    09:15 IST  ==  03:45 UTC  on the session date

so `candles.timestamp::date` IS the NSE trading-session date, and daily bars sit
on the same "interval open in naive UTC" footing as every intraday timeframe.

Forbidden for a NEWLY WRITTEN equity daily bar:
  * 00:00 UTC  — the dead, pre-split legacy series
  * 18:30 UTC  — the one-day-offset legacy series
  * any other time of day
  * a future timestamp
  * a weekend or NSE-holiday session date (when a calendar is available)

NON-EQUITY IS NOT COVERED BY THAT RULE. Indices (^NSEI), ETFs and INAV feeds are
classified separately and pass through: they are not what this system trades, and
forcing an equity contract onto them would reject legitimate index data.

FAIL CLOSED. A daily equity bar that cannot be proven canonical is dropped and
logged, never silently rewritten — silent repair is how three conventions
accumulated in the first place.
"""
from __future__ import annotations

import datetime as _dt
import re
from enum import Enum

from utils.logger import logger

# 09:15 IST session open, expressed in UTC. The single source of this constant.
#
# CAVEAT (Step 2D.0): this anchors the REGULAR session. NSE also runs a Diwali
# Muhurat session of roughly 18:00-19:00 IST (2026-11-08), which does not open at
# 09:15 — anchoring that day's bar to 03:45 UTC would place it ~8 hours before
# trading actually began. One token session a year; flagged rather than silently
# mis-anchored, pending a decision in Step 2D.
CANONICAL_DAILY_UTC_TIME = _dt.time(3, 45)
IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))

# Legacy conventions, named so rejection messages are self-explanatory.
_LEGACY_TIMES = {
    _dt.time(0, 0):   "00:00 UTC — the dead pre-split legacy daily series",
    _dt.time(18, 30): "18:30 UTC — the one-day-offset legacy daily series",
}


class InstrumentClass(str, Enum):
    NSE_EQUITY = "nse_equity"
    NSE_INDEX = "nse_index"
    ETF_OR_INAV = "etf_or_inav"
    NON_EQUITY_SERIES = "non_equity_series"   # -SG / -N0 / -GS / -TB / -SM ...
    NON_NSE = "non_nse"                       # .BO and anything else
    UNKNOWN = "unknown"


# ETF / INAV names carry no company; they are the rows that produced every OHLC
# invariant violation in the Step 2B audit (open=0.0 alongside high=1047).
_ETF_PAT = re.compile(
    r"(ETF|BEES|IETF|INAV|LIQUID|GILT|NIFTY|SENSEX|GOLD|SILVER|BETA|MOM\d*|"
    r"VALUE|QUAL|ALPHA|MAFANG|PSUBNK|CPSE|BOND|LOWVOL|DIVOPP|MIDCAP|SMALLCAP)",
    re.I,
)
_SERIES_SUFFIX = re.compile(r"-[A-Z0-9]{2}$")


def classify_instrument(symbol: str | None) -> InstrumentClass:
    """Classify a candle symbol. Never raises; unknown input is UNKNOWN."""
    if not symbol or not isinstance(symbol, str):
        return InstrumentClass.UNKNOWN
    s = symbol.strip()
    if s.startswith("^"):
        return InstrumentClass.NSE_INDEX
    if not s.endswith(".NS"):
        return InstrumentClass.NON_NSE
    base = s[:-3]
    if not base:
        return InstrumentClass.UNKNOWN
    if _SERIES_SUFFIX.search(base) or base[:1].isdigit():
        return InstrumentClass.NON_EQUITY_SERIES
    if _ETF_PAT.search(base):
        return InstrumentClass.ETF_OR_INAV
    return InstrumentClass.NSE_EQUITY


def nse_closed_dates(holidays_payload: list[dict] | None) -> set[str]:
    """Dates NSE is ACTUALLY SHUT, from an Upstox /v2/market/holidays payload.

    THIS IS NOT "every date the endpoint returns" (fixed 2026-09-04, Step 2D.0).

    That endpoint mixes three kinds of entry, and only one of them closes NSE:

        TRADING_HOLIDAY     NSE shut                       -> exclude the session
        SETTLEMENT_HOLIDAY  NSE TRADES NORMALLY            -> a real session
        SPECIAL_TIMING      NSE trades on a shifted clock  -> a real session

    Measured on the 2026 calendar: 22 entries, of which **6 are days NSE is
    open** — Id-E-Milad (26-Aug), Gudi Padwa, Budget Day, Diwali Laxmi Pujan and
    two more. RELIANCE traded 5,744,474 shares on 26-Aug-2026.

    Treating the raw list as closures would therefore discard six genuine
    trading sessions a year, and they are exactly the unusual sessions a
    prediction model most wants.

    The authority is each entry's `open_exchanges`: if NSE appears there, the
    market was open, whatever the entry is called.
    """
    closed: set[str] = set()
    for h in holidays_payload or []:
        d = h.get("date")
        if not d:
            continue
        opens = {e.get("exchange") for e in (h.get("open_exchanges") or [])}
        if "NSE" not in opens:
            closed.add(d)
    return closed


def nse_extra_open_dates(holidays_payload: list[dict] | None) -> set[str]:
    """Dates NSE trades that a weekday test would wrongly reject.

    NSE runs SPECIAL WEEKEND SESSIONS (discovered 2026-09-04, Step 2D.0):

        2026-02-01  Sunday  Budget Day Session   09:15-15:30 IST  (a FULL session)
        2026-11-08  Sunday  Diwali Laxmi Pujan   18:00-19:00 IST  (Muhurat)

    A weekday-only session test discards both. Budget Day in particular is a
    normal-length session whose bar a prediction model would very much want.
    """
    extra: set[str] = set()
    for h in holidays_payload or []:
        d = h.get("date")
        if not d:
            continue
        opens = {e.get("exchange") for e in (h.get("open_exchanges") or [])}
        if "NSE" in opens and _dt.date.fromisoformat(d).weekday() >= 5:
            extra.add(d)
    return extra


def is_nse_trading_session(
    d: _dt.date,
    holidays: set[str] | None = None,
    extra_open: set[str] | None = None,
) -> bool:
    """Was NSE open on this date?

    `holidays`   — dates NSE was ACTUALLY SHUT. Build with nse_closed_dates(),
                   never from the raw /market/holidays date list, or six real
                   sessions a year are thrown away (settlement holidays and
                   special-timing days trade normally).
    `extra_open` — weekend dates NSE nonetheless traded. Build with
                   nse_extra_open_dates(). Without it, Budget Day and Muhurat
                   sessions are wrongly rejected.

    The calendar is authoritative over the weekday heuristic, in both
    directions. Both sets are injected rather than fetched so this stays
    synchronous and testable.
    """
    iso = d.isoformat()
    if extra_open and iso in extra_open:
        return True                      # calendar overrides the weekday rule
    if d.weekday() >= 5:
        return False
    if holidays and iso in holidays:
        return False
    return True


def validate_canonical_daily_equity_candle(
    candle: dict,
    *,
    holidays: set[str] | None = None,
    now_utc: _dt.datetime | None = None,
) -> tuple[bool, str | None]:
    """(ok, reason). Applies the equity daily contract; passes everything else.

    Returns (True, None) for any row the contract does not govern — a non-daily
    timeframe, or a non-equity instrument — so this can sit in the hot path of
    every write without special-casing at the call sites.
    """
    if candle.get("timeframe") != "1d":
        return True, None

    klass = classify_instrument(candle.get("symbol"))
    if klass is not InstrumentClass.NSE_EQUITY:
        # Indices/ETFs/debt keep their own (legacy) handling; BSE is rejected by
        # the NSE-only gate elsewhere, not here.
        return True, None

    ts = candle.get("timestamp")
    if not isinstance(ts, _dt.datetime):
        return False, f"timestamp is {type(ts).__name__}, expected datetime"
    if ts.tzinfo is not None:
        return False, "timestamp is timezone-aware; the candles table is naive UTC"

    t = ts.time()
    if t != CANONICAL_DAILY_UTC_TIME:
        why = _LEGACY_TIMES.get(t, f"{t} — not the canonical session open")
        return False, f"non-canonical daily time {why}; expected 03:45 UTC"

    ref = now_utc or _dt.datetime.utcnow()
    if ts > ref + _dt.timedelta(minutes=5):
        return False, f"future timestamp {ts} (now {ref})"

    if not is_nse_trading_session(ts.date(), holidays):
        kind = "weekend" if ts.date().weekday() >= 5 else "NSE holiday"
        return False, f"session date {ts.date()} is a {kind}"

    return True, None


def filter_canonical_candles(
    candles: list[dict],
    *,
    holidays: set[str] | None = None,
    source: str = "unknown",
) -> tuple[list[dict], dict[str, int]]:
    """Split a batch into (accepted, rejection-reason counts).

    `source` is recorded in the log so every dropped bar has a deterministic
    provenance path — which writer produced it, and why it was refused.
    """
    ok: list[dict] = []
    rejected: dict[str, int] = {}
    for c in candles:
        good, why = validate_canonical_daily_equity_candle(c, holidays=holidays)
        if good:
            ok.append(c)
        else:
            key = (why or "unknown").split(";")[0][:70]
            rejected[key] = rejected.get(key, 0) + 1
    if rejected:
        logger.error(
            f"[candle_contract] REJECTED {sum(rejected.values())} of {len(candles)} "
            f"daily equity bars from source={source!r}: {rejected}"
        )
    return ok, rejected
