import asyncio
import asyncpg
import pandas as pd
from datetime import datetime, timedelta
import sys

from crawler.upstox_quotes import ensure_key_map, _KEY_MAP
from crawler.upstox_candles import get_upstox_candles_for_range
from crawler.price_feed import save_candles_to_db
from utils.candle_contract import validate_canonical_daily_equity_candle, classify_instrument, InstrumentClass
from tasks._db import celery_session
from sqlalchemy import text

async def main():
    await ensure_key_map()
    
    # 1. Dynamically derive the universe
    nse_equity = []
    etf_inav = []
    
    for sym in _KEY_MAP.keys():
        cls = classify_instrument(sym)
        if cls == InstrumentClass.NSE_EQUITY:
            nse_equity.append(sym)
        elif cls == InstrumentClass.ETF_OR_INAV:
            etf_inav.append(sym)
            
    print(f"Current universe dynamically derived:")
    print(f"  NSE_EQUITY: {len(nse_equity)}")
    print(f"  ETF_OR_INAV: {len(etf_inav)}")
    
    # Pilot symbols
    pilot_symbols = [
        'RELIANCE.NS', 'TCS.NS', 'INFY.NS', 'BAJFINANCE.NS', 'LTTS.NS', 
        'ASTRAL.NS', 'DELTACORP.NS', 'ITC.NS',
        'NIFTYBEES.NS', 'BANKBEES.NS'
    ]
    
    start_date = (datetime.now() - timedelta(days=3650)).strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')
    
    print(f"\nRunning Pilot Backfill for 10 symbols ({start_date} to {end_date})")
    
    conn = await asyncpg.connect(user='autotrade', password='autotrade', database='autotrade_pro', host='127.0.0.1')
    
    for sym in pilot_symbols:
        cls = classify_instrument(sym)
        print(f"\n--- {sym} ({cls.name}) ---")
        
        # Fresh Upstox Fetch
        candles = await get_upstox_candles_for_range(sym, start_date, end_date, interval="1d")
        api_received = len(candles)
        
        # Canonical Validation
        valid_candles = []
        violations = {
            'weekend_holiday': 0,
            'timestamp': 0,
            'ohlc': 0,
            'other': 0
        }
        
        for c in candles:
            # Check basic OHLC validity (must be positive)
            if c['open'] <= 0 or c['high'] <= 0 or c['low'] <= 0 or c['close'] <= 0:
                violations['ohlc'] += 1
                continue
                
            ok, reason = validate_canonical_daily_equity_candle(c)
            if ok:
                valid_candles.append(c)
            else:
                if 'weekend' in str(reason) or 'holiday' in str(reason):
                    violations['weekend_holiday'] += 1
                elif 'timestamp' in str(reason) or 'canonical session open' in str(reason):
                    violations['timestamp'] += 1
                else:
                    violations['other'] += 1
                    
        print(f"API rows received: {api_received}")
        print(f"Rows passing canonical validation: {len(valid_candles)}")
        print(f"Weekend/holiday violations: {violations['weekend_holiday']}")
        print(f"Timestamp convention violations: {violations['timestamp']}")
        print(f"OHLC violations: {violations['ohlc']}")
        
        if not valid_candles:
            print("No valid candles to insert.")
            continue
            
        first_session = valid_candles[0]['timestamp'].date()
        last_session = valid_candles[-1]['timestamp'].date()
        print(f"First/Last session: {first_session} to {last_session}")
        
        # Check existing 03:45 rows for fresh-Upstox comparison
        q_existing = f"SELECT timestamp, close, volume FROM candles WHERE symbol = '{sym}' AND timeframe = '1d' AND to_char(timestamp, 'HH24:MI') = '03:45' ORDER BY timestamp"
        existing_rows = await conn.fetch(q_existing)
        
        if existing_rows:
            existing_dict = {r['timestamp']: r for r in existing_rows}
            match_count = 0
            mismatch_count = 0
            for vc in valid_candles:
                ts = vc['timestamp']
                if ts in existing_dict:
                    ex = existing_dict[ts]
                    if abs(vc['close'] - ex['close']) < 0.01:
                        match_count += 1
                    else:
                        mismatch_count += 1
            print(f"Fresh-Upstox Price-Basis Comparison vs existing 03:45 rows:")
            print(f"  Overlap rows tested: {match_count + mismatch_count}")
            print(f"  Matches: {match_count}")
            print(f"  Mismatches: {mismatch_count}")
        else:
            print(f"Fresh-Upstox Comparison: No existing 03:45 series found for {sym}")
            
        # Determine existing rows count before insert
        q_count = f"SELECT COUNT(*) FROM candles WHERE symbol = '{sym}' AND timeframe = '1d' AND to_char(timestamp, 'HH24:MI') = '03:45'"
        rows_before = await conn.fetchval(q_count)
        
        # Insert
        async with celery_session() as s2:
            inserted = await save_candles_to_db(valid_candles, s2, source="oneoff_backfill")
            await s2.commit()
            
        # Determine rows after
        rows_after = await conn.fetchval(q_count)
        already_existing = len(valid_candles) - inserted
        
        # Count explicit duplicates from DB perspective (not strictly needed since we used UPSERT, but good to report)
        print(f"Rows inserted: {inserted}")
        print(f"Rows already existing (skipped): {already_existing}")
        
    await conn.close()

if __name__ == '__main__':
    asyncio.run(main())
