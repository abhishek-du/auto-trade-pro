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
from dataclasses import dataclass
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
    OTHER_INDEX = "other_index"               # ^BSESN and friends — not NSE
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

# NSE indices Upstox serves under the NSE_INDEX segment. Kept in step with
# crawler.upstox_quotes._INDEX_KEYS, which holds the actual instrument keys;
# duplicated as a plain frozenset so this module stays import-light and
# synchronous.
_NSE_INDEX_SYMBOLS = frozenset({"^NSEI", "^NSEBANK", "^INDIAVIX"})


def classify_instrument(symbol: str | None) -> InstrumentClass:
    """Classify a candle symbol. Never raises; unknown input is UNKNOWN."""
    if not symbol or not isinstance(symbol, str):
        return InstrumentClass.UNKNOWN
    s = symbol.strip()
    if s.startswith("^"):
        # Only indices Upstox actually serves under NSE_INDEX can join the
        # canonical daily pipeline. ^BSESN is a BSE index with no Upstox key:
        # classifying it as NSE_INDEX would route it to a canonical fetch that
        # returns nothing and, because that path fails closed, would silently
        # delete its data from /india/market-indices and the regime engine's
        # _REGIME_DAILY_SYMBOLS. Keep it on its legacy source.
        return (InstrumentClass.NSE_INDEX if s in _NSE_INDEX_SYMBOLS
                else InstrumentClass.OTHER_INDEX)
    if not s.endswith(".NS"):
        return InstrumentClass.NON_NSE
    base = s[:-3]
    if not base:
        return InstrumentClass.UNKNOWN
    # The `-BE` / `-SM` / `-GS` / `-SG` suffix is what marks a non-ordinary
    # series. A LEADING digit does not: 3MINDIA, 5PAISA, 63MOONS, 360ONE,
    # 20MICRONS, 21STCENMGM, 3BBLACKBIO, 3IINFOLTD and 3PLAND are ordinary NSE
    # equities, and an earlier `base[:1].isdigit()` clause classified all nine as
    # NON_EQUITY_SERIES — which exempted them from the daily contract entirely.
    # The suffix test still separates 3IINFOLTD.NS (equity) from
    # 3IINFOLTD-BE.NS (its trade-for-trade series), so dropping the digit rule
    # costs nothing.
    if _SERIES_SUFFIX.search(base):
        return InstrumentClass.NON_EQUITY_SERIES
    if _ETF_PAT.search(base):
        return InstrumentClass.ETF_OR_INAV
    return InstrumentClass.NSE_EQUITY


class DailyPolicy(str, Enum):
    """What the daily contract requires of an instrument class.

    Step 2F. The contract previously governed NSE_EQUITY and returned
    `(True, None)` for everything else, on the reasoning that indices and ETFs
    "keep their own legacy handling". That exemption is what let
    sync_regime_daily_candles_kite write `00:00` bars for ^NSEI, ^NSEBANK and
    NIFTYBEES.NS every five minutes for months: the guard could not reject what
    it did not govern, and those three feed the regime gate.

    Each class now states its policy explicitly. Nothing is silently identical
    to anything else, and nothing is silently exempt.
    """

    CANONICAL = "canonical"          # must be 03:45 UTC on a real NSE session
    LEGACY_EXEMPT = "legacy_exempt"  # NSE-traded, contract NOT enforced, see below
    EXCLUDED = "excluded"            # never accepted into the daily table at all


# One row per class. A new InstrumentClass without an entry here raises on
# lookup rather than defaulting to "allow" — the failure mode that created this
# whole line of work.
_DAILY_POLICY: dict[InstrumentClass, DailyPolicy] = {
    # The regime + prediction dataset. All three carry the canonical contract.
    InstrumentClass.NSE_EQUITY:        DailyPolicy.CANONICAL,
    InstrumentClass.NSE_INDEX:         DailyPolicy.CANONICAL,   # ^NSEI, ^NSEBANK
    InstrumentClass.ETF_OR_INAV:       DailyPolicy.CANONICAL,   # NIFTYBEES.NS
    # -BE (trade-for-trade), -SM (SME), -BZ and -GS series. EXEMPT, and the
    # exemption is a measured constraint rather than an oversight: Upstox's
    # instrument master carries no key for any of these symbol forms (checked
    # 2026-09-07 — SIMBHALS-BZ.NS, AAREYDRUGS-BE.NS, AAKAAR-SM.NS and
    # 719GS2060-GS.NS all resolve to None against a 2,723-symbol map), so the
    # canonical fetch returns nothing for them. Enforcing the contract would not
    # move them onto 03:45; it would silently drop the daily bars of ~1,163
    # symbols that currently arrive on the legacy path.
    #
    # They are excluded from the model dataset instead, which is where the risk
    # actually lies. Revisit if Upstox ever publishes keys for these series.
    InstrumentClass.NON_EQUITY_SERIES: DailyPolicy.LEGACY_EXEMPT,
    # Not NSE. ^BSESN is a BSE index; .BO is the BSE cash segment. Neither
    # belongs in the NSE daily table, and the NSE-only work of 2026-08-28 said
    # so — this makes the choke point enforce it too.
    InstrumentClass.OTHER_INDEX:       DailyPolicy.EXCLUDED,
    InstrumentClass.NON_NSE:           DailyPolicy.EXCLUDED,
    InstrumentClass.UNKNOWN:           DailyPolicy.EXCLUDED,
}

