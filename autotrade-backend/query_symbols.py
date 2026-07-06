import asyncio
from sqlalchemy import text
from db.database import get_db

symbols = ['EPACK', 'IGARASHI', 'ATULAUTO', 'RICOAUTO', 'MOSCHIP', 'INDOFARM']

async def main():
    async for db in get_db():
        print("Checking kite_instruments:")
        for sym in symbols:
            res = await db.execute(text("SELECT tradingsymbol, instrument_type, segment FROM kite_instruments WHERE tradingsymbol LIKE :sym LIMIT 5"), {"sym": f"%{sym}%"})
            print(f"{sym}: {res.fetchall()}")
        
        print("\nChecking hub_universe:")
        for sym in symbols:
            res = await db.execute(text("SELECT symbol, turnover_cr, rank FROM hub_universe WHERE symbol LIKE :sym"), {"sym": f"%{sym}%"})
            print(f"{sym}: {res.fetchall()}")
            
        print("\nChecking candles (turnover calculation):")
        for sym in symbols:
            res = await db.execute(text("""
                SELECT symbol, AVG(volume * close) / 10000000 AS avg_turnover_cr 
                FROM candles 
                WHERE timeframe = '1d' AND symbol LIKE :sym 
                  AND timestamp > NOW() - INTERVAL '30 days'
                GROUP BY symbol LIMIT 1
            """), {"sym": f"%{sym}%"})
            print(f"{sym}: {res.fetchall()}")
        break

asyncio.run(main())
