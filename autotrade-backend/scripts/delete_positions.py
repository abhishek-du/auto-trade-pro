"""One-shot script: delete three option positions opened on 29-Jun-2026 9:20 am
and refund their cost to the virtual wallet.

Run from autotrade-backend/:
  .venv/bin/python scripts/delete_positions.py
"""
import asyncio
from sqlalchemy import select, delete, update
from db.database import AsyncSessionLocal
from db.models import PaperTrade, OpenPosition, VirtualWallet, TradeStatus

# Symbols as they appear in the DB (NSE F&O format)
TARGET_SYMBOLS = ["FINNIFTY26800CE", "BANKNIFTY58200CE", "NIFTY24050CE"]

async def main():
    async with AsyncSessionLocal() as db:
        # ── 1. Find the open positions for these symbols ──────────────────────
        stmt = (
            select(OpenPosition)
            .where(OpenPosition.symbol.in_(TARGET_SYMBOLS))
        )
        result = await db.execute(stmt)
        positions = result.scalars().all()

        if not positions:
            # Try a broader match in case the symbol has expiry embedded
            print("Exact match not found — trying LIKE search...")
            from sqlalchemy import or_
            stmt2 = (
                select(OpenPosition)
                .where(
                    or_(
                        OpenPosition.symbol.like("%FINNIFTY%26800%CE%"),
                        OpenPosition.symbol.like("%BANKNIFTY%58200%CE%"),
                        OpenPosition.symbol.like("%NIFTY%24050%CE%"),
                    )
                )
            )
            result2 = await db.execute(stmt2)
            positions = result2.scalars().all()

        if not positions:
            print("No matching open positions found.")
            return

        print(f"Found {len(positions)} position(s) to delete:")
        total_refund = 0.0
        trade_ids = []
        for p in positions:
            cost = p.size_usd  # size_usd holds the total INR cost for this position
            total_refund += cost
            trade_ids.append(p.trade_id)
            print(f"  [{p.id}] {p.symbol}  qty={p.size_units}  entry=₹{p.entry_price}  cost=₹{cost:,.2f}  trade_id={p.trade_id}")

        print(f"\nTotal refund: ₹{total_refund:,.2f}")

        # ── 2. Delete open_positions rows ────────────────────────────────────
        await db.execute(
            delete(OpenPosition).where(OpenPosition.id.in_([p.id for p in positions]))
        )

        # ── 3. Mark paper_trades as CANCELLED ────────────────────────────────
        if trade_ids:
            await db.execute(
                update(PaperTrade)
                .where(PaperTrade.id.in_(trade_ids))
                .values(status=TradeStatus.STOPPED)
            )

        # ── 4. Refund cost to virtual wallet ─────────────────────────────────
        wallet_result = await db.execute(select(VirtualWallet))
        wallet = wallet_result.scalar_one_or_none()
        if wallet:
            old_balance = wallet.balance
            wallet.balance += total_refund
            print(f"\nWallet: ₹{old_balance:,.2f}  →  ₹{wallet.balance:,.2f}  (refunded ₹{total_refund:,.2f})")
        else:
            print("WARNING: virtual_wallet row not found — balance not updated")

        await db.commit()
        print("\nDone — positions deleted and capital refunded.")

asyncio.run(main())