# Classes whose daily bars may be used to train or score a model.
MODEL_ELIGIBLE_CLASSES = frozenset({
    InstrumentClass.NSE_EQUITY,
    InstrumentClass.NSE_INDEX,
    InstrumentClass.ETF_OR_INAV,
})


def daily_policy(symbol: str | None) -> DailyPolicy:
    """The daily-write policy for `symbol`'s instrument class."""
    return _DAILY_POLICY[classify_instrument(symbol)]


def is_model_eligible(symbol: str | None) -> bool:
    """May this symbol's daily bars feed a prediction dataset?"""
    return classify_instrument(symbol) in MODEL_ELIGIBLE_CLASSES


# ── The NSE sessions that fall on a weekend ──────────────────────────────────
#
# Diwali Muhurat, Union Budget Saturdays, and three disaster-recovery sessions.
# NSE genuinely traded on each of these, so the plain weekday rule refuses real
# data. Ten of them land in the last ten years.
#
# CURATED ON PURPOSE. It is not derived from the candle data and not inferred
# from whatever the API happens to return: letting a price source assert its own
# trading calendar makes the validation circular — the thing being checked would
# be supplying the check. Each entry is a documented exchange event.
#
# This is the ONE copy. The backfill writer and engine.daily_series both read it
# from here, so writer and reader cannot drift apart — which is exactly the
# asymmetry Step 2H.1 found, where the writer could store these sessions and the
# reader then dropped them.
#
# NOT wired into the contract's own defaults. resolve_daily_session_date() and
# validate_canonical_daily_equity_candle() still default to extra_open=None, so
# no live writer changes behaviour because this exists. Supplying it to the
# production writer is a separate decision — see the 2026-11-08 Muhurat note in
# the Step 2H.1 report.
NSE_SPECIAL_SESSIONS: frozenset[str] = frozenset({
    "2016-10-30",  # Diwali Muhurat (Sunday)
    "2019-10-27",  # Diwali Muhurat (Sunday)
    "2020-02-01",  # Union Budget (Saturday)
    "2020-11-14",  # Diwali Muhurat (Saturday)
    "2023-11-12",  # Diwali Muhurat (Sunday)
    "2024-01-20",  # special live / disaster-recovery session (Saturday)
    "2024-03-02",  # special live / disaster-recovery session (Saturday)
    "2024-05-18",  # special live / disaster-recovery session (Saturday)
    "2025-02-01",  # Union Budget (Saturday)
    "2026-02-01",  # Union Budget (Sunday)
})


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
    extra_open: set[str] | None = None,
    now_utc: _dt.datetime | None = None,
) -> tuple[bool, str | None]:
    """(ok, reason). Applies the equity daily contract; passes everything else.

    Returns (True, None) for any row the contract does not govern — a non-daily
    timeframe, or a non-equity instrument — so this can sit in the hot path of
    every write without special-casing at the call sites.
    """
    if candle.get("timeframe") != "1d":
        return True, None

    sym = candle.get("symbol")
    klass = classify_instrument(sym)
    policy = _DAILY_POLICY.get(klass)

    if policy is None:                       # a class with no declared policy
        return False, f"{klass.value}: no daily policy declared — refusing"

    if policy is DailyPolicy.EXCLUDED:
        # ^BSESN and .BO. Historical rows are left alone; this only stops NEW
        # non-NSE data entering the NSE daily table.
        return False, f"{klass.value} is excluded from the NSE daily table"

    if policy is DailyPolicy.LEGACY_EXEMPT:
        # Declared exemption, not a silent one — see _DAILY_POLICY for why these
        # series cannot be served canonically. They are kept out of the model
        # dataset by MODEL_ELIGIBLE_CLASSES instead.
        return True, None

    # CANONICAL carries the full timestamp contract below.
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

    # `extra_open` names dates NSE traded that the weekday rule would refuse:
    # Diwali Muhurat, Budget Saturdays, and the occasional disaster-recovery
    # session. is_nse_trading_session has always supported it; until Step 2H the
    # parameter simply was not plumbed through to here, so those sessions were
    # unconditionally rejected at the write path.
    #
    # It stays OPTIONAL and defaults to None, which is exactly the previous
    # behaviour — no existing caller changes. A caller that knows the calendar
    # (the one-off backfill does, for ten years of it) can now pass it instead
    # of losing real sessions. Ten such dates fall in the last ten years, and
    # the legacy series carries all ten, so a canonical series that could not
    # represent them could not replace the legacy one.
    if not is_nse_trading_session(ts.date(), holidays, extra_open):
        kind = "weekend" if ts.date().weekday() >= 5 else "NSE holiday"
        return False, f"session date {ts.date()} is a {kind}"

    return True, None


