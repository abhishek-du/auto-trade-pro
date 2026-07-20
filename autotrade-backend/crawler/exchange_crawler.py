import asyncio
import pandas as pd
from datetime import datetime, timedelta
import nselib
from nselib import capital_market
from utils.logger import logger

def _parse_nselib_date(date_str: str) -> datetime:
    try:
        return datetime.strptime(str(date_str).strip(), "%d-%b-%Y")
    except Exception:
        return datetime.utcnow()

async def fetch_bulk_deals() -> list[dict]:
    """Fetch recent bulk deals from NSE via nselib."""
    def _fetch():
        try:
            # period='1W' fetches last 7 days. nselib API uses 1W, 1M, etc.
            df = capital_market.bulk_deal_data(period='1W')
            if df is None or df.empty:
                return []
            
            rows = []
            for _, row in df.iterrows():
                symbol = str(row.get("Symbol", "")).strip()
                if not symbol: continue
                
                client = str(row.get("ClientName", "")).title()
                buy_sell = str(row.get("Buy/Sell", "")).upper()
                qty = row.get("QuantityTraded", "0")
                price = row.get("TradePrice/Wght.Avg.Price", "0")
                
                headline = f"BULK DEAL: {client} {buy_sell} {qty} shares of {symbol} @ ₹{price}"
                
                rows.append({
                    "headline": headline,
                    "source": "NSE Bulk Deals",
                    "url": f"https://www.nseindia.com/market-data/bulk-deals",
                    "published_at": _parse_nselib_date(row.get("Date", "")),
                })
            return rows
        except Exception as e:
            logger.error(f"Failed to fetch bulk deals: {e}")
            return []

    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(None, _fetch)
    if res:
        logger.info(f"Bulk Deals ✓  {len(res)} items")
    return res

async def fetch_block_deals() -> list[dict]:
    """Fetch recent block deals from NSE via nselib."""
    def _fetch():
        try:
            df = capital_market.block_deals_data(period='1W')
            if df is None or df.empty:
                return []
            
            rows = []
            for _, row in df.iterrows():
                symbol = str(row.get("Symbol", "")).strip()
                if not symbol: continue
                
                client = str(row.get("ClientName", "")).title()
                buy_sell = str(row.get("Buy/Sell", "")).upper()
                qty = row.get("QuantityTraded", "0")
                price = row.get("TradePrice/Wght.Avg.Price", "0")
                
                headline = f"BLOCK DEAL: {client} {buy_sell} {qty} shares of {symbol} @ ₹{price}"
                
                rows.append({
                    "headline": headline,
                    "source": "NSE Block Deals",
                    "url": f"https://www.nseindia.com/market-data/block-deals",
                    "published_at": _parse_nselib_date(row.get("Date", "")),
                })
            return rows
        except Exception as e:
            logger.error(f"Failed to fetch block deals: {e}")
            return []

    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(None, _fetch)
    if res:
        logger.info(f"Block Deals ✓  {len(res)} items")
    return res
