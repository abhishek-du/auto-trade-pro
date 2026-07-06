import asyncio
from db.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        # 1. Closed trades - win/loss ratio
        closed = (await db.execute(text("""
            SELECT 
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE pnl > 0) as wins,
                COUNT(*) FILTER (WHERE pnl < 0) as losses,
                AVG(pnl_percent) as avg_pnl_pct,
                SUM(pnl) as total_pnl,
                AVG(pnl_percent) FILTER (WHERE pnl > 0) as avg_win_pct,
                AVG(pnl_percent) FILTER (WHERE pnl < 0) as avg_loss_pct
            FROM paper_trades 
            WHERE status IN ('CLOSED', 'STOPPED')
        """))).fetchone()
        
        print("=== CLOSED TRADES ANALYSIS ===")
        print(f"Total Closed: {closed.total}")
        print(f"Wins: {closed.wins} | Losses: {closed.losses}")
        if closed.total:
            print(f"Win Rate: {(closed.wins or 0) / closed.total * 100:.1f}%")
        print(f"Avg PnL%: {closed.avg_pnl_pct:.2f}%" if closed.avg_pnl_pct else "Avg PnL%: N/A")
        print(f"Avg Win%: {closed.avg_win_pct:.2f}%" if closed.avg_win_pct else "Avg Win%: N/A")
        print(f"Avg Loss%: {closed.avg_loss_pct:.2f}%" if closed.avg_loss_pct else "Avg Loss%: N/A")
        print(f"Total PnL: ₹{closed.total_pnl:,.0f}" if closed.total_pnl else "Total PnL: N/A")

        # 2. How trades closed - SL hit vs TP hit
        reasons = (await db.execute(text("""
            SELECT 
                status,
                COUNT(*) as count,
                AVG(pnl_percent) as avg_pnl
            FROM paper_trades 
            WHERE status IN ('CLOSED', 'STOPPED')
            GROUP BY status
        """))).fetchall()
        
        print("\n=== HOW TRADES CLOSED ===")
        for r in reasons:
            print(f"  {r.status}: {r.count} trades, avg PnL: {r.avg_pnl:.2f}%" if r.avg_pnl else f"  {r.status}: {r.count} trades")

        # 3. Exit reasons
        exit_reasons = (await db.execute(text("""
            SELECT exit_reason, COUNT(*) as count, AVG(pnl_percent) as avg_pnl
            FROM paper_trades 
            WHERE status IN ('CLOSED', 'STOPPED') AND exit_reason IS NOT NULL
            GROUP BY exit_reason
            ORDER BY count DESC
            LIMIT 10
        """))).fetchall()
        
        print("\n=== EXIT REASONS ===")
        for r in exit_reasons:
            print(f"  {r.exit_reason}: {r.count} trades, avg: {r.avg_pnl:.2f}%" if r.avg_pnl else f"  {r.exit_reason}: {r.count}")

        # 4. Best and worst trades
        best = (await db.execute(text("""
            SELECT symbol, direction, entry_price, exit_price, pnl_percent, status
            FROM paper_trades WHERE pnl IS NOT NULL
            ORDER BY pnl_percent DESC LIMIT 5
        """))).fetchall()
        
        print("\n=== TOP 5 BEST TRADES ===")
        for r in best:
            print(f"  {r.symbol} ({r.direction}) | Entry: {r.entry_price:.2f} | Exit: {r.exit_price:.2f} | PnL: {r.pnl_percent:.2f}% | {r.status}")

        worst = (await db.execute(text("""
            SELECT symbol, direction, entry_price, exit_price, pnl_percent, status
            FROM paper_trades WHERE pnl IS NOT NULL
            ORDER BY pnl_percent ASC LIMIT 5
        """))).fetchall()
        
        print("\n=== TOP 5 WORST TRADES ===")
        for r in worst:
            print(f"  {r.symbol} ({r.direction}) | Entry: {r.entry_price:.2f} | Exit: {r.exit_price:.2f} | PnL: {r.pnl_percent:.2f}% | {r.status}")

asyncio.run(main())