def filter_canonical_candles(
    candles: list[dict],
    *,
    holidays: set[str] | None = None,
    extra_open: set[str] | None = None,
    source: str = "unknown",
) -> tuple[list[dict], dict[str, int]]:
    """Split a batch into (accepted, rejection-reason counts).

    `source` is recorded in the log so every dropped bar has a deterministic
    provenance path — which writer produced it, and why it was refused.
    """
    ok: list[dict] = []
    rejected: dict[str, int] = {}
    for c in candles:
        good, why = validate_canonical_daily_equity_candle(
            c, holidays=holidays, extra_open=extra_open)
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


# ── Read-side: resolving a stored timestamp back to its NSE session ──────────
#
# Step 2D.0 proved that a reader which groups daily rows by `timestamp::date`
# is ACTIVELY WRONG while the legacy and canonical series coexist:
#
#   RELIANCE  2026-09-02 03:45  close 1313.1   -> session 2026-09-02
#   RELIANCE  2026-09-02 18:30  close 1302.5   -> session 2026-09-03
#
# Both share the calendar date 2026-09-02. Ordering `timestamp DESC` and keeping
# the first row hands the NEXT session's close to 2026-09-02 — a one-session
# look-ahead in every metric derived from it. 2,276 symbol/date pairs currently
# hold both conventions.
#
# So the read side gets its own explicit contract. Nothing here guesses: a
# timestamp whose convention is not recognised is refused, not approximated.

class DailyConvention(str, Enum):
    CANONICAL_0345 = "canonical_0345"   # 03:45 UTC — session open, current writer
    LEGACY_1830 = "legacy_1830"         # 18:30 UTC — session date MINUS one
    LEGACY_0000 = "legacy_0000"         # 00:00 UTC — session date, pre-split series
    UNKNOWN = "unknown"


class SessionStatus(str, Enum):
    VALID_CANONICAL = "valid_canonical"
    VALID_LEGACY = "valid_legacy"
    # Equity 00:00 rows are the DEAD pre-split series. Their DATE is sound, their
    # PRICES are not comparable with the live series — callers must not silently
    # mix them into a return calculation.
    VALID_LEGACY_UNADJUSTED = "valid_legacy_unadjusted"
    UNRESOLVED = "unresolved"
    REJECTED_FUTURE = "rejected_future"
    REJECTED_NON_SESSION = "rejected_non_session"


@dataclass(frozen=True)
class SessionResolution:
    session_date: _dt.date | None
    convention: DailyConvention
    status: SessionStatus
    reason: str | None = None

    @property
    def usable(self) -> bool:
        return self.status in (
            SessionStatus.VALID_CANONICAL,
            SessionStatus.VALID_LEGACY,
            SessionStatus.VALID_LEGACY_UNADJUSTED,
        )


def next_nse_session(
    after: _dt.date,
    holidays: set[str] | None = None,
    extra_open: set[str] | None = None,
    *,
    max_lookahead: int = 15,
) -> _dt.date | None:
    """The first NSE trading session strictly after `after`.

    Calendar-driven, never "+1 day": a Friday 18:30 bar belongs to Monday, and a
    bar stored the day before a closure belongs to the next OPEN session, which
    may be several days later. `max_lookahead` bounds the walk so a malformed
    calendar cannot loop.
    """
    d = after + _dt.timedelta(days=1)
    for _ in range(max_lookahead):
        if is_nse_trading_session(d, holidays, extra_open):
            return d
        d += _dt.timedelta(days=1)
    return None


