import json
import sys
import pandas as pd
from collections import defaultdict
import numpy as np

def aggregate_stats(all_trades, equity=500_000.0):
    if not all_trades:
        return {"total_trades": 0, "error": "no_trades"}

    wins   = [t for t in all_trades if t["pnl"] > 0]
    losses = [t for t in all_trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses)) or 1e-9

    win_pct    = len(wins) / len(all_trades)
    avg_win    = gw / len(wins)   if wins   else 0.0
    avg_loss   = gl / len(losses) if losses else 0.0
    expectancy = win_pct * avg_win - (1 - win_pct) * avg_loss

    by_date = defaultdict(float)
    for t in all_trades:
        day = (t.get("ts_exit") or "")[:10]
        if day:
            by_date[day] += t["pnl"]

    pnl_s  = pd.Series(by_date).sort_index()
    rets   = pnl_s / equity
    sharpe = float(np.sqrt(252) * rets.mean() / (rets.std() + 1e-9)) if len(rets) > 1 else 0.0

    gross_rolling = pnl_s.abs().rolling(30, min_periods=1).mean() * 30 * 15  # 15 positions instead of total symbols
    cum_pnl = pnl_s.cumsum()
    peak    = cum_pnl.expanding(min_periods=1).max()
    dd_inr  = cum_pnl - peak
    dd_pct  = (dd_inr / (gross_rolling + 1e-9)) * 100
    
    worst_dd_pct = float(dd_pct.min()) if not dd_pct.empty else 0.0
    worst_dd_inr = float(dd_inr.min()) if not dd_inr.empty else 0.0

    return {
        "total_trades": len(all_trades),
        "winners": len(wins),
        "losers": len(losses),
        "win_rate_pct": round(win_pct * 100, 2),
        "avg_win_inr": round(avg_win, 2),
        "avg_loss_inr": round(avg_loss, 2),
        "profit_factor": round(gw / gl, 2),
        "expectancy_per_trade": round(expectancy, 2),
        "gross_profit_inr": round(gw, 2),
        "gross_loss_inr": round(gl, 2),
        "net_pnl_inr": round(sum(t["pnl"] for t in all_trades), 2),
        "sharpe_annual": round(sharpe, 2),
        "max_drawdown_pct": round(worst_dd_pct, 2),
        "max_drawdown_inr": round(worst_dd_inr, 2)
    }

def year_breakdown(all_trades):
    by_yr = defaultdict(list)
    for t in all_trades:
        by_yr[t["ts_exit"][:4]].append(t)
    out = {}
    for yr, trds in sorted(by_yr.items()):
        s = aggregate_stats(trds)
        pass_gate = s["profit_factor"] >= 1.0 and s["net_pnl_inr"] > 0
        out[yr] = {
            "trades": len(trds),
            "win_rate_pct": s.get("win_rate_pct", 0),
            "profit_factor": s.get("profit_factor", 0),
            "net_pnl_inr": s.get("net_pnl_inr", 0),
            "verdict": "PASS" if pass_gate else "FAIL",
        }
    return out

def run_portfolio_sim(json_path, max_positions=15):
    with open(json_path, 'r') as f:
        data = json.load(f)
    
    all_trades = data.get("all_trades", [])
    if not all_trades:
        print("No trades found in JSON.")
        return
        
    # Group by entry date
    trades_by_entry = defaultdict(list)
    for t in all_trades:
        trades_by_entry[t["ts"][:10]].append(t)
        
    # Get all unique dates
    all_dates = sorted(list(set([t["ts"][:10] for t in all_trades] + [t["ts_exit"][:10] for t in all_trades])))
    
    open_positions = []
    constrained_trades = []
    
    for date in all_dates:
        # 1. Close positions exiting today
        still_open = []
        for p in open_positions:
            if p["ts_exit"][:10] == date:
                constrained_trades.append(p)
            else:
                still_open.append(p)
        open_positions = still_open
        
        # 2. Open new positions if we have slots
        if date in trades_by_entry:
            # Sort today's candidates by confidence descending
            candidates = sorted(trades_by_entry[date], key=lambda x: x.get("confidence", 0), reverse=True)
            
            for cand in candidates:
                if len(open_positions) < max_positions:
                    open_positions.append(cand)
                else:
                    pass # Ignore trade due to portfolio constraint
                    
    stats = aggregate_stats(constrained_trades)
    yd = year_breakdown(constrained_trades)
    
    print(json.dumps({
        "stats": stats,
        "year_breakdown": yd
    }, indent=2))

if __name__ == "__main__":
    run_portfolio_sim(sys.argv[1])
