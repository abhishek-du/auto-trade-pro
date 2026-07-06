import asyncio
from datetime import datetime
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import PaperTrade, OpenPosition, TradeStatus, TradeDirection, VirtualWallet

async def main():
    async with AsyncSessionLocal() as db:
        # Re-insert PaperTrades
        t1 = PaperTrade(
            symbol="BANKNIFTY26JUL57700CE",
            direction=TradeDirection.BUY,
            status=TradeStatus.OPEN,
            entry_price=1534.0,
            stop_loss=0.0,
            take_profit=0.0,
            size_units=90.0,
            size_usd=138060.00,
            instrument_type="OPTION",
            underlying_symbol="BANKNIFTY",
            strike_price=57700.0,
            option_type="CE",
            lot_size=15,
            contract_multiplier=1.0,
            margin_blocked=138060.00,
            ai_reason="Restored accidentally deleted trade",
            pattern_name="Restored"
        )
        t2 = PaperTrade(
            symbol="BANKNIFTY26JUL58200CE",
            direction=TradeDirection.BUY,
            status=TradeStatus.OPEN,
            entry_price=1323.05,
            stop_loss=0.0,
            take_profit=0.0,
            size_units=90.0,
            size_usd=119074.50,
            instrument_type="OPTION",
            underlying_symbol="BANKNIFTY",
            strike_price=58200.0,
            option_type="CE",
            lot_size=15,
            contract_multiplier=1.0,
            margin_blocked=119074.50,
            ai_reason="Restored accidentally deleted trade",
            pattern_name="Restored"
        )
        
        db.add(t1)
        db.add(t2)
        await db.flush() # to get trade IDs
        
        # Re-insert OpenPositions
        p1 = OpenPosition(
            symbol=t1.symbol,
            direction=t1.direction,
            entry_price=t1.entry_price,
            current_price=t1.entry_price,
            stop_loss=t1.stop_loss,
            take_profit=t1.take_profit,
            size_units=t1.size_units,
            size_usd=t1.size_usd,
            instrument_type=t1.instrument_type,
            underlying_symbol=t1.underlying_symbol,
            strike_price=t1.strike_price,
            option_type=t1.option_type,
            lot_size=t1.lot_size,
            contract_multiplier=t1.contract_multiplier,
            margin_blocked=t1.margin_blocked,
            product="MIS",
            trade_style="MIS",
            trade_id=t1.id
        )
        p2 = OpenPosition(
            symbol=t2.symbol,
            direction=t2.direction,
            entry_price=t2.entry_price,
            current_price=t2.entry_price,
            stop_loss=t2.stop_loss,
            take_profit=t2.take_profit,
            size_units=t2.size_units,
            size_usd=t2.size_usd,
            instrument_type=t2.instrument_type,
            underlying_symbol=t2.underlying_symbol,
            strike_price=t2.strike_price,
            option_type=t2.option_type,
            lot_size=t2.lot_size,
            contract_multiplier=t2.contract_multiplier,
            margin_blocked=t2.margin_blocked,
            product="MIS",
            trade_style="MIS",
            trade_id=t2.id
        )
        db.add(p1)
        db.add(p2)
        
        # Deduct wallet
        total_cost = 138060.00 + 119074.50
        wallet_result = await db.execute(select(VirtualWallet))
        wallet = wallet_result.scalar_one_or_none()
        if wallet:
            wallet.balance -= total_cost
            print(f"Restored 2 F&O trades. Deducted {total_cost} from wallet. New balance: {wallet.balance}")
            
        await db.commit()

asyncio.run(main())
