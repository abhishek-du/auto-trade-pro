"""Runnable replay/validation harness for the Pre-Event Expectation Gap strategy.

Pulls historical scheduled events (MarketEvent EARNINGS rows) in a date range,
freezes a prediction at T-30/15/5/1 before each, measures realized outcomes at
T+1/3/5/10/20, and writes a JSON report + edge verdict to results/.

A positive verdict here is a HARD PREREQUISITE before any paper/live wiring
(Phase 6). See engine/pre_event_expectation_gap/replay.py for the anti-lookahead
guarantees and the documented caveats.

Usage:
    .venv/bin/python scripts/replay_pre_event_gap.py --from 2023-01-01 --out results/pre_event_gap_replay.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from db.database import AsyncSessionLocal
from db.models import MarketEvent
from engine.pre_event_expectation_gap.types import PreEventType
from engine.pre_event_expectation_gap.engine import PreEventExpectationGapEngine
from engine.pre_event_expectation_gap.replay import replay_events
from utils.logger import logger


async def _load_historical_events(from_date: date, to_date: date, symbols: list[str] | None):
    async with AsyncSessionLocal() as s:
        filters = [
            MarketEvent.event_type == "EARNINGS",
            MarketEvent.event_date >= from_date,
            MarketEvent.event_date <= to_date,
            MarketEvent.symbol.is_not(None),
        ]
        if symbols:
            filters.append(MarketEvent.symbol.in_(symbols))
        rows = (await s.execute(select(MarketEvent).where(*filters).order_by(MarketEvent.event_date))).scalars().all()
    return [(r.symbol, r.event_date, PreEventType.QUARTERLY_RESULT) for r in rows]


async def run(from_date: date, to_date: date, symbols: list[str] | None, out_path: str | None) -> dict:
    events = await _load_historical_events(from_date, to_date, symbols)
    logger.info(f"[replay] {len(events)} historical EARNINGS events in range")
    engine = PreEventExpectationGapEngine()
    async with AsyncSessionLocal() as session:
        report = await replay_events(engine, events, session)

    if out_path:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        logger.info(f"[replay] report written to {out_path}")
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from", dest="from_date", required=True, help="YYYY-MM-DD")
    p.add_argument("--to", dest="to_date", default=None, help="YYYY-MM-DD (default: 30 days ago)")
    p.add_argument("--symbols", default=None, help="comma-separated (default: all EARNINGS events in range)")
    p.add_argument("--out", default="results/pre_event_gap_replay.json")
    args = p.parse_args()

    frm = date.fromisoformat(args.from_date)
    to = date.fromisoformat(args.to_date) if args.to_date else (date.today() - timedelta(days=30))
    syms = [s.strip() for s in args.symbols.split(",")] if args.symbols else None

    result = asyncio.run(run(frm, to, syms, args.out))
    print(json.dumps(result.get("verdict", {}), indent=2, default=str))
    print("decision distribution:", result.get("decision_distribution"))
