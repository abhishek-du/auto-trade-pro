import asyncio
import datetime
from collections import defaultdict

from crawler.bhavcopy_fno import fetch_fno_bhavcopy

async def run_iron_condor_backtest(start_date: datetime.date, end_date: datetime.date):
    """
    Simple EOD Backtester for NIFTY Iron Condor.
    Rules:
    - Entry: If no position is open, open a NIFTY Iron Condor for the nearest expiry (at least 2 DTE).
      - Short CE: ATM + 200, Long CE: ATM + 400
      - Short PE: ATM - 200, Long PE: ATM - 400
    - Exit: EOD of the Expiry day.
    """
    print(f"Starting F&O Backtest from {start_date} to {end_date}...")
    
    current_date = start_date
    open_trade = None
    trade_history = []
    
    while current_date <= end_date:
        # Skip weekends
        if current_date.weekday() >= 5:
            current_date += datetime.timedelta(days=1)
            continue
            
        rows = await fetch_fno_bhavcopy(current_date, symbols={"NIFTY"})
        if not rows: # Holiday
            current_date += datetime.timedelta(days=1)
            continue
            
        # Get spot price. Bhavcopy legacy doesn't have spot. We approximate Nifty Spot 
        # using the near-month FUT settle price (or closest CE/PE parity).
        fut_rows = [r for r in rows if r.instrument_type == "FUT"]
        if not fut_rows:
            current_date += datetime.timedelta(days=1)
            continue
        fut_rows.sort(key=lambda x: x.expiry)
        spot_proxy = fut_rows[0].settle
        
        # 1. Check Exit
        if open_trade:
            # Check if today is expiry
            if current_date == open_trade['expiry']:
                # Close trade
                close_pnl = 0
                for leg in open_trade['legs']:
                    # Find the exact contract today
                    c = next((r for r in rows if r.strike == leg['strike'] and r.instrument_type == leg['type'] and r.expiry == open_trade['expiry']), None)
                    if c:
                        exit_price = c.settle
                    else:
                        # Expired OTM
                        exit_price = 0.0
                        
                    if leg['side'] == 'SELL':
                        pnl = (leg['entry'] - exit_price) * 75  # Nifty lot size 75
                    else:
                        pnl = (exit_price - leg['entry']) * 75
                    close_pnl += pnl
                    
                print(f"[{current_date}] EXIT Iron Condor. PnL: \u20b9{close_pnl:.2f}")
                trade_history.append({
                    "entry_date": open_trade['entry_date'],
                    "exit_date": current_date,
                    "pnl": close_pnl
                })
                open_trade = None

        # 2. Check Entry
        if not open_trade:
            # Find nearest expiry >= current_date + 2 days
            expiries = sorted(list(set(r.expiry for r in rows if r.instrument_type == "CE")))
            valid_expiries = [e for e in expiries if (e - current_date).days >= 2]
            if valid_expiries:
                target_expiry = valid_expiries[0]
                atm = round(spot_proxy / 50) * 50
                
                # Strikes
                s_ce, l_ce = atm + 200, atm + 400
                s_pe, l_pe = atm - 200, atm - 400
                
                # Get premiums
                def get_prem(strike, opt_type):
                    c = next((r for r in rows if r.strike == strike and r.instrument_type == opt_type and r.expiry == target_expiry), None)
                    return c.close if c else None
                    
                p_s_ce = get_prem(s_ce, "CE")
                p_l_ce = get_prem(l_ce, "CE")
                p_s_pe = get_prem(s_pe, "PE")
                p_l_pe = get_prem(l_pe, "PE")
                
                if all([p_s_ce, p_l_ce, p_s_pe, p_l_pe]):
                    net_credit = (p_s_ce + p_s_pe) - (p_l_ce + p_l_pe)
                    if net_credit > 0:
                        open_trade = {
                            "entry_date": current_date,
                            "expiry": target_expiry,
                            "spot": spot_proxy,
                            "net_credit": net_credit,
                            "legs": [
                                {"type": "CE", "strike": s_ce, "side": "SELL", "entry": p_s_ce},
                                {"type": "CE", "strike": l_ce, "side": "BUY",  "entry": p_l_ce},
                                {"type": "PE", "strike": s_pe, "side": "SELL", "entry": p_s_pe},
                                {"type": "PE", "strike": l_pe, "side": "BUY",  "entry": p_l_pe},
                            ]
                        }
                        print(f"[{current_date}] ENTRY Iron Condor for Expiry {target_expiry}. Spot: {atm}, Net Credit: \u20b9{net_credit:.2f}")

        current_date += datetime.timedelta(days=1)
        
    # Summary
    if trade_history:
        total_pnl = sum(t["pnl"] for t in trade_history)
        wins = sum(1 for t in trade_history if t["pnl"] > 0)
        win_rate = (wins / len(trade_history)) * 100
        
        print("\n--- BACKTEST RESULTS ---")
        print(f"Total Trades: {len(trade_history)}")
        print(f"Total PnL: \u20b9{total_pnl:.2f}")
        print(f"Win Rate: {win_rate:.1f}%")
    else:
        print("No trades executed.")

if __name__ == "__main__":
    end = datetime.date.today() - datetime.timedelta(days=1)
    start = end - datetime.timedelta(days=60) # Last 60 days
    asyncio.run(run_iron_condor_backtest(start, end))
