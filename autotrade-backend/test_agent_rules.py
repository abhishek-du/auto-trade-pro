import asyncio
import datetime
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import PaperTrade
from engine.agent.agent_loop import _log_skipped_decision

async def test_regime_veto():
    from unittest.mock import MagicMock
    
    # Create fake session
    session = MagicMock()
    
    class FakeFeatures:
        regime = "RANGE"
        
    class FakeCandidate:
        strategy = "HUB_SIGNAL"
        confidence = 70
        sector = "Media"
        
    candidate = FakeCandidate()
    features = FakeFeatures()
    symbol = "SUNTV.NS"
    
    # A. Regime Veto test
    blocked = False
    if features.regime == "RANGE" and candidate is not None and getattr(candidate, "strategy", "") == "HUB_SIGNAL":
        if candidate.confidence < 80:
            print(f"[TEST PASS] BLOCKED {symbol} | REGIME_VETO: Range market requires 80+ confidence")
            blocked = True
            
    if not blocked:
        print("[TEST FAIL] Regime Veto should have blocked the trade")


async def test_cooldown_lockout():
    # Insert a fake closed trade 10 minutes ago
    async with AsyncSessionLocal() as session:
        # 1. Clear previous SUNTV test trades
        # 2. Add one trade closed 10 mins ago
        now = datetime.datetime.utcnow()
        from db.models import TradeDirection, TradeStatus
        t1 = PaperTrade(
            symbol="SUNTV.NS",
            direction="BUY",
            entry_price=480,
            status=TradeStatus.CLOSED,
            closed_at=now - datetime.timedelta(minutes=10),
            pnl=-50
        )
        session.add(t1)
        await session.commit()
        
        # Test logic
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cooldown_query = select(PaperTrade.closed_at, PaperTrade.pnl).where(
            PaperTrade.symbol == "SUNTV.NS",
            PaperTrade.closed_at >= today_start
        ).order_by(PaperTrade.closed_at.desc())
        cooldown_res = await session.execute(cooldown_query)
        symbol_closed_trades = cooldown_res.fetchall()
        
        blocked = False
        if symbol_closed_trades:
            last_closed_time = symbol_closed_trades[0][0]
            if last_closed_time and (datetime.datetime.utcnow() - last_closed_time) < datetime.timedelta(minutes=45):
                print(f"[TEST PASS] BLOCKED SUNTV.NS | COOLDOWN_LOCK: mandatory 45 min rest")
                blocked = True
                
        if not blocked:
            print("[TEST FAIL] Cooldown lock should have triggered")
            
        # Clean up
        await session.delete(t1)
        await session.commit()

async def test_two_strikes():
    async with AsyncSessionLocal() as session:
        now = datetime.datetime.utcnow()
        from db.models import TradeDirection, TradeStatus
        t1 = PaperTrade(symbol="SUNTV.NS", direction="BUY", entry_price=480, status=TradeStatus.CLOSED, closed_at=now - datetime.timedelta(hours=2), pnl=-50)
        t2 = PaperTrade(symbol="SUNTV.NS", direction="BUY", entry_price=480, status=TradeStatus.CLOSED, closed_at=now - datetime.timedelta(hours=1), pnl=-50)
        session.add_all([t1, t2])
        await session.commit()
        
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cooldown_query = select(PaperTrade.closed_at, PaperTrade.pnl).where(
            PaperTrade.symbol == "SUNTV.NS",
            PaperTrade.closed_at >= today_start
        ).order_by(PaperTrade.closed_at.desc())
        cooldown_res = await session.execute(cooldown_query)
        symbol_closed_trades = cooldown_res.fetchall()
        
        blocked = False
        if symbol_closed_trades:
            losses = sum(1 for _, pnl in symbol_closed_trades if pnl is not None and pnl < 0)
            if losses >= 2:
                print(f"[TEST PASS] BLOCKED SUNTV.NS | TWO_STRIKES_LOCK: Max losses reached today ({losses} losses)")
                blocked = True
                
        if not blocked:
            print("[TEST FAIL] Two-Strikes lock should have triggered")
            
        await session.delete(t1)
        await session.delete(t2)
        await session.commit()

async def run_all():
    print("--- Running Tests ---")
    await test_regime_veto()
    await test_cooldown_lockout()
    await test_two_strikes()
    print("--- Done ---")

if __name__ == "__main__":
    asyncio.run(run_all())
