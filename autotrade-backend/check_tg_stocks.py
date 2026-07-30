import asyncio
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from db.models import PaperTrade, AgentDecision

async def check():
    engine = create_async_engine("postgresql+asyncpg://autotrade:autotrade@localhost:5432/autotrade_pro")
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    
    symbols_to_check = [
        "CLSEL", "CLSEL.NS", "CHAMAN LAL", 
        "PRICOL", "PRICOL.NS", 
        "AURIONPRO", "AURIONPRO.NS"
    ]
    
    async with async_session() as session:
        # Check PaperTrades
        print("--- Trades Taken (PaperTrade) ---")
        stmt_trades = select(PaperTrade).where(
            or_(PaperTrade.symbol.in_(symbols_to_check), 
                PaperTrade.symbol.ilike('%PRICOL%'),
                PaperTrade.symbol.ilike('%AURIONPRO%'),
                PaperTrade.symbol.ilike('%CLSEL%'))
        )
        res_trades = await session.execute(stmt_trades)
        for t in res_trades.scalars().all():
            print(f"Trade: {t.symbol} | Strategy: {t.strategy_name} | Opened: {t.opened_at}")
        
        # Check Decisions (including skipped)
        print("\n--- Agent Decisions (Investigated but maybe skipped) ---")
        stmt_decisions = select(AgentDecision).where(
            or_(AgentDecision.symbol.in_(symbols_to_check),
                AgentDecision.symbol.ilike('%PRICOL%'),
                AgentDecision.symbol.ilike('%AURIONPRO%'),
                AgentDecision.symbol.ilike('%CLSEL%'))
        ).order_by(AgentDecision.created_at.desc()).limit(10)
        res_dec = await session.execute(stmt_decisions)
        
        for d in res_dec.scalars().all():
            reason = str(getattr(d, 'reasons', getattr(d, 'reason', '')))[:100]
            print(f"Decision: {d.symbol} | Action: {d.action} | Confidence: {d.confidence}% | Reason: {reason}")

if __name__ == "__main__":
    asyncio.run(check())
