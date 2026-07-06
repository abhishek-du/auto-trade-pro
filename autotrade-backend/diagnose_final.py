import asyncio
from db.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        # Runtime config
        cfg = (await db.execute(text("""
            SELECT key, value FROM runtime_settings 
            WHERE key IN ('INTRADAY_ENABLED','CONFIDENCE_THRESHOLD','MAX_NEW_ENTRIES_PER_CYCLE',
                          'RISK_PER_TRADE_MIN','RISK_PER_TRADE_MAX','SWING_ENABLED','MAX_PORTFOLIO_RISK')
            ORDER BY key
        """))).fetchall()
        print("=== RUNTIME CONFIG ===")
        for r in cfg: print(f"  {r.key}: {r.value}")

        # Direction breakdown
        breakdown = (await db.execute(text("""
            SELECT direction, COUNT(*) as cnt, 
                   AVG(pnl_percent) as avg_pnl,
                   COUNT(*) FILTER(WHERE pnl > 0) as wins
            FROM paper_trades 
            WHERE status IN ('CLOSED','STOPPED')
            GROUP BY direction
        """))).fetchall()
        print("\n=== DIRECTION BREAKDOWN ===")
        for r in breakdown:
            wr = (r.wins/r.cnt*100) if r.cnt else 0
            print(f"  {r.direction}: {r.cnt} trades | Win: {wr:.0f}% | Avg PnL: {r.avg_pnl:.2f}%")

        # Exit reasons
        exits = (await db.execute(text("""
            SELECT exit_reason, COUNT(*) as cnt, AVG(pnl_percent) as avg_pnl
            FROM paper_trades 
            WHERE status IN ('CLOSED','STOPPED')
            GROUP BY exit_reason ORDER BY cnt DESC
        """))).fetchall()
        print("\n=== EXIT REASONS ===")
        for r in exits:
            print(f"  {r.exit_reason}: {r.cnt} | Avg: {r.avg_pnl:.2f}%")

        # Hold time analysis  
        hold = (await db.execute(text("""
            SELECT symbol, direction, pnl_percent, holding_hours, exit_reason, product
            FROM paper_trades
            WHERE status='CLOSED' AND holding_hours IS NOT NULL
            ORDER BY holding_hours
        """))).fetchall()
        print("\n=== HOLD TIME + PRODUCT ===")
        for r in hold:
            print(f"  {r.symbol} ({r.direction}) | {r.holding_hours:.1f}h | {r.product} | {r.exit_reason} | PnL: {r.pnl_percent:.2f}%")

        # Strategy breakdown
        strat = (await db.execute(text("""
            SELECT strategy_name, COUNT(*) as cnt, AVG(pnl_percent) as avg_pnl
            FROM paper_trades
            WHERE status IN ('CLOSED','STOPPED') AND strategy_name IS NOT NULL
            GROUP BY strategy_name ORDER BY avg_pnl DESC
        """))).fetchall()
        print("\n=== BY STRATEGY ===")
        for r in strat:
            print(f"  {r.strategy_name}: {r.cnt} trades | Avg PnL: {r.avg_pnl:.2f}%")

asyncio.run(main())
