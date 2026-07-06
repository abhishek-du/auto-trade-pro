import asyncio
from sqlalchemy import select, delete, update
from db.database import AsyncSessionLocal
from db.models import PaperTrade, OpenPosition, TradeStatus, VirtualWallet

async def main():
    async with AsyncSessionLocal() as db:
        # 1. Find all OpenPositions
        result = await db.execute(select(OpenPosition))
        positions = result.scalars().all()

        if not positions:
            print("No open positions found.")
            return

        print(f"Found {len(positions)} open positions to delete:")
        total_refund = 0.0
        trade_ids = []
        for p in positions:
            # In cash trades, size_usd is the margin blocked
            cost = p.size_usd
            total_refund += cost
            trade_ids.append(p.trade_id)
            print(f"  [{p.id}] {p.symbol}  qty={p.size_units}  entry=₹{p.entry_price}  cost=₹{cost:,.2f}  trade_id={p.trade_id}")

        print(f"\nTotal refund: ₹{total_refund:,.2f}")

        # 2. Delete open_positions rows
        await db.execute(
            delete(OpenPosition).where(OpenPosition.id.in_([p.id for p in positions]))
        )

        # 3. Delete paper_trades
        if trade_ids:
            await db.execute(
                delete(PaperTrade).where(PaperTrade.id.in_(trade_ids))
            )

        # 4. Refund cost to virtual wallet
        wallet_result = await db.execute(select(VirtualWallet))
        wallet = wallet_result.scalar_one_or_none()
        if wallet:
            old_balance = wallet.balance
            wallet.balance += total_refund
            print(f"\nWallet: ₹{old_balance:,.2f}  →  ₹{wallet.balance:,.2f}  (refunded ₹{total_refund:,.2f})")
        else:
            print("WARNING: wallet row not found — balance not updated")

        await db.commit()
        print("\nDone — positions and trades deleted and capital refunded.")

asyncio.run(main())