def resolve_daily_session_date(
    symbol: str | None,
    timeframe: str,
    timestamp: _dt.datetime,
    *,
    holidays: set[str] | None = None,
    extra_open: set[str] | None = None,
    now_utc: _dt.datetime | None = None,
) -> SessionResolution:
    """Which NSE session does this stored daily bar represent?

    Returns the session date, the convention it was recognised as, and a status
    the caller can audit — never a bare date, so an ambiguous row cannot be
    mistaken for a confident one.

    Recognised conventions, and ONLY these:

        03:45 UTC  -> session = timestamp.date()          (canonical)
        18:30 UTC  -> session = next valid NSE session     (legacy, calendar-driven)
        00:00 UTC  -> session = timestamp.date()          (legacy; unadjusted for equity)

    Anything else is UNRESOLVED. A future timestamp or a resolved date that is
    not a trading session is REJECTED. 00:00 is NEVER silently treated as 18:30 —
    they are separate historical series.
    """
    if timeframe != "1d":
        return SessionResolution(None, DailyConvention.UNKNOWN,
                                 SessionStatus.UNRESOLVED,
                                 f"timeframe {timeframe!r} is not daily")
    if not isinstance(timestamp, _dt.datetime) or timestamp.tzinfo is not None:
        return SessionResolution(None, DailyConvention.UNKNOWN,
                                 SessionStatus.UNRESOLVED,
                                 "timestamp must be a naive datetime")

    ref = now_utc or _dt.datetime.utcnow()
    if timestamp > ref + _dt.timedelta(minutes=5):
        return SessionResolution(None, DailyConvention.UNKNOWN,
                                 SessionStatus.REJECTED_FUTURE,
                                 f"timestamp {timestamp} is in the future")

    t = timestamp.time()
    klass = classify_instrument(symbol)

    if t == CANONICAL_DAILY_UTC_TIME:
        d = timestamp.date()
        if not is_nse_trading_session(d, holidays, extra_open):
            return SessionResolution(None, DailyConvention.CANONICAL_0345,
                                     SessionStatus.REJECTED_NON_SESSION,
                                     f"{d} is not an NSE trading session")
        return SessionResolution(d, DailyConvention.CANONICAL_0345,
                                 SessionStatus.VALID_CANONICAL)

    if t == _dt.time(18, 30):
        d = next_nse_session(timestamp.date(), holidays, extra_open)
        if d is None:
            return SessionResolution(None, DailyConvention.LEGACY_1830,
                                     SessionStatus.UNRESOLVED,
                                     "no NSE session found within the lookahead window")
        return SessionResolution(d, DailyConvention.LEGACY_1830,
                                 SessionStatus.VALID_LEGACY)

    if t == _dt.time(0, 0):
        d = timestamp.date()
        if not is_nse_trading_session(d, holidays, extra_open):
            return SessionResolution(None, DailyConvention.LEGACY_0000,
                                     SessionStatus.REJECTED_NON_SESSION,
                                     f"{d} is not an NSE trading session")
        # Index 00:00 is the CURRENT index source and is split-irrelevant.
        # Equity 00:00 is the dead pre-split series — flagged, not refused, so a
        # caller can still align dates while knowing not to mix the prices.
        status = (SessionStatus.VALID_LEGACY
                  if klass is InstrumentClass.NSE_INDEX
                  else SessionStatus.VALID_LEGACY_UNADJUSTED)
        return SessionResolution(d, DailyConvention.LEGACY_0000, status)

    return SessionResolution(None, DailyConvention.UNKNOWN,
                             SessionStatus.UNRESOLVED,
                             f"unrecognised daily time-of-day {t}")


# Preference when two rows resolve to the SAME session: the canonical bar is
# authoritative, then the offset legacy series, then the unadjusted one. This is
# only ever applied to rows already agreed to describe one session — it is not a
# tiebreaker between different sessions.
_CONVENTION_RANK = {
    DailyConvention.CANONICAL_0345: 0,
    DailyConvention.LEGACY_1830: 1,
    DailyConvention.LEGACY_0000: 2,
    DailyConvention.UNKNOWN: 9,
}


def convention_rank(c: DailyConvention) -> int:
    return _CONVENTION_RANK.get(c, 9)
