"""STEP 2B §2 — Instrument master integrity. READ-ONLY.

Counts the Upstox bulk master by segment/type, then validates the rows this
system actually persisted. Every number here is a COUNT from source data, not a
restatement of what a previous run reported.
"""
from __future__ import annotations

import asyncio, gzip, json, re, sys
from collections import Counter

import httpx
from sqlalchemy import text

sys.path.insert(0, "/home/cis/windows/auto-trade-pro/autotrade-backend")
from db.database import AsyncSessionLocal

URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


async def main() -> None:
    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as c:
        r = await c.get(URL)
    raw = json.loads(gzip.decompress(r.content))
    print(f"§2.1  BULK MASTER (NSE.json.gz)  total instruments = {len(raw):,}\n")

    seg = Counter(i.get("segment", "?") for i in raw)
    print("  by segment:")
    for k, v in seg.most_common():
        print(f"    {k:16} {v:>7,}")

    print("\n  NSE_EQ broken down by instrument_type:")
    eqseg = [i for i in raw if i.get("segment") == "NSE_EQ"]
    for k, v in Counter(i.get("instrument_type", "?") for i in eqseg).most_common():
        print(f"    {k:16} {v:>7,}")

    print("\n  NSE_INDEX by instrument_type:")
    for k, v in Counter(i.get("instrument_type", "?")
                        for i in raw if i.get("segment") == "NSE_INDEX").most_common():
        print(f"    {k:16} {v:>7,}")

    eq = [i for i in eqseg if i.get("instrument_type") == "EQ"]
    print(f"\n§2.2  WHY 2,639?  NSE_EQ + instrument_type=='EQ' = {len(eq):,}")
    print("      (the sync's filter; everything else in NSE_EQ is a non-EQ series)")

    # What the EQ filter excludes, and whether that is defensible.
    excl = [i for i in eqseg if i.get("instrument_type") != "EQ"]
    print(f"      excluded from NSE_EQ: {len(excl):,}")
    sfx = Counter()
    for i in excl:
        s = i.get("trading_symbol", "")
        m = re.search(r"-([A-Z0-9]{2})$", s)
        sfx[m.group(1) if m else i.get("instrument_type", "?")] += 1
    for k, v in sfx.most_common(12):
        print(f"        {k:14} {v:>7,}")

    # Identity integrity inside the master itself.
    print("\n§2.3  MASTER IDENTITY INTEGRITY (NSE_EQ/EQ rows)")
    ik = Counter(i.get("instrument_key") for i in eq)
    ts = Counter(i.get("trading_symbol") for i in eq)
    isin = Counter(i.get("isin") for i in eq if i.get("isin"))
    print(f"    duplicate instrument_key : {sum(1 for v in ik.values() if v > 1)}")
    print(f"    duplicate trading_symbol : {sum(1 for v in ts.values() if v > 1)}")
    print(f"    duplicate ISIN           : {sum(1 for v in isin.values() if v > 1)}")
    print(f"    NULL/blank instrument_key: {sum(1 for i in eq if not i.get('instrument_key'))}")
    print(f"    NULL/blank ISIN          : {sum(1 for i in eq if not i.get('isin'))}")
    bad_key = [i['instrument_key'] for i in eq
               if not str(i.get('instrument_key','')).startswith('NSE_EQ|')]
    print(f"    instrument_key not NSE_EQ|: {len(bad_key)}  {bad_key[:3]}")
    bad_isin = [i.get('isin') for i in eq
                if i.get('isin') and not re.fullmatch(r'IN[A-Z0-9]{10}', i['isin'])]
    print(f"    malformed ISIN            : {len(bad_isin)}  {bad_isin[:3]}")
    odd = [i['trading_symbol'] for i in eq
           if not re.fullmatch(r'[A-Z0-9&*\-\.]{1,30}', i.get('trading_symbol',''))]
    print(f"    unusual trading_symbol    : {len(odd)}  {odd[:5]}")

    dup_isin = [k for k, v in isin.items() if v > 1]
    if dup_isin:
        print(f"    ISINs appearing twice     : {dup_isin[:5]}")
        for d in dup_isin[:3]:
            print(f"      {d}: {[i['trading_symbol'] for i in eq if i.get('isin')==d]}")

    # What actually landed in our DB.
    print("\n§2.4  PERSISTED ROWS (kite_instruments)")
    async with AsyncSessionLocal() as s:
        async def q(sql):
            return (await s.execute(text(sql))).all()
        for label, sql in [
            ("rows by exchange", "SELECT exchange, count(*) FROM kite_instruments GROUP BY 1 ORDER BY 2 DESC"),
            ("NSE by instrument_type", "SELECT instrument_type, count(*) FROM kite_instruments WHERE exchange='NSE' GROUP BY 1 ORDER BY 2 DESC"),
        ]:
            print(f"    {label}:")
            for row in await q(sql):
                print(f"      {str(row[0]):16} {row[1]:>7,}")

        checks = [
            ("dup instrument_key (NSE)",
             "SELECT count(*) FROM (SELECT instrument_key FROM kite_instruments WHERE exchange='NSE' AND instrument_key IS NOT NULL GROUP BY 1 HAVING count(*)>1) t"),
            ("dup (exchange,tradingsymbol)",
             "SELECT count(*) FROM (SELECT exchange,tradingsymbol FROM kite_instruments GROUP BY 1,2 HAVING count(*)>1) t"),
            ("dup ISIN within NSE",
             "SELECT count(*) FROM (SELECT isin FROM kite_instruments WHERE exchange='NSE' AND isin IS NOT NULL GROUP BY 1 HAVING count(*)>1) t"),
            ("NSE EQ rows",
             "SELECT count(*) FROM kite_instruments WHERE exchange='NSE' AND instrument_type='EQ'"),
            ("NSE EQ with instrument_key",
             "SELECT count(*) FROM kite_instruments WHERE exchange='NSE' AND instrument_type='EQ' AND instrument_key IS NOT NULL"),
            ("NSE EQ with ISIN",
             "SELECT count(*) FROM kite_instruments WHERE exchange='NSE' AND instrument_type='EQ' AND isin IS NOT NULL"),
            ("NSE rows whose key is NOT NSE_EQ|",
             "SELECT count(*) FROM kite_instruments WHERE exchange='NSE' AND instrument_key IS NOT NULL AND instrument_key NOT LIKE 'NSE\\_EQ|%'"),
            ("BSE rows carrying an instrument_key",
             "SELECT count(*) FROM kite_instruments WHERE exchange='BSE' AND instrument_key IS NOT NULL"),
            ("rows with a .BO tradingsymbol",
             "SELECT count(*) FROM kite_instruments WHERE tradingsymbol LIKE '%.BO'"),
            ("NSE EQ with series suffix (-BE/-SM/...)",
             "SELECT count(*) FROM kite_instruments WHERE exchange='NSE' AND instrument_type='EQ' AND tradingsymbol ~ '-[A-Z0-9]{2}$'"),
        ]
        print("    validations:")
        for label, sql in checks:
            v = (await s.execute(text(sql))).scalar()
            print(f"      {label:42} {v:>7,}")

        # Staleness of the persisted master
        r2 = (await s.execute(text(
            "SELECT min(refreshed_at), max(refreshed_at) FROM kite_instruments WHERE exchange='NSE'"))).first()
        print(f"      NSE refreshed_at range: {r2[0]}  ->  {r2[1]}")

        # Rows in the DB that the master no longer lists (delisted candidates)
        master_syms = {i["trading_symbol"] for i in eq}
        db_syms = {r[0] for r in await q(
            "SELECT tradingsymbol FROM kite_instruments WHERE exchange='NSE' AND instrument_type='EQ'")}
        gone = db_syms - master_syms
        new = master_syms - db_syms
        print(f"      in DB but NOT in today's master : {len(gone):,}  {sorted(gone)[:6]}")
        print(f"      in master but NOT in DB         : {len(new):,}  {sorted(new)[:6]}")

asyncio.run(main())
