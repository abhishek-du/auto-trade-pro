"""Deep per-trade analysis of a run_backtest.py JSON dump.

Usage: .venv/bin/python scripts/analyze_backtest.py results/backtest_8yr_long.json
Prints: overall, year-by-year, strategy, regime, exit-reason, confidence bucket,
holding-period, R-multiple distribution, monthly, best/worst trades, streaks.
"""
import json, sys
from collections import defaultdict
from datetime import date
import statistics as st

PATH = sys.argv[1] if len(sys.argv) > 1 else "results/backtest_8yr_long.json"
d = json.load(open(PATH))
trades = d["all_trades"]

def days_between(a, b):
    try:
        ya,ma,da = map(int, a[:10].split("-")); yb,mb,db = map(int, b[:10].split("-"))
        return (date(yb,mb,db) - date(ya,ma,da)).days
    except Exception:
        return None

for t in trades:
    t["hold_days"] = days_between(t.get("ts",""), t.get("ts_exit","")) or 0

def block(name, tr):
    if not tr:
        print(f"  {name:<26} n=0"); return
    wins=[x for x in tr if x["pnl"]>0]; losses=[x for x in tr if x["pnl"]<=0]
    gw=sum(x["pnl"] for x in wins); gl=abs(sum(x["pnl"] for x in losses)) or 1e-9
    net=gw-gl; pf=gw/gl; wr=len(wins)/len(tr)*100
    aw=gw/len(wins) if wins else 0; al=gl/len(losses) if losses else 0
    exp=net/len(tr)
    print(f"  {name:<26} n={len(tr):<5} WR={wr:5.1f}%  PF={pf:5.2f}  net=Rs{net:>12,.0f}  "
          f"avgW=Rs{aw:>8,.0f} avgL=Rs{al:>8,.0f} exp=Rs{exp:>7,.0f}")

print("="*118)
print(f"  DEEP BACKTEST ANALYSIS  |  {d['from_date']} -> {d['to_date']}  |  "
      f"symbols tested={d['symbols_tested']} skipped={d['symbols_skipped']}  |  conf>={d['conf_threshold']}")
print("="*118)

s=d["stats"]
print(f"\nOVERALL: trades={s['total_trades']}  WR={s['win_rate_pct']}%  PF={s['profit_factor']}  "
      f"net=Rs{s['net_pnl_inr']:,.0f}  Sharpe={s['sharpe_annual']}  maxDD={s['max_drawdown_pct']}% (Rs{s['max_drawdown_inr']:,.0f})")
print(f"         gross_profit=Rs{s['gross_profit_inr']:,.0f}  gross_loss=Rs{s['gross_loss_inr']:,.0f}  "
      f"avgWin=Rs{s['avg_win_inr']:,.0f}  avgLoss=Rs{s['avg_loss_inr']:,.0f}  expectancy=Rs{s['expectancy_per_trade']}/trade")

nifty={"2018":"+3.2","2019":"+12.0","2020":"+14.9","2021":"+24.1","2022":"+4.3",
       "2023":"+20.0","2024":"+8.8","2025":"neg(FII exodus)","2026":"-8.5 H1"}
print("\n== YEAR-BY-YEAR (strategy net vs Nifty calendar return) ==")
by_yr=defaultdict(list)
for t in trades: by_yr[(t.get("ts_exit") or "")[:4]].append(t)
for yr in sorted(k for k in by_yr if k.isdigit()):
    print(f"  {yr}  Nifty={nifty.get(yr,'?'):>16} |", end="")
    block("", by_yr[yr])

print("\n== STRATEGY ==")
by_s=defaultdict(list)
for t in trades: by_s[t.get("strategy","?")].append(t)
for k in sorted(by_s): block(k, by_s[k])

print("\n== ENTRY REGIME ==")
by_r=defaultdict(list)
for t in trades: by_r[t.get("regime","?")].append(t)
for k in sorted(by_r): block(k, by_r[k])

