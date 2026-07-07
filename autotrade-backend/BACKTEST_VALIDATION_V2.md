# AutoTrade Pro — Realism & Validation Backtest (V2)

**Audit Date:** 2026-07-07
**Objective:** Stress-test the PULLBACK_LONG edge against survivorship bias, realistic stop-loss slippage, and portfolio capital constraints. 

---

### Step 1: Survivorship Bias Fix
* **Method:** Re-ran the universe backtest enforcing point-in-time liquidity. Instead of relying on today's top 500, a dynamic check was added to `precompute()` guaranteeing a 30-day trailing turnover of ₹50M+ *on the day the trade was generated*.
* **Finding:** The original backtest generated 1,325 trades. With strict point-in-time liquidity enforced, the trade count fell to **1,207** (eliminating 118 trades that were taken on currently-large stocks when they were historically illiquid). 
* **Impact:** Surprisingly, the edge held up. Profit factor increased slightly to **1.34**, and win rate remained stable at **54.3%**. The bulk of the 2021-2023 profits came from genuinely liquid stocks at the time.

### Step 2: Realistic Fills (Stop-Loss Slippage)
* **Method:** Added a conservative gap-through slippage penalty to stop-outs. Stops no longer fill exactly at the stop price; they fill at the worse of the daily Open price (if a gap down occurred) or a flat 0.5% negative slippage penalty. Target fills were left untouched as they represent resting limit orders.
* **Finding:** The slippage ate into net profitability (reducing total net P&L from ~18.8L to ~17.5L), but the underlying distribution was robust enough to absorb the penalty.

### Step 3: Portfolio Constraints (15 Max Positions)
* **Method:** The unconstrained backtest took every valid signal, simulating unlimited capital. A new `portfolio_sim.py` chronologically walked through the 1,207 valid trades, restricting the portfolio to a maximum of 15 concurrent positions, selecting setups based on `confidence` scores when signals exceeded available cash slots.
* **Finding:** Trade count was constrained down to **1,066**. The exclusion of some highly profitable but lower-confidence trades shifted the metrics:
  * **Win Rate:** 53.19%
  * **Profit Factor:** **1.28**
  * **Sharpe Annual:** 1.98
  * **Max Drawdown:** -3.84% (₹-2,11,306 on ₹75L exposed capital)

### Step 4: Historical Harness for Untested Components
* **a. Hub 7-Factor Engine:** The `master_intelligence_scores` table only contains history back to **2026-06-12** (approx. 3 weeks of data, ~265,000 rows). It requires live external API pulls (news, macro) that cannot easily be backfilled. It will not be statistically valid to backtest until at least Q1 2027.
* **b. LLM Veto:** We have successfully toggled `AGENT_LLM_SHADOW_MODE=true`. It is now recording its reasoning in the DB natively, allowing us to review its accuracy without blocking real setups over the next 2-3 months.
* **c. Intraday MIS Burst:** Depends heavily on 1-minute order book and footprint data which the system does not retain historically. Shadow-mode forward testing in paper trading is the only viable route here.
* **d. F&O Spreads:** Constructing a robust historical F&O backtest is exceptionally difficult without expensive historical options chain data (implied volatility and Greeks). Given current DB capabilities, shadow-mode forward testing is the only option.

---

### Step 5: Final Verdict

Under the crucible of point-in-time selection, gap slippage, and a strict 15-position portfolio limit, **PULLBACK_LONG FAILS the original deployment gates.**

While it cleared the Sharpe, Drawdown, and Win-Rate hurdles easily, **the Profit Factor fell to 1.28**, failing the strict `>= 1.30` requirement you laid out. 

Furthermore, the **worst rolling 12-month period is 2019**, where the strategy lost **₹-1,56,884** (PF=0.54), followed closely by 2022 where it bled **₹-32,562**. To run this live, you would have to be psychologically and financially prepared to trade a system that might bleed capital for 12-18 straight months during hostile regimes, despite its long-term expectancy.

**Recommendation:** The strategy is exceptionally solid as a baseline, but transaction costs and slippage in a constrained portfolio bring it just under the threshold of what I would consider safe for real money today. Let the shadow mode run for a few months to collect LLM veto and Hub signal data, which could push the PF back over 1.30.
