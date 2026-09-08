#!/usr/bin/env python
"""ONE-OFF shadow backfill of NSE daily history from Upstox.

NOT imported by Celery, beat, or any production module. Run by hand.

WHAT THIS IS FOR
----------------
The legacy 00:00 daily series (4.85M rows, 2016->2026) is corporate-action
adjusted price-only, with volume left unadjusted, and its adjustment factors are
unrecoverable from the database. It is therefore excluded from the model
dataset. Before anyone deletes it, we need proof that a canonical Upstox series
can stand in its place.

This script builds that proof. It is a SHADOW backfill: purely additive, writing
canonical 03:45 rows through the same choke point production uses. It never
deletes, updates, normalizes or migrates an existing row.

PRICE BASIS — THE UPSTOX HISTORICAL CANONICAL SERIES
---------------------------------------------------
This writes the Upstox historical canonical series, using Upstox's historical
adjustment semantics. It is NOT described as "raw" or "unadjusted": Upstox
applies its own corporate-action adjustment, so RELIANCE's 2016-09-07 close
comes back as 242.50 against a nominal ~1,050 at the time, reflecting the 2017
and 2024 bonuses.

The property that matters is REPRODUCIBILITY. Re-fetching sessions this codebase
already wrote from Upstox reproduces them exactly — 84 of 86 rows identical for
each of RELIANCE, TCS, INFY, HDFCBANK and ICICIBANK at price ratio 1.00000, the
only movement being post-settlement volume revision on the newest bars. That
check is a first-class validation step here (see refetch comparison), not a
one-off measurement.

The series does differ from the LEGACY one for some symbols by a constant factor
(INFY ~2.16%, volume identical). That difference lives in the legacy data and is
the reason this backfill exists.

No transformation is applied. Whatever the production parser returns is what
gets validated and stored.

USAGE
    python scripts/oneoff_upstox_backfill.py --pilot --dry-run
    python scripts/oneoff_upstox_backfill.py --pilot
    python scripts/oneoff_upstox_backfill.py --full --resume
    python scripts/oneoff_upstox_backfill.py --symbols RELIANCE.NS,TCS.NS
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ── status vocabulary ────────────────────────────────────────────────────────
SUCCESS, NO_DATA, NO_KEY, ERROR = "SUCCESS", "NO_DATA", "NO_KEY", "ERROR"

DEFAULT_YEARS = 10
DEFAULT_CONCURRENCY = 20
DEFAULT_CHECKPOINT = "backfill_log.csv"
MAX_ATTEMPTS = 4
BACKOFF_BASE = 1.5

# The curated weekend-session calendar now lives in the shared contract module
# so the writer here and engine.daily_series read the SAME list — the reader
# asymmetry found in Step 2H.1 was exactly what two copies would reproduce.
from utils.candle_contract import NSE_SPECIAL_SESSIONS   # noqa: E402

# Eight NSE equities across the sectors whose data shapes differ most, plus two
# ETFs. The classes are validated and REPORTED SEPARATELY: the replacement
# target is NSE equity, and pooling them would blur what the pilot proves.
PILOT_EQUITY = [
    "RELIANCE.NS",    # large-cap, 2017 + 2024 bonus — the corporate-action case
    "HDFCBANK.NS",    # financial, post-merger
    "ICICIBANK.NS",   # financial
    "TCS.NS",         # IT
    "INFY.NS",        # IT — the symbol whose legacy basis differs by ~2.16%
    "LT.NS",          # industrial / capital goods
    "ITC.NS",         # FMCG, demerger history
    "SBIN.NS",        # PSU bank
]
PILOT_ETF = [
    "NIFTYBEES.NS",   # the regime ETF, already on the canonical writer
    "BANKBEES.NS",    # banking-sector ETF
]
PILOT_SYMBOLS = PILOT_EQUITY + PILOT_ETF

CSV_FIELDS = ["timestamp", "symbol", "instrument_key", "instrument_class", "status",
              "rows_received", "rows_valid", "rows_saved", "rows_existing",
              "first_session", "last_session", "conventions", "duplicates",
              "weekend_violations", "ohlc_violations", "refetch_compared",
              "refetch_identical", "dropped_invalid", "error_type", "error_message",
              "elapsed_seconds", "attempt"]


@dataclass
class Result:
    symbol: str
    instrument_key: str = ""
    status: str = ERROR
    rows_received: int = 0        # returned by the API
    rows_valid: int = 0           # passed canonical validation
    rows_saved: int = 0           # actually inserted
    rows_existing: int = 0        # valid but already present (ON CONFLICT DO NOTHING)
    first_session: str = ""
    last_session: str = ""
    error_type: str = ""
    error_message: str = ""
    elapsed_seconds: float = 0.0
    attempt: int = 1
    # per-symbol quality detail
    instrument_class: str = ""
    conventions: str = ""         # distinct timestamp times seen
    duplicates: int = 0
    weekend_violations: int = 0
    ohlc_violations: int = 0
    refetch_compared: int = 0     # existing 03:45 rows checked against a fresh fetch
    refetch_identical: int = 0
    dropped_invalid: int = 0      # bars refused individually (--skip-invalid-candles)


@dataclass
class Stats:
    requests: int = 0
    retries: int = 0
    rows_received: int = 0
    rows_saved: int = 0
    rows_rejected: int = 0
    violations: dict = field(default_factory=lambda: {
        "not_0345": 0, "weekend_or_holiday": 0, "duplicate_session": 0,
        "ohlc": 0, "future": 0, "volume": 0})


# ── universe ─────────────────────────────────────────────────────────────────

async def build_universe(session, *, include_etf: bool = False) -> list[tuple[str, str]]:
    """(symbol, instrument_key) for every backfillable NSE instrument.

    Source is the same instrument master production uses — kite_instruments,
    NSE-only, non-empty instrument_key — filtered by the shared contract
    classifier. Keys are never manufactured: a symbol with no key is reported
    NO_KEY rather than guessed at.
    """
    from sqlalchemy import text
    from utils.candle_contract import InstrumentClass, classify_instrument

    rows = (await session.execute(text(
        "SELECT tradingsymbol, instrument_key FROM kite_instruments "
        "WHERE exchange = 'NSE' AND instrument_key IS NOT NULL AND instrument_key <> '' "
        "ORDER BY tradingsymbol"
    ))).all()

    allowed = {InstrumentClass.NSE_EQUITY}
    if include_etf:
        allowed.add(InstrumentClass.ETF_OR_INAV)

    out, excluded = [], {}
    for r in rows:
        sym = f"{r.tradingsymbol}.NS"
        klass = classify_instrument(sym)
        if klass in allowed:
            out.append((sym, r.instrument_key))
        else:
            excluded[klass.value] = excluded.get(klass.value, 0) + 1
    if excluded:
        print(f"  universe exclusions by class (recorded, not silent): {excluded}")
    return out


# ── validation ───────────────────────────────────────────────────────────────

def validate_batch(symbol: str, candles: list[dict], stats: Stats,
                   res: "Result | None" = None) -> tuple[bool, str]:
    """Every check from the brief, before a single row is persisted.

    All-or-nothing per symbol: a batch with one bad bar is rejected whole rather
    than partially saved, so a symbol is never left half-backfilled.
    """
    from utils.candle_contract import validate_canonical_daily_equity_candle as validate

    if not candles:
        return False, "empty batch"

    now = dt.datetime.utcnow()
    seen: set[dt.date] = set()

    for c in candles:
        ts = c.get("timestamp")
        if not isinstance(ts, dt.datetime):
            return False, f"timestamp is {type(ts).__name__}, expected datetime"

        # 4. canonical session open, exactly
        if ts.time() != dt.time(3, 45):
            stats.violations["not_0345"] += 1
            return False, f"non-canonical timestamp {ts.time()} (expected 03:45)"

        # 9. no future sessions
        if ts > now + dt.timedelta(minutes=5):
            stats.violations["future"] += 1
            return False, f"future session {ts}"

        # 8. unique session dates
        d = ts.date()
        if d in seen:
            stats.violations["duplicate_session"] += 1
            if res: res.duplicates += 1
            return False, f"duplicate session date {d}"
        seen.add(d)

        # 6. OHLC validity
        o, h, l, cl = c.get("open"), c.get("high"), c.get("low"), c.get("close")
        if None in (o, h, l, cl) or any(x != x for x in (o, h, l, cl)):
            stats.violations["ohlc"] += 1
            if res: res.ohlc_violations += 1
            return False, f"missing or NaN OHLC on {d}"
        if not (l <= o <= h and l <= cl <= h and l <= h):
            stats.violations["ohlc"] += 1
            if res: res.ohlc_violations += 1
            return False, f"OHLC invariant broken on {d}: o={o} h={h} l={l} c={cl}"

        # 7. volume validity
        v = c.get("volume")
        if v is None or v != v or v < 0:
            stats.violations["volume"] += 1
            return False, f"invalid volume {v} on {d}"

        # 5. weekend / holiday, via the shared contract. NSE_SPECIAL_SESSIONS
        # names the ten weekend dates NSE actually traded; without it the
        # contract refuses them and a ten-year history cannot be stored.
        ok, why = validate(c, extra_open=NSE_SPECIAL_SESSIONS, now_utc=now)
        if not ok:
            key = ("weekend_or_holiday" if ("weekend" in (why or "") or "holiday" in (why or ""))
                   else "not_0345")
            stats.violations[key] += 1
            if res and key == "weekend_or_holiday": res.weekend_violations += 1
            return False, why or "contract rejected the bar"

    return True, ""


async def compare_against_existing(symbol: str, candles: list[dict],
                                  res: "Result") -> None:
    """Reproducibility check: existing Upstox-written 03:45 rows vs this fetch.

    This is the strongest available price-basis validation. Rows already in the
    table for this symbol at 03:45 were written by the production Upstox path;
    if a fresh fetch of the same sessions disagrees, the source is retro-adjusting
    and the whole premise of replacing legacy history is unsound. Measured
    2026-09-07: 84/86 identical per symbol, the rest post-settlement volume.

    Read-only. Never mutates and never blocks the save.
    """
    try:
        from sqlalchemy import text
        from db.database import AsyncSessionLocal

        fresh = {c["timestamp"].date(): c for c in candles}
        async with AsyncSessionLocal() as s:
            rows = (await s.execute(text(
                "SELECT timestamp, close, volume FROM candles "
                "WHERE symbol = :s AND timeframe = '1d' "
                "  AND to_char(timestamp,'HH24:MI') = '03:45'"), {"s": symbol})).all()
        for ts, close, vol in rows:
            f = fresh.get(ts.date())
            if not f:
                continue
            res.refetch_compared += 1
            if abs(f["close"] - close) < 0.011 and abs(f["volume"] - (vol or 0)) < 1.5:
                res.refetch_identical += 1
    except Exception:
        pass          # diagnostic only — must never fail a backfill



def partition_batch(symbol: str, candles: list[dict], stats: Stats,
                    res: "Result | None" = None) -> tuple[list[dict], list[tuple]]:
    """Split a batch into (valid, rejected) instead of refusing it whole.

    WHY THIS EXISTS (Step 2J)
    -------------------------
    validate_batch() is all-or-nothing: one bad bar refuses the symbol. That is
    the right default for a first run — it refuses to guess — but at full scale
    it cost 46 symbols their ENTIRE ten-year history over a single bar each:

      * 45 symbols: a synthetic 2025-04-26 placeholder (o=h=l=c, volume 0) on a
        date independently confirmed NOT to be a trading session.
      * IDEA.NS: one bar whose volume arrived as -81,259,413, an upstream int32
        overflow of ~4.21bn shares.

    Both bars MUST stay rejected. What was disproportionate was discarding the
    other ~2,476 valid bars alongside them.

    This is safe to add because the production choke point already behaves this
    way: crawler.price_feed.save_candles_to_db -> filter_canonical_candles()
    validates PER ROW, keeping the good and logging the dropped. So this does
    not weaken any contract — it stops the one-off script being stricter than
    production, and every row it keeps is still re-validated at the choke point.

    Opt-in via --skip-invalid-candles; the default stays all-or-nothing.
    """
    good: list[dict] = []
    bad: list[tuple] = []
    for c in candles:
        ok, why = validate_batch(symbol, [c], stats, res)
        if ok:
            good.append(c)
        else:
            bad.append((c.get("timestamp"), why))
    if bad:
        # print, not logging: this is a CLI one-off and the operator needs to see
        # every dropped bar inline with the run.
        print(f"    [drop] {symbol}: refused {len(bad)} candle(s), kept {len(good)}"
              f" -> {[(str(t)[:10], w) for t, w in bad[:3]]}")
    return good, bad


# ── checkpointing ────────────────────────────────────────────────────────────

def load_completed(path: Path) -> set[str]:
    """Symbols already finished successfully, for --resume."""
    if not path.exists():
        return set()
    done = set()
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            if row.get("status") == SUCCESS:
                done.add(row["symbol"])
    return done


def append_checkpoint(path: Path, r: Result) -> None:
    """Append-only: one durable line per symbol attempt, flushed immediately."""
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        w.writerow({
            "timestamp": dt.datetime.utcnow().isoformat(timespec="seconds"),
            "symbol": r.symbol, "instrument_key": r.instrument_key,
            "instrument_class": r.instrument_class,
            "status": r.status, "rows_received": r.rows_received,
            "rows_valid": r.rows_valid, "rows_saved": r.rows_saved,
            "rows_existing": r.rows_existing,
            "first_session": r.first_session, "last_session": r.last_session,
            "conventions": r.conventions, "duplicates": r.duplicates,
            "weekend_violations": r.weekend_violations,
            "ohlc_violations": r.ohlc_violations,
            "refetch_compared": r.refetch_compared,
            "refetch_identical": r.refetch_identical,
            "dropped_invalid": r.dropped_invalid, "error_type": r.error_type,
            "error_message": (r.error_message or "")[:300],
            "elapsed_seconds": f"{r.elapsed_seconds:.2f}", "attempt": r.attempt,
        })
        f.flush()
        os.fsync(f.fileno())


# ── one symbol ───────────────────────────────────────────────────────────────

async def backfill_symbol(symbol: str, key: str, frm: str, to: str, *,
                          dry_run: bool, stats: Stats,
                          skip_invalid: bool = False) -> Result:
    """Fetch -> validate -> persist one symbol. Never raises to the caller."""
    from crawler.upstox_candles import get_upstox_candles_for_range

    from utils.candle_contract import classify_instrument

    res = Result(symbol=symbol, instrument_key=key,
                 instrument_class=classify_instrument(symbol).value)
    t0 = time.perf_counter()

    for attempt in range(1, MAX_ATTEMPTS + 1):
        res.attempt = attempt
        try:
            stats.requests += 1
            candles = await get_upstox_candles_for_range(symbol, frm, to, interval="1d")
            break
        except Exception as exc:                       # transient — bounded retry
            if attempt >= MAX_ATTEMPTS:
                res.status = ERROR
                res.error_type = type(exc).__name__
                res.error_message = str(exc)
                res.elapsed_seconds = time.perf_counter() - t0
                return res
            stats.retries += 1
            await asyncio.sleep(BACKOFF_BASE ** attempt)
    else:                                              # pragma: no cover
        candles = []

    res.rows_received = len(candles)
    stats.rows_received += len(candles)

    if not candles:
        res.status = NO_DATA
        res.elapsed_seconds = time.perf_counter() - t0
        return res

    res.conventions = ",".join(sorted({c["timestamp"].strftime("%H:%M") for c in candles}))
    await compare_against_existing(symbol, candles, res)

    if skip_invalid:
        candles, dropped = partition_batch(symbol, candles, stats, res)
        res.dropped_invalid = len(dropped)
        if dropped:
            res.error_message = f"dropped {len(dropped)}: {dropped[0][1]}"[:280]
        stats.rows_rejected += len(dropped)
        ok, why = (bool(candles), "" if candles else "every candle invalid")
    else:
        ok, why = validate_batch(symbol, candles, stats, res)
    if not ok:
        res.status = ERROR
        res.error_type = "ValidationError"
        res.error_message = why
        stats.rows_rejected += len(candles)
        res.elapsed_seconds = time.perf_counter() - t0
        return res

    res.rows_valid = len(candles)
    ts = [c["timestamp"] for c in candles]
    res.first_session = min(ts).date().isoformat()
    res.last_session = max(ts).date().isoformat()

    if dry_run:
        res.status = SUCCESS
        res.rows_saved = 0
        res.elapsed_seconds = time.perf_counter() - t0
        return res

    try:
        from crawler.price_feed import save_candles_to_db
        from db.database import AsyncSessionLocal

        async with AsyncSessionLocal() as s:
            # Additive only. enforce_contract stays on — this is the same guard
            # production writes through. refresh_current_session stays OFF: a
            # backfill must never rewrite a bar another writer already owns.
            saved = await save_candles_to_db(
                candles, s, source="oneoff-upstox-backfill",
                extra_open=NSE_SPECIAL_SESSIONS)
        res.rows_saved = saved
        res.rows_existing = max(0, res.rows_valid - saved)
        stats.rows_saved += saved
        res.status = SUCCESS
    except Exception as exc:
        res.status = ERROR
        res.error_type = type(exc).__name__
        res.error_message = f"{exc}\n{traceback.format_exc(limit=2)}"

    res.elapsed_seconds = time.perf_counter() - t0
    return res


# ── driver ───────────────────────────────────────────────────────────────────

async def run(args) -> int:
    from db.database import AsyncSessionLocal
    from crawler.upstox_quotes import _to_key, ensure_key_map

    ckpt = Path(args.checkpoint)
    to_date = args.to_date or dt.date.today().isoformat()
    frm_date = args.from_date or (
        dt.date.fromisoformat(to_date) - dt.timedelta(days=365 * args.years + 3)).isoformat()

    await ensure_key_map()

    async with AsyncSessionLocal() as s:
        # The pilot deliberately spans both classes, so its lookup must contain
        # ETFs regardless of --include-etf (which governs FULL mode).
        universe = await build_universe(s, include_etf=args.include_etf or args.pilot)

    if args.symbols:
        want = {x.strip().upper() for x in args.symbols.split(",") if x.strip()}
        want = {w if w.endswith(".NS") else f"{w}.NS" for w in want}
        universe = [(sym, k) for sym, k in universe if sym in want]
        missing = want - {sym for sym, _ in universe}
        for m in sorted(missing):
            universe.append((m, ""))
    elif args.pilot:
        by = dict(universe)
        universe = [(sym, by.get(sym, "")) for sym in PILOT_SYMBOLS]
    elif not args.full:
        print("  refusing to run: pass --pilot, --full, or --symbols")
        return 2

    if args.limit:
        universe = universe[: args.limit]

    done = load_completed(ckpt) if args.resume else set()
    todo = [(sym, k) for sym, k in universe if sym not in done]

    mode = "PILOT" if args.pilot else ("FULL" if args.full else "SYMBOLS")
    print(f"\n  mode={mode}  range={frm_date} .. {to_date}  concurrency={args.concurrency}"
          f"  dry_run={args.dry_run}")
    print(f"  universe={len(universe)}  already_done={len(done)}  to_process={len(todo)}")
    print(f"  checkpoint={ckpt}\n")

    stats = Stats()
    results: list[Result] = []
    sem = asyncio.Semaphore(args.concurrency)
    t0 = time.perf_counter()

    async def one(sym: str, key: str) -> Result:
        async with sem:
            if not key and not _to_key(sym):
                r = Result(symbol=sym, status=NO_KEY,
                           error_message="no Upstox instrument key; not manufactured")
                append_checkpoint(ckpt, r)
                return r
            r = await backfill_symbol(sym, key or _to_key(sym) or "", frm_date, to_date,
                                      dry_run=args.dry_run, stats=stats,
                                      skip_invalid=args.skip_invalid_candles)
            append_checkpoint(ckpt, r)
            return r

    tasks = [asyncio.create_task(one(sym, key)) for sym, key in todo]
    for i, fut in enumerate(asyncio.as_completed(tasks), 1):
        r = await fut
        results.append(r)
        if i % max(1, len(tasks) // 20) == 0 or i == len(tasks):
            print(f"    {i}/{len(tasks)}  last={r.symbol} {r.status} rows={r.rows_saved}")

    elapsed = time.perf_counter() - t0
    print_report(results, stats, elapsed, args)
    return 0


def print_report(results: list[Result], stats: Stats, elapsed: float, args) -> None:
    by = {}
    for r in results:
        by.setdefault(r.status, []).append(r)
    n = len(results) or 1

    # ── per symbol, the full detail ──────────────────────────────────────────
    print("\n" + "=" * 112)
    print("  PER-SYMBOL DETAIL")
    print(f"  {'symbol':15}{'class':12}{'status':9}{'recv':>6}{'valid':>6}{'ins':>6}"
          f"{'exist':>7}{'conv':>7}{'dup':>5}{'wknd':>5}{'ohlc':>5}  {'first':10} {'last':10} {'refetch':>9}")
    for r in sorted(results, key=lambda x: (x.instrument_class, x.symbol)):
        refetch = (f"{r.refetch_identical}/{r.refetch_compared}" if r.refetch_compared else "-")
        print(f"  {r.symbol:15}{r.instrument_class:12}{r.status:9}"
              f"{r.rows_received:>6}{r.rows_valid:>6}{r.rows_saved:>6}{r.rows_existing:>7}"
              f"{r.conventions or '-':>7}{r.duplicates:>5}{r.weekend_violations:>5}"
              f"{r.ohlc_violations:>5}  {r.first_session or '-':10} {r.last_session or '-':10}"
              f"{refetch:>9}")

    # ── universe, split by instrument class ─────────────────────────────────
    print("\n  UNIVERSE (classes reported separately)")
    classes = sorted({r.instrument_class for r in results})
    for k in classes:
        sub = [r for r in results if r.instrument_class == k]
        cnt = {st: len([r for r in sub if r.status == st]) for st in (SUCCESS, NO_DATA, NO_KEY, ERROR)}
        print(f"    {k:14} attempted={len(sub):5}  SUCCESS={cnt[SUCCESS]:5}"
              f"  NO_DATA={cnt[NO_DATA]:4}  NO_KEY={cnt[NO_KEY]:4}  ERROR={cnt[ERROR]:4}")
    print(f"    {'TOTAL':14} attempted={len(results):5}  SUCCESS={len(by.get(SUCCESS,[])):5}"
          f"  NO_DATA={len(by.get(NO_DATA,[])):4}  NO_KEY={len(by.get(NO_KEY,[])):4}"
          f"  ERROR={len(by.get(ERROR,[])):4}")

    print("\n  ROWS")
    print(f"    received         {stats.rows_received:,}")
    print(f"    passed contract  {sum(r.rows_valid for r in results):,}")
    print(f"    inserted (new)   {stats.rows_saved:,}")
    print(f"    already existing {sum(r.rows_existing for r in results):,}")
    print(f"    rejected         {stats.rows_rejected:,}")

    print("\n  QUALITY (violations that blocked a save)")
    for k, v in stats.violations.items():
        print(f"    {k:20} {v}")

    print("\n  PRICE-BASIS REPRODUCIBILITY (existing 03:45 rows vs a fresh Upstox fetch)")
    cmpd = sum(r.refetch_compared for r in results)
    idt = sum(r.refetch_identical for r in results)
    if cmpd:
        print(f"    compared {cmpd:,} · identical {idt:,} ({idt/cmpd*100:.2f}%)"
              f" · differing {cmpd - idt:,}")
        print("    (differences expected only on the newest sessions — settlement volume)")
    else:
        print("    no pre-existing 03:45 rows to compare against")

    print("\n  PERFORMANCE")
    print(f"    elapsed          {elapsed:.1f}s")
    print(f"    requests         {stats.requests}")
    print(f"    retries          {stats.retries}")
    print(f"    effective req/s  {stats.requests / elapsed if elapsed else 0:.2f}")

    if by.get(ERROR):
        print("\n  ERRORS")
        for r in by[ERROR][:12]:
            print(f"    {r.symbol:16} {r.error_type}: {r.error_message[:88]}")
    if by.get(NO_KEY):
        print(f"\n  NO_KEY ({len(by[NO_KEY])}): {[r.symbol for r in by[NO_KEY]][:12]}")
    if by.get(NO_DATA):
        print(f"\n  NO_DATA ({len(by[NO_DATA])}): {[r.symbol for r in by[NO_DATA]][:12]}")

    ok = by.get(SUCCESS, [])
    if ok:
        depths = sorted(r.rows_received for r in ok)
        print("\n  COVERAGE")
        print(f"    successful symbols   {len(ok)} ({len(ok)/n*100:.1f}%)")
        print(f"    history depth rows   min={depths[0]} p50={depths[len(depths)//2]} max={depths[-1]}")
        short = [r for r in ok if r.rows_received < 250]
        print(f"    unexpectedly short (<250 sessions): {len(short)}"
              + (f" e.g. {[r.symbol for r in short][:6]}" if short else ""))
    print("=" * 112 + "\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="One-off Upstox NSE daily backfill (shadow, additive)")
    m = p.add_argument_group("mode")
    m.add_argument("--pilot", action="store_true", help=f"run the {len(PILOT_SYMBOLS)} pilot symbols")
    m.add_argument("--full", action="store_true", help="run the whole derived universe")
    m.add_argument("--symbols", help="comma-separated symbol filter")
    m.add_argument("--limit", type=int, help="cap the number of symbols")
    p.add_argument("--from-date", dest="from_date")
    p.add_argument("--to-date", dest="to_date")
    p.add_argument("--years", type=int, default=DEFAULT_YEARS)
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    p.add_argument("--dry-run", action="store_true", help="fetch and validate, persist nothing")
    p.add_argument("--resume", action="store_true", help="skip symbols already SUCCESS in the checkpoint")
    p.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    p.add_argument("--skip-invalid-candles", action="store_true",
                   help="drop individual invalid candles instead of refusing the whole "
                        "symbol (production's choke point already behaves this way)")
    p.add_argument("--include-etf", action="store_true",
                   help="also backfill ETF_OR_INAV (default: NSE equity only)")
    return p


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
