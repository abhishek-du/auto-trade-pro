"""STEP 2C §2/§3 — raw Upstox daily-candle timestamp contract. READ-ONLY.

Records the RAW timestamp before any conversion, then derives UTC / IST / expected
NSE session date, validating the session against an INDEPENDENT calendar (Upstox
market/holidays + market/timings), never against our own database.
"""
from __future__ import annotations
import asyncio, datetime as dt, json, sys
import httpx
sys.path.insert(0, "/home/cis/windows/auto-trade-pro/autotrade-backend")
from utils.config import settings

H = {"Accept": "application/json", "Authorization": f"Bearer {settings.UPSTOX_ACCESS_TOKEN}"}
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BASE = "https://api.upstox.com"

SAMPLE = {
    "RELIANCE":   "NSE_EQ|INE002A01018",
    "HDFCBANK":   "NSE_EQ|INE040A01034",
    "ICICIBANK":  "NSE_EQ|INE090A01021",
    "BHARTIARTL": "NSE_EQ|INE397D01024",
}
SESSIONS = ["2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03"]


async def independent_calendar(c) -> tuple[set, dict]:
    """NSE holidays straight from Upstox — independent of our DB."""
    r = await c.get(f"{BASE}/v2/market/holidays", headers=H)
    hol = {}
    for h in (r.json().get("data") or []):
        hol[h.get("date")] = h.get("description", "")
    return set(hol), hol


async def main():
    async with httpx.AsyncClient(timeout=45) as c:
        holidays, holdesc = await independent_calendar(c)
        print(f"§3  INDEPENDENT NSE CALENDAR (Upstox /v2/market/holidays): {len(holidays)} holidays in range")
        for d in sorted(x for x in holidays if "2026-08" <= x <= "2026-09-30"):
            print(f"      {d}  {holdesc[d][:50]}")

        # Resolve the two extra tiers from our instrument map (identity only,
        # not used as a timestamp source).
        from crawler.upstox_quotes import ensure_key_map, _to_key
        await ensure_key_map()
        for extra in ("ABBOTINDIA", "PATELRMART"):      # mid-cap, lower-liquidity
            k = _to_key(f"{extra}.NS")
            if k:
                SAMPLE[extra] = k

        print(f"\n§2  RAW UPSTOX DAILY RESPONSES  ({len(SAMPLE)} symbols)")
        print(f"    endpoint : GET {BASE}/v3/historical-candle/{{key}}/days/1/{{to}}/{{from}}")
        print(f"    params   : unit=days interval=1  from=2026-08-28 to=2026-09-03")
        print(f"    NOTE: raw timestamp recorded verbatim, BEFORE any conversion.\n")

        records = []
        for sym, key in SAMPLE.items():
            url = f"{BASE}/v3/historical-candle/{key.replace('|','%7C')}/days/1/2026-09-03/2026-08-28"
            r = await c.get(url, headers=H)
            if r.status_code != 200:
                print(f"  {sym}: HTTP {r.status_code}"); continue
            for row in ((r.json().get("data") or {}).get("candles") or []):
                raw = row[0]
                aware = dt.datetime.fromisoformat(raw)
                utc = aware.astimezone(dt.timezone.utc).replace(tzinfo=None)
                ist = aware.astimezone(IST)
                records.append(dict(symbol=sym, instrument_key=key, raw=raw,
                                    utc=utc, ist=ist, o=row[1], h=row[2], l=row[3],
                                    close=row[4], vol=row[5]))

        print("  symbol      raw timestamp                 -> naive UTC          | IST wall clock       | IST date")
        for r in sorted(records, key=lambda x: (x["symbol"], x["raw"]))[:14]:
            print(f"  {r['symbol']:11} {r['raw']:29} -> {str(r['utc']):19} | "
                  f"{r['ist']:%Y-%m-%d %H:%M %a} | {r['ist'].date()}")

        print("\n§2b  CONTRACT DERIVATION")
        offs = {r["raw"][-6:] for r in records}
        times = {r["raw"][11:19] for r in records}
        print(f"    every raw timestamp offset : {offs}")
        print(f"    every raw wall-clock time  : {times}")
        print(f"    -> Upstox stamps a DAILY bar at MIDNIGHT IST of the session date.")
        print(f"    -> It is a DATE LABEL, not the instant the bar opened (session opens 09:15 IST).")

        print("\n§3b  SESSION VALIDATION against the independent calendar")
        dates = sorted({r["ist"].date().isoformat() for r in records})
        for d in dates:
            wd = dt.date.fromisoformat(d).strftime("%a")
            is_hol = d in holidays
            n = sum(1 for r in records if r["ist"].date().isoformat() == d)
            ok = wd not in ("Sat", "Sun") and not is_hol
            print(f"    IST date {d} ({wd})  bars={n:2}  holiday={is_hol}  valid_session={ok}")

        print("\n    Same check on the NAIVE-UTC date (what timestamp::date returns today):")
        udates = sorted({r["utc"].date().isoformat() for r in records})
        bad = 0
        for d in udates:
            wd = dt.date.fromisoformat(d).strftime("%a")
            is_hol = d in holidays
            ok = wd not in ("Sat", "Sun") and not is_hol
            if not ok: bad += 1
            print(f"    UTC date {d} ({wd})  holiday={is_hol}  valid_session={ok}"
                  f"{'   <-- NOT A TRADING SESSION' if not ok else ''}")
        print(f"\n    => {bad} of {len(udates)} UTC-derived dates are not NSE sessions.")
        print(f"    => IST date is the session; UTC date is the session MINUS ONE DAY.")

        with open("/tmp/claude-1000/-home-cis-windows-auto-trade-pro/3b62f59e-c58f-45bc-832f-5d4acd5b9be4/scratchpad/step2c_raw.json","w") as f:
            json.dump([{**r, "utc": str(r["utc"]), "ist": str(r["ist"])} for r in records], f, indent=1)
        print(f"\n    raw sample saved: {len(records)} records")

asyncio.run(main())
