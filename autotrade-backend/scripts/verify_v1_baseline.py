"""Independent integrity gate over a materialized V1 baseline (Phase 2N §3).

Reads the WRITTEN files and re-derives every claim from the database. It shares
only the frozen constants with the builder — not its feature code — so a bug in
the builder cannot hide behind the same bug in the checker. Gate 13/14 go
further and recompute features from scratch, through the reader, at `as_of = D`.

    .venv/bin/python scripts/verify_v1_baseline.py --dir datasets/v1_baseline
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import csv
import datetime as dt
import glob
import gzip
import hashlib
import json
import math
import os
import random
import sys

import numpy as np
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import AsyncSessionLocal                       # noqa: E402
from engine.daily_series import session_bars                    # noqa: E402
from utils.candle_contract import (                             # noqa: E402
    NSE_SPECIAL_SESSIONS, classify_instrument, InstrumentClass,
)
from scripts import v1_contract as C                            # noqa: E402

results: list[tuple[str, str, str]] = []


def gate(n: int, name: str, ok: bool, detail: str = "") -> None:
    results.append((f"{n:2d}", name, "PASS" if ok else "FAIL"))
    print(f"   [{'PASS' if ok else 'FAIL'}] {n:2d}. {name}"
          + (f"  — {detail}" if detail else ""))


def _f(x):
    return float(x) if x not in ("", None) else None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--sample", type=int, default=300)
    args = ap.parse_args()

    man = json.load(open(os.path.join(args.dir, "manifest.json")))
    files = sorted(glob.glob(os.path.join(args.dir, "v1_baseline_*.csv.gz")))
    print(f"manifest: {man['dataset_version']} / {man['contract_version']}")

    rows: list[dict] = []
    for p in files:
        with gzip.open(p, "rt", newline="") as f:
            rows.extend(csv.DictReader(f))
    print(f"loaded {len(rows):,} rows from {len(files)} shards\n")

    syms = {r["symbol"] for r in rows}
    preds = {r["symbol"]: [] for r in rows}
    for r in rows:
        preds[r["symbol"]].append(dt.date.fromisoformat(r["prediction_session"]))

    async with AsyncSessionLocal() as s:
        universe = {x[0] for x in (await s.execute(text("""
            SELECT DISTINCT symbol FROM candles
            WHERE timeframe='1d' AND to_char(timestamp,'HH24:MI')='03:45'"""))).all()}
        equities = {x for x in universe
                    if classify_instrument(x) is InstrumentClass.NSE_EQUITY}

        cov = (await s.execute(text("""
            SELECT timestamp::date d, count(DISTINCT symbol) n FROM candles
            WHERE timeframe='1d' AND to_char(timestamp,'HH24:MI')='03:45'
              AND close > 0 AND symbol = ANY(:s)
            GROUP BY 1 ORDER BY 1"""), {"s": sorted(equities)})).all()
        peak: dict[int, int] = {}
        for d, n in cov:
            peak[d.year] = max(peak.get(d.year, 0), n)
        calendar = [d for d, n in cov if n >= C.CALENDAR_MIN_COVERAGE * peak[d.year]]
        cal_idx = {d: i for i, d in enumerate(calendar)}

        # canonical bars per symbol, straight from the table (independent path)
        canon: dict[str, dict[dt.date, tuple]] = collections.defaultdict(dict)
        order: dict[str, list[dt.date]] = {}
        for sym, d, o, h, lo, c, v in (await s.execute(text("""
            SELECT symbol, timestamp::date, open, high, low, close, volume
            FROM candles WHERE timeframe='1d' AND to_char(timestamp,'HH24:MI')='03:45'
              AND symbol = ANY(:s) ORDER BY symbol, timestamp"""),
            {"s": sorted(syms)})).all():
            canon[sym][d] = (float(o), float(h), float(lo), float(c), float(v or 0))
        for sym in canon:
            order[sym] = sorted(canon[sym])

        # legacy bars, to prove none was consumed
        legacy = collections.defaultdict(set)
        for sym, d in (await s.execute(text("""
            SELECT symbol, timestamp::date FROM candles
            WHERE timeframe='1d' AND to_char(timestamp,'HH24:MI') IN ('18:30','00:00')
              AND symbol = ANY(:s)"""), {"s": sorted(syms)})).all():
            legacy[sym].add(d)

        print("INTEGRITY GATES")
        bad = [r["symbol"] for r in rows if r["symbol"] not in equities]
        gate(1, "symbol in the 2,468 NSE equity universe", not bad,
             f"{len(equities)} equities; offenders={len(set(bad))}")

        m2 = [r for r in rows
              if dt.date.fromisoformat(r["prediction_session"]) not in canon[r["symbol"]]]
        gate(2, "prediction_session has a canonical 03:45 bar", not m2, f"{len(m2)} missing")

        m3 = [r for r in rows
              if dt.date.fromisoformat(r["target_session"]) not in canon[r["symbol"]]]
        gate(3, "target_session has a canonical 03:45 bar", not m3, f"{len(m3)} missing")

        m4 = [r for r in rows if r["target_session"] <= r["prediction_session"]]
        gate(4, "target_session > prediction_session", not m4, f"{len(m4)} violations")

        m5 = [r for r in rows
              if cal_idx.get(dt.date.fromisoformat(r["target_session"]), -99)
                 - cal_idx.get(dt.date.fromisoformat(r["prediction_session"]), 99) != 1]
        gate(5, "session-index distance == 1", not m5, f"{len(m5)} violations")

        # gate 6: no excluded gap class present — a kept row must miss 0 sessions
        m6 = 0
        for r in rows:
            D = dt.date.fromisoformat(r["prediction_session"])
            T = dt.date.fromisoformat(r["target_session"])
            nxt = [d for d in order[r["symbol"]] if d > D]
            if not nxt or nxt[0] != T:
                m6 += 1
        gate(6, "no suspension / listing-boundary / inactivity rows", m6 == 0,
             f"{m6} rows whose target is not the symbol's own next canonical bar")

        m7 = 0
        for r in rows:
            D = dt.date.fromisoformat(r["prediction_session"])
            if order[r["symbol"]].index(D) + 1 < C.WARMUP_SESSIONS:
                m7 += 1
        gate(7, f"warm-up >= {C.WARMUP_SESSIONS} sessions", m7 == 0, f"{m7} short rows")

        req = [c for c in C.FEATURES if c not in C.NULLABLE_ON_LOCKED_BAR]
        miss = sum(1 for r in rows for c in req if r[c] == "")
        lockbad = sum(1 for r in rows for c in C.NULLABLE_ON_LOCKED_BAR
                      if r[c] == "" and r["locked_bar_flag"] != "1")
        gate(8, "every required feature has a value", miss == 0 and lockbad == 0,
             f"{miss} unexpected nulls; {lockbad} nulls outside a locked bar")

        badt = sum(1 for r in rows for t in C.TARGETS
                   if r[t] == "" or not math.isfinite(float(r[t])))
        gate(9, "no NaN / infinite target", badt == 0, f"{badt} bad targets")

        m10 = sum(1 for r in rows if float(r["close_D"]) <= 0)
        gate(10, "prediction close > 0", m10 == 0, f"{m10} violations")

        m11 = sum(1 for r in rows
                  if canon[r["symbol"]][dt.date.fromisoformat(r["target_session"])][3] <= 0)
        gate(11, "target close > 0", m11 == 0, f"{m11} violations")

        keys = collections.Counter((r["symbol"], r["prediction_session"]) for r in rows)
        dup = [k for k, v in keys.items() if v > 1]
        gate(12, "no duplicate (symbol, prediction_session)", not dup, f"{len(dup)} dups")

        # ── 13 / 14: recompute from scratch through the reader at as_of = D ──
        random.seed(20260908)
        smp = random.sample(rows, min(args.sample, len(rows)))
        f13 = f14 = 0
        pbars_all = await session_bars(C.MARKET_PROXY_SYMBOL, calendar[0], calendar[-1],
                                       s, as_of=calendar[-1],
                                       extra_open=NSE_SPECIAL_SESSIONS)
        for r in smp:
            sym = r["symbol"]
            D = dt.date.fromisoformat(r["prediction_session"])
            b = await session_bars(sym, D - dt.timedelta(days=800), D + dt.timedelta(days=60),
                                   s, as_of=D, extra_open=NSE_SPECIAL_SESSIONS)
            if not b or b[-1][0] != D:
                f13 += 1
                continue
            c = [x[4] for x in b]
            h = [x[2] for x in b]
            exp_r1 = c[-1] / c[-2] - 1.0
            exp_h20 = max(h[-20:])
            got_r1, got_h20 = _f(r["return_1d"]), _f(r["high_20d"])
            if (abs(got_r1 - exp_r1) > 1e-6 * max(1.0, abs(exp_r1))
                    or abs(got_h20 - exp_h20) > 1e-6 * max(1.0, exp_h20)):
                f13 += 1
            pb = [x for x in pbars_all if x[0] <= D]
            pc = [x[4] for x in pb]
            if len(pc) > 21:
                exp_m21 = pc[-1] / pc[-22] - 1.0
                got_m21 = _f(r["mkt_return_21d"])
                if got_m21 is None or abs(got_m21 - exp_m21) > 1e-6 * max(1.0, abs(exp_m21)):
                    f14 += 1
        gate(13, f"no future feature value (recomputed, n={len(smp)})", f13 == 0,
             f"{f13} mismatches")
        gate(14, f"market proxy obeys the same cutoff (recomputed, n={len(smp)})",
             f14 == 0, f"{f14} mismatches")

        f15 = 0
        for r in rows:
            T = dt.date.fromisoformat(r["target_session"])
            ret = canon[r["symbol"]][T][3] / float(r["close_D"]) - 1.0
            if (abs(ret) > C.EXTREME_RETURN_THRESHOLD) != (r["extreme_return_flag"] == "1"):
                f15 += 1
        gate(15, "extreme_return_flag matches the recomputed return", f15 == 0,
             f"{f15} mismatches")

        flagged = sum(1 for r in rows if r["extreme_return_flag"] == "1")
        train = len(rows) - flagged
        gate(16, "flagged anomalies excluded from the default training population",
             train == man["training_row_count"] and flagged == man["quarantined_row_count"],
             f"{flagged} flagged, {train:,} trainable, manifest agrees")

        f17 = sum(1 for r in rows
                  if dt.date.fromisoformat(r["prediction_session"]) not in canon[r["symbol"]]
                  or dt.date.fromisoformat(r["target_session"]) not in canon[r["symbol"]])
        legacy_only = sum(1 for r in rows
                          for d in (dt.date.fromisoformat(r["prediction_session"]),
                                    dt.date.fromisoformat(r["target_session"]))
                          if d not in canon[r["symbol"]] and d in legacy[r["symbol"]])
        gate(17, "no 18:30 / 00:00 legacy bar consumed", f17 == 0 and legacy_only == 0,
             f"{legacy_only} legacy-only sessions referenced")

        today = dt.date.today()
        maxd = max(dt.date.fromisoformat(r["target_session"]) for r in rows)
        cur_open = maxd >= today and dt.datetime.now().hour < 16
        gate(18, "no current / future session consumed",
             maxd <= calendar[-1] and not cur_open,
             f"max target={maxd}, last calendar session={calendar[-1]}, today={today}")

        # ── manifest digest ─────────────────────────────────────────────────
        ok_files = True
        for entry in man["files"]:
            p = os.path.join(args.dir, entry["file"])
            hh = hashlib.sha256()
            with gzip.open(p, "rb") as f:            # digest is over CONTENT
                for ch in iter(lambda: f.read(1 << 20), b""):
                    hh.update(ch)
            if hh.hexdigest() != entry["sha256_content"]:
                ok_files = False
        digest = hashlib.sha256(
            "".join(e["sha256_content"] for e in man["files"]).encode()).hexdigest()
        gate(19, "manifest digests match the files on disk",
             ok_files and digest == man["dataset_digest"], f"digest={digest[:16]}...")

    failed = [r for r in results if r[2] == "FAIL"]
    print(f"\n{len(results) - len(failed)}/{len(results)} gates PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
