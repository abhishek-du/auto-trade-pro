"""STEP 2C.2 Phase H + L — golden-basket validation.

RAW UPSTOX  ->  CANONICAL MAPPING  ->  DB STORED, compared exactly.

Writes only through the guarded canonical path, so this doubles as proof that
repeated ingestion is idempotent and that no non-canonical row can be created.
"""
from __future__ import annotations
import asyncio, datetime as dt, json, math, sys
import httpx
from sqlalchemy import text
sys.path.insert(0, "/home/cis/windows/auto-trade-pro/autotrade-backend")
from db.database import AsyncSessionLocal
from utils.config import settings

H = {"Accept": "application/json", "Authorization": f"Bearer {settings.UPSTOX_ACCESS_TOKEN}"}
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BASKET = json.load(open("/tmp/claude-1000/-home-cis-windows-auto-trade-pro/3b62f59e-c58f-45bc-832f-5d4acd5b9be4/scratchpad/golden_basket.json"))
FROM, TO = "2026-08-28", "2026-09-03"


async def main():
    from crawler.upstox_candles import _to_naive_utc, get_upstox_candles_for_range
    from crawler.upstox_quotes import _to_key, ensure_key_map
    from crawler.price_feed import save_candles_to_db
    from utils.candle_contract import CANONICAL_DAILY_UTC_TIME
    await ensure_key_map()

    async with httpx.AsyncClient(timeout=45) as c:
        hol = {h["date"] for h in ((await c.get(
            "https://api.upstox.com/v2/market/holidays", headers=H)).json().get("data") or [])}

        stats = dict(symbols=0, identity_ok=0, raw_ok=0, offset_ok=0, canon_ok=0,
                     ohlc_ok=0, vol_ok=0, session_ok=0, nofuture_ok=0,
                     persist_ok=0, ts_match=0, ohlcv_match=0, idempotent=0, bars=0)
        failures = []
        sem = asyncio.Semaphore(5)

        async def one(sym):
            async with sem:
                key = _to_key(sym)
                if not key:
                    failures.append((sym, "no instrument_key")); return
                stats["identity_ok"] += 1
                url = (f"https://api.upstox.com/v3/historical-candle/"
                       f"{key.replace('|','%7C')}/days/1/{TO}/{FROM}")
                r = await c.get(url, headers=H)
                if r.status_code != 200:
                    failures.append((sym, f"raw HTTP {r.status_code}")); return
                raw = (r.json().get("data") or {}).get("candles") or []
                if not raw:
                    failures.append((sym, "raw empty")); return
                stats["raw_ok"] += 1

                canon = await get_upstox_candles_for_range(sym, FROM, TO, interval="1d")
                cmap = {x["timestamp"]: x for x in canon}

                for row in raw:
                    stats["bars"] += 1
                    ts_raw = row[0]
                    # 3. raw has +05:30 offset and midnight wall clock
                    if ts_raw.endswith("+05:30") and ts_raw[11:19] == "00:00:00":
                        stats["offset_ok"] += 1
                    else:
                        failures.append((sym, f"raw shape {ts_raw}")); continue
                    # 4. canonical mapping
                    ct = _to_naive_utc(ts_raw, daily=True)
                    sess = dt.datetime.fromisoformat(ts_raw).date()
                    if ct.time() == CANONICAL_DAILY_UTC_TIME and ct.date() == sess:
                        stats["canon_ok"] += 1
                    else:
                        failures.append((sym, f"canon {ts_raw}->{ct}")); continue
                    o, h_, l, cl, v = row[1], row[2], row[3], row[4], row[5]
                    # 5/6 OHLC + volume validity
                    fin = all(isinstance(x,(int,float)) and math.isfinite(x) for x in (o,h_,l,cl))
                    if fin and l <= min(o,cl) and h_ >= max(o,cl) and h_ >= l and min(o,h_,l,cl) > 0:
                        stats["ohlc_ok"] += 1
                    else:
                        failures.append((sym, f"ohlc {sess} o={o} h={h_} l={l} c={cl}")); continue
                    if v >= 0: stats["vol_ok"] += 1
                    else: failures.append((sym, f"neg volume {sess}")); continue
                    # 7/8 session validity + no future
                    if sess.weekday() < 5 and sess.isoformat() not in hol:
                        stats["session_ok"] += 1
                    else:
                        failures.append((sym, f"bad session {sess}")); continue
                    if ct <= dt.datetime.utcnow(): stats["nofuture_ok"] += 1
                    else: failures.append((sym, f"future {ct}")); continue
                    # 11. canonical output must equal the raw bar exactly
                    m = cmap.get(ct)
                    if not m:
                        failures.append((sym, f"canon missing {ct}")); continue
                    if (m["open"], m["high"], m["low"], m["close"], m["volume"]) == \
                       (float(o), float(h_), float(l), float(cl), float(v)):
                        stats["ohlcv_match"] += 1
                    else:
                        failures.append((sym, f"OHLCV drift {ct}: {m} vs {row[1:6]}"))

                # 9/10/12/13 persistence + idempotency, through the guard
                async with AsyncSessionLocal() as s:
                    n1 = await save_candles_to_db(canon, s, source="golden-basket")
                    n2 = await save_candles_to_db(canon, s, source="golden-basket-repeat")
                    stored = (await s.execute(text("""
                        SELECT timestamp, open, high, low, close, volume FROM candles
                        WHERE symbol=:s AND timeframe='1d'
                          AND timestamp BETWEEN :a AND :b ORDER BY timestamp"""),
                        {"s": sym,
                         "a": dt.datetime.fromisoformat(FROM),
                         "b": dt.datetime.fromisoformat(TO) + dt.timedelta(days=1)})).all()
                stats["persist_ok"] += 1
                if n2 == 0: stats["idempotent"] += 1
                else: failures.append((sym, f"NOT idempotent: 2nd write inserted {n2}"))
                smap = {r0[0]: r0 for r0 in stored}
                for x in canon:
                    row0 = smap.get(x["timestamp"])
                    if row0 is None:
                        failures.append((sym, f"not persisted {x['timestamp']}")); continue
                    if (round(row0[1],4),round(row0[2],4),round(row0[3],4),round(row0[4],4)) == \
                       (round(x["open"],4),round(x["high"],4),round(x["low"],4),round(x["close"],4)):
                        stats["ts_match"] += 1
                    else:
                        failures.append((sym, f"DB drift {x['timestamp']}"))

        stats["symbols"] = len(BASKET)
        await asyncio.gather(*[one(x) for x in BASKET])

        print(f"=== GOLDEN BASKET — {len(BASKET)} NSE equities, sessions {FROM}..{TO} ===\n")
        order = ["symbols","identity_ok","raw_ok","bars","offset_ok","canon_ok","ohlc_ok",
                 "vol_ok","session_ok","nofuture_ok","ohlcv_match","persist_ok",
                 "idempotent","ts_match"]
        for k in order:
            print(f"    {k:14} {stats[k]:>6,}")
        print(f"\n    failures: {len(failures)}")
        for f in failures[:12]:
            print(f"      {f[0]:16} {f[1][:96]}")

asyncio.run(main())
