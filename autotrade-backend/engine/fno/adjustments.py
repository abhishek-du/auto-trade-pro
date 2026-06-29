import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.models import OpenPosition
from utils.logger import logger

async def trail_stop_loss_atr(session: AsyncSession, current_prices: dict, atr_value: float = 20.0):
    """
    Dynamically trails stop losses for all open F&O positions based on ATR.
    current_prices: dict mapping symbol -> latest price.
    """
    positions = (await session.execute(select(OpenPosition).where(OpenPosition.status == "OPEN"))).scalars().all()
    updated = 0
    
    for pos in positions:
        if pos.symbol not in current_prices:
            continue
            
        current_p = current_prices[pos.symbol]
        
        # Trailing for SHORT (Sell) legs
        if pos.direction.value == "SELL":
            # If price moves in our favor (drops), trail the SL down
            new_sl = current_p + (atr_value * 1.5)
            if pos.stop_loss == 0 or new_sl < pos.stop_loss:
                pos.stop_loss = new_sl
                updated += 1
                logger.info(f"[ADJUSTMENTS] Trailed SL for {pos.symbol} (SELL) to {new_sl:.2f}")
                
        # Trailing for LONG (Buy) legs
        elif pos.direction.value == "BUY":
            # If price moves in our favor (rises), trail the SL up
            new_sl = current_p - (atr_value * 1.5)
            if pos.stop_loss == 0 or new_sl > pos.stop_loss:
                pos.stop_loss = new_sl
                updated += 1
                logger.info(f"[ADJUSTMENTS] Trailed SL for {pos.symbol} (BUY) to {new_sl:.2f}")
                
    if updated > 0:
        await session.commit()

async def roll_untested_condor_leg(session: AsyncSession, underlying: str, spot: float):
    """
    Rolls the untested side of an Iron Condor closer to the spot to collect more credit.
    (Placeholder for advanced dynamic adjustment logic).
    """
    logger.info(f"[ADJUSTMENTS] Evaluating leg rolls for {underlying} at {spot}")
    # Logic: Identify if spot has moved > 150 points towards one wing.
    # If yes, square off the opposite wing and sell a closer wing.
    pass
