"""One-time reconciliation: close phantom-open AgentTrade ledger rows.

Background
----------
AgentTrade is the agent's own trade ledger (powers /agent/positions and the
agent analytics views). It is written on entry, but historically was only closed
by the agent's own exit path (_record_exit). Positions closed by the paper
mark-to-market task (update_positions_with_current_prices -> close_paper_trade)
left their AgentTrade row OPEN forever — "phantom-open" rows that appear in
/agent/positions but match no real OpenPosition.

close_paper_trade now syncs the ledger on every close, so NO new phantoms are
created. Run this ONCE after deploying that fix (restart Celery) to clear the
existing backlog — running it before the fix is live just lets phantoms
re-accumulate.

This is DISPLAY-ONLY: it sets exit_ts / exit_reason='RECONCILED' / pnl=0 and does
NOT touch the wallet. The real realised P&L already lives in paper_trades and was
applied to the wallet when those positions actually closed; setting pnl=0 here
avoids any double-count if agent_trades.pnl is ever aggregated.

Usage:
    .venv/bin/python scripts/reconcile_agent_ledger.py            # dry run (default)
    .venv/bin/python scripts/reconcile_agent_ledger.py --apply    # perform the close
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from db.database import AsyncSessionLocal
from db.models import AgentTrade, OpenPosition


async def _find_phantoms(session) -> list[AgentTrade]:
    """Open AgentTrade rows whose symbol has no matching open_positions row."""
    open_syms = set((await session.execute(select(OpenPosition.symbol))).scalars().all())
    rows = (await session.execute(
        select(AgentTrade)
        .where(AgentTrade.exit_ts == None)               # noqa: E711  (SQL NULL test)
        .order_by(AgentTrade.symbol, AgentTrade.entry_ts)
    )).scalars().all()
    return [t for t in rows if t.symbol not in open_syms]


async def main(apply: bool) -> None:
    async with AsyncSessionLocal() as session:
        phantoms = await _find_phantoms(session)
        if not phantoms:
            print("No phantom-open AgentTrade rows. Ledger is consistent.")
            return

        print(f"{'APPLYING —' if apply else 'DRY RUN —'} {len(phantoms)} phantom-open "
              f"AgentTrade row(s) (open in ledger, no matching open_position):\n")
        total_notional = 0.0
        for t in phantoms:
            notional = float(t.qty) * float(t.entry_price)
            total_notional += notional
            print(f"  {t.symbol:<18} qty={t.qty:<6} entry={t.entry_price:<10} "
                  f"notional=₹{notional:,.0f}  entry_ts={t.entry_ts}")
            if apply:
                t.exit_price  = t.entry_price   # display-only; real P&L is in paper_trades
                t.exit_ts     = datetime.utcnow()
                t.exit_reason = "RECONCILED"
                t.pnl         = 0.0
        print(f"\n  total phantom notional in ledger: ₹{total_notional:,.0f}")

        if apply:
            await session.commit()
            print(f"\n✓ Closed {len(phantoms)} phantom row(s) as RECONCILED (no wallet effect).")
        else:
            print("\nDry run only — re-run with --apply to close these rows "
                  "(after deploying the close_paper_trade ledger-sync fix).")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Close phantom-open AgentTrade ledger rows")
    p.add_argument("--apply", action="store_true", help="Perform the close (default: dry run)")
    args = p.parse_args()
    asyncio.run(main(args.apply))
