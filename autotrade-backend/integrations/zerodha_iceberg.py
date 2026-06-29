import asyncio
from typing import List
from utils.logger import logger

async def execute_iceberg_order(kite_client, tradingsymbol: str, transaction_type: str, total_quantity: int, limit_price: float, leg_size: int = 50) -> List[str]:
    """
    Executes a large order by breaking it into smaller (Iceberg) legs.
    Helps in minimizing slippage and avoiding large market impacts.
    """
    order_ids = []
    remaining_qty = total_quantity
    
    logger.info(f"[ICEBERG] Starting {transaction_type} for {total_quantity} qty of {tradingsymbol} @ {limit_price}")
    
    while remaining_qty > 0:
        qty = min(remaining_qty, leg_size)
        try:
            # Assumes kite_client is an authenticated KiteConnect instance
            order_id = kite_client.place_order(
                variety=kite_client.VARIETY_REGULAR,
                exchange=kite_client.EXCHANGE_NFO,
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                quantity=qty,
                product=kite_client.PRODUCT_NRML, # NRML for positional spreads
                order_type=kite_client.ORDER_TYPE_LIMIT,
                price=limit_price
            )
            order_ids.append(order_id)
            remaining_qty -= qty
            logger.info(f"[ICEBERG] Placed leg {qty} qty, order_id: {order_id}")
            # Anti-throttling delay
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"[ICEBERG] Failed to place order leg: {e}")
            break
            
    return order_ids
