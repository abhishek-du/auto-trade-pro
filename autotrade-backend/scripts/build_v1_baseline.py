"""Materialize the frozen V1 BASELINE dataset (Phase 2N).

Reads canonical daily bars THROUGH `engine.daily_series` — never with a direct
`candles` query — and writes gzipped CSV shards plus a manifest. It creates no
table and mutates nothing: the only writes are new files under --out.

Point-in-time is structural, not asserted after the fact. Every feature is a
backward-looking rolling window over the symbol's own session-ordered series,
and the row for session D is emitted from index i while the target comes from
index i+1, so no feature can see its own target. The verifier re-derives the
same claims from the written files.

Usage
-----
    .venv/bin/python scripts/build_v1_baseline.py --out datasets/v1_baseline
    .venv/bin/python scripts/build_v1_baseline.py --out /tmp/x --limit-symbols 50
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import math
import os
import sys

import numpy as np
import pandas as pd
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import AsyncSessionLocal                      # noqa: E402
from engine.daily_series import session_bars, session_bars_bulk  # noqa: E402
from utils.candle_contract import (                            # noqa: E402
    NSE_SPECIAL_SESSIONS, classify_instrument, InstrumentClass,
)
from scripts import v1_contract as C                           # noqa: E402

BATCH = 120


# ── helpers ──────────────────────────────────────────────────────────────────

def _fmt(v) -> str:
    """Deterministic text for a value. %.10g keeps the digest stable."""
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        return f"{v:.10g}"
    if isinstance(v, (np.floating,)):
        f = float(v)
        return "" if math.isnan(f) else f"{f:.10g}"
    if isinstance(v, (bool, np.bool_)):
        return "1" if v else "0"
    if isinstance(v, (np.integer,)):
        return str(int(v))
    return str(v)


def _ema_sma_seeded(x: np.ndarray, span: int) -> np.ndarray:
    """EMA seeded with the SMA of the first `span` values, as documented.

    pandas' ewm cannot seed this way, and the seeding convention is part of the
    frozen catalog rather than an implementation detail — an unseeded recursion
    from bar 0 gives a different ema_200 for the first several hundred bars.
    """
    n = len(x)
    out = np.full(n, np.nan)
    if n < span:
        return out
    k = 2.0 / (span + 1.0)
    e = float(np.mean(x[:span]))
    out[span - 1] = e
    for i in range(span, n):
        e = x[i] * k + e * (1.0 - k)
        out[i] = e
    return out


def _consecutive_up(close: np.ndarray, cap: int = 10) -> np.ndarray:
    up = np.zeros(len(close))
    run = 0
    for i in range(1, len(close)):
        run = run + 1 if close[i] > close[i - 1] else 0
        up[i] = min(run, cap)
    return up


def admit_row(i: int, sessions: list, cal_idx: dict, *,
              warmup: int = C.WARMUP_SESSIONS) -> tuple[bool, str]:
    """Should the transition sessions[i] -> sessions[i+1] become a row?

    Pure, so the frozen admission rules can be tested without a database.
    Returns (admitted, reason). `reason` is "" when admitted.
    """
    if i + 1 >= len(sessions):
        return False, "NO_TARGET"
    D, T = sessions[i], sessions[i + 1]
    if i + 1 < warmup:
        return False, "WARMUP"
    a, b = cal_idx.get(D), cal_idx.get(T)
    if a is None or b is None:
        return False, "SESSION_OFF_CALENDAR"
    if b - a != 1:
        return False, "NOT_NEXT_SESSION"
    return True, ""


def _proxy_features(bars) -> dict[dt.date, dict]:
    """Market features from NIFTYBEES_MARKET_PROXY, keyed by session date.

    Same backward-only construction as the equity features, so joining on D
    cannot import a proxy value from after D.
    """
    d = pd.DataFrame(bars, columns=["session", "open", "high", "low", "close", "volume"])
    c = d["close"].astype(float)
    r1 = c.pct_change(1)
    out = {}
    sma50 = c.rolling(50).mean()
    sma200 = c.rolling(200).mean()
    r5 = c.pct_change(5)
    r21 = c.pct_change(21)
    vol21 = r1.rolling(21).std()
    for i, sess in enumerate(d["session"]):
        out[sess] = {
            "mkt_return_1d": r1.iloc[i], "mkt_return_5d": r5.iloc[i],
            "mkt_return_21d": r21.iloc[i], "mkt_vol_21d": vol21.iloc[i],
            "mkt_above_sma50": (1.0 if c.iloc[i] > sma50.iloc[i] else 0.0)
                               if not math.isnan(sma50.iloc[i]) else np.nan,
            "mkt_above_sma200": (1.0 if c.iloc[i] > sma200.iloc[i] else 0.0)
                                if not math.isnan(sma200.iloc[i]) else np.nan,
            "_r1": r1.iloc[i],
        }
    return out


def _symbol_frame(bars, proxy_r1: dict) -> pd.DataFrame:
    """All backward-looking features for one symbol, one row per session."""
    d = pd.DataFrame(bars, columns=["session", "open", "high", "low", "close",
                                    "volume", "convention"])
    o, h, l, c = (d[k].astype(float) for k in ("open", "high", "low", "close"))
    v = d["volume"].astype(float)
    cn = c.to_numpy()

    r1 = c.pct_change(1)
    d["return_1d"] = r1
    d["return_5d"] = c.pct_change(5)
    d["return_21d"] = c.pct_change(21)
    d["return_63d"] = c.pct_change(63)

    for n in (20, 50, 200):
        d[f"sma_{n}"] = c.rolling(n).mean()
    for n in (20, 50, 200):
        d[f"ema_{n}"] = _ema_sma_seeded(cn, n)
    for n in (20, 50, 200):
        d[f"close_vs_sma{n}"] = c / d[f"sma_{n}"] - 1.0
    d["ema_stack_state"] = np.where(
        (d["ema_20"] > d["ema_50"]) & (d["ema_50"] > d["ema_200"]), 1.0,
        np.where((d["ema_20"] < d["ema_50"]) & (d["ema_50"] < d["ema_200"]), -1.0, 0.0))
    d.loc[d["ema_200"].isna(), "ema_stack_state"] = np.nan

    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    d["atr_14"] = tr.rolling(14).mean()
    d["realised_vol_21d"] = r1.rolling(21).std()
    d["realised_vol_63d"] = r1.rolling(63).std()

    hl = h - l
    locked = hl <= 0
    d["locked_bar_flag"] = locked.astype(int)
    d["range_pct"] = np.where(locked, np.nan, hl / c)
    d["body_pct"] = np.where(locked, np.nan, (c - o) / hl)
    d["upper_wick_pct"] = np.where(locked, np.nan, (h - np.maximum(o, c)) / hl)
    d["lower_wick_pct"] = np.where(locked, np.nan, (np.minimum(o, c) - l) / hl)
    d["gap_pct"] = o / prev_c - 1.0

    d["high_20d"] = h.rolling(20).max()
    d["low_20d"] = l.rolling(20).min()
    d["high_52w"] = h.rolling(252).max()
    d["low_52w"] = l.rolling(252).min()
    span20 = d["high_20d"] - d["low_20d"]
    span52 = d["high_52w"] - d["low_52w"]
    d["range_position_20d"] = np.where(span20 > 0, (c - d["low_20d"]) / span20, np.nan)
    d["range_position_52w"] = np.where(span52 > 0, (c - d["low_52w"]) / span52, np.nan)
    d["dist_from_52w_high"] = c / d["high_52w"] - 1.0
    prior_hi20 = h.rolling(20).max().shift(1)
    prior_lo20 = l.rolling(20).min().shift(1)
    d["breakout_20d"] = (c > prior_hi20).astype(float)
    d["breakdown_20d"] = (c < prior_lo20).astype(float)
    d.loc[prior_hi20.isna(), ["breakout_20d", "breakdown_20d"]] = np.nan
    d["consecutive_up_days"] = _consecutive_up(cn)

    d["volume"] = v
    d["avg_volume_20d"] = v.rolling(20).mean()
    d["volume_ratio_20d"] = v / d["avg_volume_20d"]
    d["volume_accel"] = v.rolling(5).mean() / d["avg_volume_20d"]
    d["turnover"] = c * v
    d["avg_turnover_20d"] = d["turnover"].rolling(20).mean()
    d["zero_volume_flag"] = (v == 0).astype(float)

    # beta vs NIFTYBEES_MARKET_PROXY over the trailing 64 sessions. Computed
    # here, on the symbol's FULL session series, not afterwards over the kept
    # rows: warm-up already guarantees 251 prior sessions, so beta is defined
    # for every emitted row. An earlier version ran this over the emitted rows
    # and nulled the first 63 of each symbol — 2,065 rows in a 40-symbol trial.
    mr = pd.Series([proxy_r1.get(x, float("nan")) for x in d["session"]],
                   index=d.index, dtype=float)
    cov = r1.rolling(64).cov(mr)
    var = mr.rolling(64).var()
    d["beta_63d"] = np.where(var > 0, cov / var, np.nan)
    return d


# ── build ────────────────────────────────────────────────────────────────────

async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit-symbols", type=int, default=0)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    started = dt.datetime.now(dt.timezone.utc)
    excl = collections.Counter()
    anomalies: list[list] = []

    async with AsyncSessionLocal() as s:
        # ── universe (canonical 03:45 presence, equities only) ───────────────
        univ = [r[0] for r in (await s.execute(text("""
            SELECT DISTINCT symbol FROM candles
            WHERE timeframe='1d' AND to_char(timestamp,'HH24:MI')='03:45'
            ORDER BY 1"""))).all()]
        equities = [x for x in univ
                    if classify_instrument(x) is InstrumentClass.NSE_EQUITY]
        print(f"[universe] canonical symbols={len(univ)}  equities={len(equities)}")
        if len(equities) != C.EXPECTED_EQUITY_SYMBOLS:
            print(f"[universe] CONTRACT MISMATCH: expected {C.EXPECTED_EQUITY_SYMBOLS}")
            return 2

        # ── NSE session calendar, derived from equity coverage ───────────────
        cov = (await s.execute(text("""
            SELECT timestamp::date d, count(DISTINCT symbol) n FROM candles
            WHERE timeframe='1d' AND to_char(timestamp,'HH24:MI')='03:45'
              AND close > 0 AND symbol = ANY(:s)
            GROUP BY 1 ORDER BY 1"""), {"s": equities})).all()
        peak: dict[int, int] = {}
        for d, n in cov:
            peak[d.year] = max(peak.get(d.year, 0), n)
        calendar = [d for d, n in cov if n >= C.CALENDAR_MIN_COVERAGE * peak[d.year]]
        cal_idx = {d: i for i, d in enumerate(calendar)}
        print(f"[calendar] sessions={len(calendar)}  {calendar[0]} .. {calendar[-1]}")
        if len(calendar) != C.EXPECTED_CALENDAR_SESSIONS:
            print(f"[calendar] CONTRACT MISMATCH: expected {C.EXPECTED_CALENDAR_SESSIONS}")
            return 2
        last_session = calendar[-1]

        # ── market proxy ─────────────────────────────────────────────────────
        pb = await session_bars(C.MARKET_PROXY_SYMBOL, calendar[0], last_session, s,
                                as_of=last_session, extra_open=NSE_SPECIAL_SESSIONS)
        proxy = _proxy_features(pb)
        proxy_r1 = {k: v["_r1"] for k, v in proxy.items()}
        print(f"[proxy] {C.MARKET_PROXY_NAME} ({C.MARKET_PROXY_SYMBOL}) "
              f"sessions={len(proxy)}")

        # ── shards ───────────────────────────────────────────────────────────
        syms = equities[: args.limit_symbols] if args.limit_symbols else equities
        shards: dict[int, list[list]] = collections.defaultdict(list)
        kept = 0

        for b0 in range(0, len(syms), BATCH):
            batch = syms[b0: b0 + BATCH]
            bulk = await session_bars_bulk(batch, s, sessions=0, as_of=last_session,
                                           extra_open=NSE_SPECIAL_SESSIONS)
            for sym in batch:
                bars = bulk.get(sym) or []
                canon = [b for b in bars if b[6] == C.CANONICAL_CONVENTION]
                excl["NON_CANONICAL_BAR"] += len(bars) - len(canon)
                if len(canon) < C.MIN_SESSIONS_PER_SYMBOL:
                    excl["WARMUP"] += max(0, len(canon) - 1)
                    continue

                d = _symbol_frame(canon, proxy_r1)
                sess = list(d["session"])
                cl = d["close"].astype(float).to_numpy()
                hi = d["high"].astype(float).to_numpy()
                lo = d["low"].astype(float).to_numpy()
                n = len(sess)

                for i in range(n - 1):
                    D, T = sess[i], sess[i + 1]
                    ok, why = admit_row(i, sess, cal_idx)
                    if not ok:
                        excl[why] += 1
                        continue
                    cD, cT, hT, lT = cl[i], cl[i + 1], hi[i + 1], lo[i + 1]
                    if not (cD > 0 and cT > 0 and hT > 0 and lT > 0
                            and all(map(math.isfinite, (cD, cT, hT, lT)))):
                        excl["INVALID_BAR"] += 1
                        continue
                    mk = proxy.get(D)
                    if mk is None:
                        excl["NO_MARKET_PROXY"] += 1
                        continue

                    ret = cT / cD - 1.0
                    extreme = abs(ret) > C.EXTREME_RETURN_THRESHOLD
                    row = {
                        "symbol": sym, "prediction_session": D, "target_session": T,
                        "calendar_gap_days": (T - D).days,
                        "close_D": cD,
                        "extreme_return_flag": 1 if extreme else 0,
                        "locked_bar_flag": int(d["locked_bar_flag"].iloc[i]),
                        "next_session_return": ret,
                        "next_session_high_return": hT / cD - 1.0,
                        "next_session_low_return": lT / cD - 1.0,
                    }
                    for f in C.PRICE_FEATURES + C.VOLUME_FEATURES:
                        row[f] = d[f].iloc[i]
                    for f in C.MARKET_FEATURES:
                        if f == "rel_strength_21d":
                            row[f] = row["return_21d"] - mk["mkt_return_21d"]
                        elif f == "beta_63d":
                            row[f] = d["beta_63d"].iloc[i]
                        else:
                            row[f] = mk[f]

                    # "A symbol emits no row until its complete feature vector
                    # is defined" (schema §1d) — enforced at emission, not
                    # patched afterwards. The four locked-bar ratios are the
                    # documented exception and are allowed to be null.
                    incomplete = any(
                        row[f] is None or (isinstance(row[f], float) and math.isnan(row[f]))
                        or (hasattr(row[f], "item") and math.isnan(float(row[f])))
                        for f in C.FEATURES if f not in C.NULLABLE_ON_LOCKED_BAR)
                    if incomplete:
                        excl["INCOMPLETE_FEATURES"] += 1
                        continue

                    shards[D.year].append([row[k] for k in C.COLUMNS])
                    kept += 1
                    if extreme:
                        excl["EXTREME_RETURN"] += 1
                        anomalies.append([sym, D, T, cD, cT, ret])
            print(f"[build] {min(b0+BATCH, len(syms)):>5}/{len(syms)} symbols  "
                  f"rows={kept:,}", flush=True)

    idx = {k: i for i, k in enumerate(C.COLUMNS)}
    by_sym = collections.defaultdict(list)
    for yr in shards:
        for r in shards[yr]:
            by_sym[r[idx['symbol']]].append(r)

    # ── write deterministic shards ───────────────────────────────────────────
    files, total = [], 0
    for yr in sorted(shards):
        rows = sorted(shards[yr], key=lambda r: (r[idx["symbol"]],
                                                 r[idx["prediction_session"]]))
        path = os.path.join(args.out, f"v1_baseline_{yr}.csv.gz")
        # The digest is taken over the UNCOMPRESSED text, and the container is
        # written with mtime=0. gzip stamps the current time into its header by
        # default, so hashing the .gz bytes gave a different digest on every
        # rebuild of byte-identical data — a manifest that cannot tell "changed"
        # from "rebuilt" proves nothing.
        h = hashlib.sha256()
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        with gzip.GzipFile(path, "wb", compresslevel=6, mtime=0) as gz:
            def _flush():
                data = buf.getvalue().encode()
                h.update(data)
                gz.write(data)
                buf.seek(0)
                buf.truncate(0)
            w.writerow(C.COLUMNS)
            _flush()
            for k, r in enumerate(rows):
                w.writerow([_fmt(x) for x in r])
                if k % 20000 == 0:
                    _flush()
            _flush()
        files.append({"file": os.path.basename(path), "rows": len(rows),
                      "sha256_content": h.hexdigest(),
                      "bytes": os.path.getsize(path)})
        total += len(rows)
        print(f"[write] {os.path.basename(path)}  rows={len(rows):,}")

    apath = os.path.join(args.out, "v1_baseline_anomalies.csv")
    with open(apath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "prediction_session", "target_session",
                    "close_D", "close_D1", "next_session_return"])
        for a in sorted(anomalies, key=lambda r: (r[0], r[1])):
            w.writerow([_fmt(x) for x in a])

    all_sessions = sorted({r[idx["prediction_session"]]
                           for yr in shards for r in shards[yr]})
    all_targets = sorted({r[idx["target_session"]]
                          for yr in shards for r in shards[yr]})
    manifest = {
        "dataset_version": C.DATASET_VERSION,
        "contract_version": C.CONTRACT_VERSION,
        "created_at": started.isoformat(),
        "row_count": total,
        "symbol_count": len(by_sym),
        "prediction_session_range": [str(all_sessions[0]), str(all_sessions[-1])],
        "target_session_range": [str(all_targets[0]), str(all_targets[-1])],
        "training_row_count": total - excl["EXTREME_RETURN"],
        "quarantined_row_count": excl["EXTREME_RETURN"],
        "provenance": {
            "source_table": "candles",
            "timeframe": "1d",
            "convention": C.CANONICAL_CONVENTION,
            "reader": "engine.daily_series.session_bars_bulk (as_of=last_session)",
            "as_of": str(last_session),
            "market_proxy": {"name": C.MARKET_PROXY_NAME,
                             "symbol": C.MARKET_PROXY_SYMBOL,
                             "sessions": len(proxy)},
            "session_calendar_sessions": len(calendar),
            "universe_symbols": len(equities),
        },
        "exclusion_rules": {
            "warmup_sessions": C.WARMUP_SESSIONS,
            "next_session_rule": "idx(target) - idx(prediction) == 1",
            "extreme_return_threshold": C.EXTREME_RETURN_THRESHOLD,
            "excluded_counts": dict(sorted(excl.items())),
        },
        "features": {"price": C.PRICE_FEATURES, "volume": C.VOLUME_FEATURES,
                     "market": C.MARKET_FEATURES},
        "targets": {
            "next_session_return": "close[D+1] / close[D] - 1",
            "next_session_high_return": "high[D+1] / close[D] - 1",
            "next_session_low_return": "low[D+1] / close[D] - 1",
            "note": "RAW OUTCOME, not executable outcome — see schema §3",
        },
        "columns": C.COLUMNS,
        "files": files,
    }
    digest = hashlib.sha256(
        "".join(f["sha256_content"] for f in files).encode()).hexdigest()
    manifest["dataset_digest"] = digest
    with open(os.path.join(args.out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    print(f"\n[done] rows={total:,}  symbols={len(by_sym)}  "
          f"sessions={all_sessions[0]} .. {all_sessions[-1]}")
    print(f"[done] digest={digest}")
    print(f"[done] exclusions={dict(sorted(excl.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
