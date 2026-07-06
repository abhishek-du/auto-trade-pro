import asyncio
from db.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        # Check runtime settings
        cfg = (await db.execute(text("""
            SELECT key, value FROM runtime_settings 
            WHERE key IN ('INTRADAY_ENABLED', 'CONFIDENCE_THRESHOLD', 'MAX_NEW_ENTRIES_PER_CYCLE', 
                          'RISK_PER_TRADE_MIN', 'RISK_PER_TRADE_MAX', 'SWING_ENABLED', 'MAX_PORTFOLIO_RISK')
            ORDER BY key
        """))).fetchall()
        print("=== CURRENT RUNTIME CONFIG ===")
        for r in cfg: print(f"  {r.key}: {r.value}")

        # Correct column names
        cols = (await db.execute(text("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name='paper_trades' ORDER BY ordinal_position LIMIT 20
        """))).fetchall()
        print("\n=== paper_trades columns ===")
        print([c[0] for c in cols])

        # Trade breakdown by direction
        breakdown = (await db.execute(text("""
            SELECT direction, pattern_name, COUNT(*) as count, 
                   AVG(pnl_percent) as avg_pnl
            FROM paper_trades 
            WHERE status IN ('CLOSED', 'STOPPED')
            GROUP BY direction, pattern_name
            ORDER BY count DESC LIMIT 10
        """))).fetchall()
        print("\n=== TRADES BY DIRECTION + PATTERN ===")
        for r in breakdown:
            print(f"  {r.direction} | {r.pattern_name} | Count: {r.count} | Avg PnL: {r.avg_pnl:.2f}%" if r.avg_pnl else f"  {r.direction} | {r.pattern_name} | {r.count}")

        # Hold time
        hold = (await db.execute(text("""
            SELECT symbol, direction, pnl_percent, created_at, closed_at,
                   EXTRACT(EPOCH FROM (closed_at - created_at))/60 as hold_min
            FROM paper_trades
            WHERE status='CLOSED' AND closed_at IS NOT NULL AND created_at IS NOT NULL
            ORDER BY hold_min
        """))).fetchall()
        print("\n=== HOLD TIME PER TRADE ===")
        for r in hold:
            print(f"  {r.symbol} ({r.direction}) | {r.hold_min:.0f} min | PnL: {r.pnl_percent:.2f}%" if r.pnl_percent else f"  {r.symbol} | {r.hold_min:.0f} min")

asyncio.run(main())
