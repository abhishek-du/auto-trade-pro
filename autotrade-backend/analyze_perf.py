import asyncio
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from db.models import PaperTrade, TradeStatus

async def run():
    engine = create_async_engine("postgresql+asyncpg://autotrade:autotrade@localhost:5432/autotrade_pro")
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    
    async with async_session() as session:
        # Get all closed trades grouped by strategy_name
        stmt = select(PaperTrade.strategy_name, PaperTrade.pnl).where(PaperTrade.status != TradeStatus.OPEN).where(PaperTrade.pnl != None)
        result = await session.execute(stmt)
        trades = result.all()
        
        stats = {}
        for strategy, pnl in trades:
            # Handle null strategy names
            strat_name = strategy if strategy else "NEWS_LLM_DEBATE"
            
            if strat_name not in stats:
                stats[strat_name] = {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}
            
            stats[strat_name]["total"] += 1
            stats[strat_name]["total_pnl"] += pnl
            if pnl > 0:
                stats[strat_name]["wins"] += 1
            elif pnl < 0:
                stats[strat_name]["losses"] += 1
                
        print("Performance by Strategy:")
        print("-" * 50)
        for strat, data in sorted(stats.items(), key=lambda x: x[1]["total_pnl"], reverse=True):
            total = data["total"]
            wins = data["wins"]
            pnl = data["total_pnl"]
            win_rate = (wins / total * 100) if total > 0 else 0
            print(f"Strategy: {strat}")
            print(f"  Total Trades: {total}")
            print(f"  Win Rate: {win_rate:.1f}% ({wins} wins, {data['losses']} losses)")
            print(f"  Total PnL: ₹{pnl:.2f}")
            print("-" * 50)

if __name__ == "__main__":
    asyncio.run(run())
