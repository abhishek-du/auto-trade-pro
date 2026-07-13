import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import OpenPosition, NewsItem, MasterIntelligenceScore, FundamentalData
from utils.logger import logger
from utils.llm import call_llm_chat
from engine.agent.decision_engine import _parse_first_json
from datetime import datetime, timedelta

async def llm_dynamic_sl_tp(session: AsyncSession) -> None:
    """
    Dynamically analyze open positions and update Stop Loss & Take Profit based on
    LLM reasoning. This makes the agent manage trades actively like a human expert,
    keeping a "peni nazar" (sharp eye) on price action and news.
    """
    positions = (await session.execute(select(OpenPosition))).scalars().all()
    if not positions:
        return
        
    # Get latest news for context
    since = datetime.utcnow() - timedelta(minutes=60)
    news = (await session.execute(
        select(NewsItem.headline).where(NewsItem.crawled_at > since).order_by(NewsItem.crawled_at.desc()).limit(15)
    )).scalars().all()
    
    news_text = "\\n".join([f"- {n}" for n in news])
    
    async def _manage_pos(pos: OpenPosition):
        # Fetch the 7-Hub Master Score for this symbol
        hub_row = (await session.execute(
            select(MasterIntelligenceScore).where(MasterIntelligenceScore.symbol == pos.symbol).order_by(MasterIntelligenceScore.id.desc()).limit(1)
        )).scalar_one_or_none()
        
        # Fetch deep Fundamental Data
        fund_row = (await session.execute(
            select(FundamentalData).where(FundamentalData.symbol.in_([pos.symbol, pos.symbol.replace(".NS", "")])).limit(1)
        )).scalar_one_or_none()
        
        hub_data = "No Hub Data Available"
        if hub_row:
            hub_data = (f"Master Score: {hub_row.master_score}\\n"
                        f"Technical: {hub_row.technical_score} | Fundamental: {hub_row.fundamental_score}\\n"
                        f"Sector: {hub_row.sector_score} | News: {hub_row.news_score}\\n"
                        f"Options: {hub_row.options_score} | Macro: {hub_row.macro_score} | Earnings: {hub_row.earnings_score}")

        fund_data = "No Deep Fundamental Data Available"
        if fund_row:
            fund_data = (f"PE: {fund_row.pe_ratio} | PB: {fund_row.pb_ratio} | ROE: {fund_row.roe}% | ROCE: {fund_row.roce}%\\n"
                         f"D/E: {fund_row.debt_to_equity} | Mkt Cap: {fund_row.market_cap_cr} Cr\\n"
                         f"FII: {fund_row.fii_holding}% | Promoter: {fund_row.promoter_holding}%\\n"
                         f"Rev Growth 3y: {fund_row.revenue_growth_3yr}% | Profit Growth 3y: {fund_row.profit_growth_3yr}%")

        # Get Live Macro (Nifty/BankNifty) from PRICE_CACHE
        from crawler.india_price_feed import PRICE_CACHE
        nifty = PRICE_CACHE.get("^NSEI", {})
        banknifty = PRICE_CACHE.get("^NSEBANK", {})
        macro_text = f"NIFTY 50: {nifty.get('change_pct', 0.0)}% | BANKNIFTY: {banknifty.get('change_pct', 0.0)}%"

        prompt = f"""You are an elite, expert human trader managing an open position.
Review the current performance, 7-factor Hub scores, deep fundamental metrics, and recent news.
Decide if we should trail the Stop Loss (SL) up to lock in profits, or adjust the Take Profit (TP) target higher/lower based on momentum.

Stock: {pos.symbol}
Direction: {pos.direction.value}
Entry Price: {pos.entry_price}
Current Price: {pos.current_price}
Current SL: {pos.stop_loss}
Current TP: {pos.take_profit}
Unrealised PnL: {pos.unrealised_pct:.2f}%

[LIVE MACRO CONTEXT]:
{macro_text}

[7-FACTOR HUB SCORE ANALYSIS]:
{hub_data}

[DEEP FUNDAMENTALS (Screener/Financials)]:
{fund_data}

[RECENT GLOBAL/INDIAN NEWS]:
{news_text}

Instructions:
1. Act smartly and cleverly. Do deep reasoning.
2. Combine the 7-Hub scores and News into ONE holistic decision.
3. If it is in profit and momentum/Hub is good, trail the SL up (protect capital) and maybe extend the TP.
4. If it is struggling, tighten the SL.
5. If it hit a resistance or bad news came, exit now by setting both SL and TP to Current Price.

Respond ONLY with valid JSON:
{{
    "new_stop_loss": 105.50,
    "new_take_profit": 115.00,
    "reasoning": "Detailed explanation citing specific 7-Hub scores (e.g. Technical=40, News=50), specific recent news events, and current PnL to justify the dynamic update."
}}
"""
        try:
            resp = await call_llm_chat(
                [{"role": "system", "content": "You are an aggressive portfolio manager maintaining dynamic SL/TP using holistic data."},
                 {"role": "user", "content": prompt}],
                max_tokens=1000, temperature=0.2
            )
            data = _parse_first_json(resp)
            if data and data.get("new_stop_loss") and data.get("new_take_profit"):
                new_sl = float(data["new_stop_loss"])
                new_tp = float(data["new_take_profit"])
                
                # Only update if it actually changed
                if abs(new_sl - pos.stop_loss) > 0.01 or abs(new_tp - pos.take_profit) > 0.01:
                    logger.info(f"[dynamic_management] {pos.symbol} LLM updating SL: {pos.stop_loss}->{new_sl}, TP: {pos.take_profit}->{new_tp} | Reason: {data.get('reasoning')}")
                    pos.stop_loss = new_sl
                    pos.take_profit = new_tp
        except Exception as e:
            logger.debug(f"[dynamic_management] Failed for {pos.symbol}: {e}")

    # Run in parallel for speed
    await asyncio.gather(*[_manage_pos(p) for p in positions])
    await session.commit()