print("\n== EXIT REASON ==")
by_e=defaultdict(list)
for t in trades: by_e[t.get("close_reason","?")].append(t)
for k in sorted(by_e): block(k, by_e[k])

print("\n== CONFIDENCE BUCKET ==")
by_c=defaultdict(list)
for t in trades: by_c[(int(t.get("confidence",0))//10)*10].append(t)
for k in sorted(by_c): block(f"{k}-{k+9}", by_c[k])

print("\n== HOLDING PERIOD (calendar days) ==")
buck=[("0-3",0,3),("4-7",4,7),("8-14",8,14),("15-30",15,30),("31-60",31,60),("60+",61,99999)]
for nm,lo,hi in buck: block(nm, [t for t in trades if lo<=t["hold_days"]<=hi])
hd=[t["hold_days"] for t in trades]
if hd: print(f"  hold days: mean={st.mean(hd):.1f} median={st.median(hd)} max={max(hd)}")

print("\n== R-MULTIPLE DISTRIBUTION (realised / initial risk) ==")
rs=[t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
if rs:
    rbuck=[("<= -1R (full stop)",-99,-1.0),("-1..0R",-1.0,0),("0..1R",0,1.0),
           ("1..2R",1.0,2.0),("2..3R",2.0,3.0),(">3R",3.0,99)]
    for nm,lo,hi in rbuck:
        sub=[t for t in trades if t.get("r_multiple") is not None and lo< t["r_multiple"] <=hi]
        block(nm, sub)
    print(f"  avg R = {st.mean(rs):.3f}  median R = {st.median(rs):.3f}")

print("\n== MONTHLY (net Rs, all years pooled by calendar month) ==")
mo=defaultdict(float); moN=defaultdict(int)
for t in trades:
    m=(t.get("ts_exit") or "")[5:7]
    if m: mo[m]+=t["pnl"]; moN[m]+=1
for m in sorted(mo): print(f"  month {m}: net=Rs{mo[m]:>11,.0f}  ({moN[m]} trades)")

print("\n== TOP 10 WINNERS ==")
for t in sorted(trades,key=lambda x:-x["pnl"])[:10]:
    print(f"  +Rs{t['pnl']:>10,.0f}  {t['symbol']:<16} {t['ts']}->{t['ts_exit']} "
          f"{t['strategy']:<16} R={t.get('r_multiple')} {t['close_reason']}")
print("\n== TOP 10 LOSERS ==")
for t in sorted(trades,key=lambda x:x["pnl"])[:10]:
    print(f"  -Rs{abs(t['pnl']):>10,.0f}  {t['symbol']:<16} {t['ts']}->{t['ts_exit']} "
          f"{t['strategy']:<16} R={t.get('r_multiple')} {t['close_reason']}")

# Win/loss by exit reason cross-strategy already covered; streaks:
print("\n== STREAKS (chronological by exit date) ==")
chron=sorted(trades,key=lambda x:x.get("ts_exit",""))
cur=0; best=0; worst=0
for t in chron:
    if t["pnl"]>0: cur = cur+1 if cur>0 else 1
    else: cur = cur-1 if cur<0 else -1
    best=max(best,cur); worst=min(worst,cur)
print(f"  longest win streak={best}  longest loss streak={abs(worst)}")

# symbols that account for most of P&L
print("\n== P&L CONCENTRATION (top symbols) ==")
by_sym=defaultdict(float); by_symN=defaultdict(int)
for t in trades: by_sym[t["symbol"]]+=t["pnl"]; by_symN[t["symbol"]]+=1
tot=sum(by_sym.values())
top=sorted(by_sym.items(),key=lambda x:-x[1])[:10]
bot=sorted(by_sym.items(),key=lambda x:x[1])[:5]
for sym,pnl in top: print(f"  +Rs{pnl:>11,.0f}  {sym:<16} ({by_symN[sym]} trades)")
print("  ---worst---")
for sym,pnl in bot: print(f"  -Rs{abs(pnl):>11,.0f}  {sym:<16} ({by_symN[sym]} trades)")
print(f"\n  total net across all symbols = Rs{tot:,.0f}")
