# 2026-08-04 Report — Candle Data Silent-Loss Fix + Telegram Alert System

Ye report do parts mein hai:
1. **Aaj ka investigation aur fix** — candle data "live hai ya purana" wale sawaal se shuru hua, aur do bade silent bugs mile aur fix hue.
2. **Telegram messaging system** — hum kaunsa bot/channel use kar rahe hai, kaunse messages bhejte hai, aur unke templates.

---

## PART 1 — Candle Data Investigation aur Fix

### Shuruaat kaise hui

User ne pucha: **"check candles is it updated & live or old"**

Isme investigate karne pe pata chala ki ek bahut serious cheez ho rahi thi — system apne aap ko "sab theek hai" bol raha tha, lekin real mein data 25+ ghante se freeze tha. Ye do alag jagah pe same bimari (disease) nikli.

### Flow samjho (general pattern jo dono bugs mein mila)

```
Celery Beat (scheduler)
      │
      │  har 3 min / 5 min pe task trigger karta hai
      ▼
Task function (e.g. kite_live_candles_task)
      │
      │  Kite/yfinance se sab symbols ka data fetch karta hai
      ▼
save_candles_to_db()  ← 3000 rows ke chunks mein DB insert karta hai
      │
      │  agar 1 chunk fail ho gaya (timeout) →
      ▼
   ❌ PEHLE: poora rollback ho jata tha (SAB chunks discard),
             lekin "saved: X" ka JHOOTHA success log print hota tha
   ✅ AB:    sirf wahi 1 chunk lost hota hai, baaki committed rehta hai
```

### Bug #1 — `kite_live_candles_task` (sabse gambhir)

**Kya problem thi:**
- Ye task har 3 minute mein chalta hai, **2,569 symbols** (poora hub_universe) ke liye 1-minute candles Kite se fetch karke DB mein save karta hai.
- Is task ka koi apna time-limit set nahi tha, isliye system-wide default use ho raha tha: **soft limit 300 second, hard limit 600 second**.
- Lekin jab hub_universe expand hua (500 se 2,569 symbols), ek pura run **7 se 25+ minute** lene laga — apne hi time-limit se kahin zyada.
- Jab 300-second limit hit hoti, Celery ek exception (`SoftTimeLimitExceeded`) throw karta — jo `save_candles_to_db()` ke chunk-loop ke beech mein girta.
- Us chunk ka `except` block sirf `session.rollback()` karta tha — aur kyunki koi chunk pehle se COMMIT nahi hua tha (sab ek hi transaction mein tha), **poore run ka data wapas ud jata tha**.
- Fir bhi task ye log print karta: `{'saved': 245339, 'errors': 0}` — matlab dikhne mein sab safal, real mein **0 rows DB mein gaye**.

**Kitna bura tha (proof):**
- DB mein candles ka aakhri timestamp `2026-08-03 06:38:00 UTC` pe atka hua tha — **25+ ghante purana**, jabki task har 3 min "success" bol raha tha.
- Poore din mein 15 baar `SoftTimeLimitExceeded` errors mile.

**Fix (3 hisso mein):**

| # | Fix | Kya kiya |
|---|-----|----------|
| 1 | `save_candles_to_db()` mein per-chunk commit | Ab har 3000-row chunk apne aap turant commit hota hai. Agar aage koi chunk fail ho, sirf wahi chunk lost hota hai — pehle wale saare chunks DB mein surakshit rehte hai. |
| 2 | Task ka apna time-limit | `soft_time_limit=1200, time_limit=1260` (pehle 300/600 tha) — 2,569 symbols ke liye realistic headroom. |
| 3 | Redis overlap-guard | `SET NX EX` lock (`kite_live_candles:running`) — agar ek run abhi chal raha hai to agla scheduled tick usse **skip** karega, do runs ek saath (stack) nahi honge. |

**Fix ke baad verification (live proof):**
- 3 consecutive clean runs dekhe: `saved: 414673, errors: 0` → `saved: 10944` → `saved: 12535`, koi bhi `save_candles_to_db error` nahi.
- DB directly check kiya: top-50 turnover symbols (RELIANCE, HDFCBANK, TCS, INFY, SBIN...) sab ka fresh candle mila, current time ke ~10 minute ke andar.

### Bug #2 — `india_price_scan` (same bimari, dusri jagah)

Monitoring karte waqt yehi disease **ek aur task** mein mili — `india_price_scan` (yfinance se OHLCV candles + NIFTY/SENSEX/VIX fetch karta hai).

**Kya mila:**
- Iska bhi koi apna time-limit nahi tha (wahi 300s/600s default).
- Iska khud ka beat-schedule bhi **300 second (5 min)** hai — matlab soft-limit == scheduling-interval, ek seedha collision.
- Real runs 8 second se lekar **1793 second (~30 minute)** tak le rahe the — 25 mein se **19 cycles** apne 300s budget se zyada chal rahe the.
- Overlap bhi confirm hua: **2 alag Celery worker processes** (PID 3025992 aur 3025995) ek hi 10-minute window mein **dono ek saath** poora crawl chala rahe the — matlab 4-worker pool mein se aadha sirf duplicate kaam mein lag raha tha.

**Fix (same pattern jo kite_live_candles mein use hua):**
- `soft_time_limit=2400, time_limit=2460` (yfinance external/rate-limited hai isliye zyada headroom diya).
- Redis overlap-guard (`india_price_scan:running`), same `SET NX EX` pattern.

**Verification:** Fix ke baad pehla run clean complete hua — `symbols=1021, candles=990, errors=165` (ye errors normal fetch-level hai, timeout wale nahi), lock bhi sahi se release hua.

### Bug #3 — Bond-pollution filter complete kiya (side finding)

Ye pehle se ek chalta hua fix tha jo is session mein pehli baar sirf `-SG`/`-SK` suffix exclude kar raha tha (Zerodha ke gilt/SDL bonds symbol jaise `675KA33-SG`). Wo **incomplete** tha — `-NE`, `-NG`, `-NI` jaise 70+ dusre suffix bhi bonds the jo miss ho rahe the.

**Complete fix:** digit-prefix + koi bhi 2-letter suffix ka regex: `^[0-9].*-[A-Z0-9]{2}$` — 5,960+ bonds exclude karta hai, 9 real digit-prefix companies (3MINDIA, 5PAISA, 360ONE, etc.) ko chhota nahi.

Ye 5 jagah apply kiya: `zerodha_market.py` (root cause — NSE_TOKENS feed karta hai), `india_price_feed.py` aur `india_signal_generator.py` ke cold-start fallback, aur dono backfill scripts.

**Verification:** Live `/api/v1/india/watchlist` API check kiya — 2,972 symbols PRICE_CACHE mein, **zero bond-pattern match**.

### Summary table — aaj ke saare fixes

| Bug | Severity | Status | Commit |
|-----|----------|--------|--------|
| `kite_live_candles_task` silent data loss (25+ ghante) | 🔴 Critical | ✅ Fixed + verified live | `451807a` |
| Bond-pollution incomplete filter | 🟡 Medium | ✅ Fixed + verified live | `451807a` |
| `india_price_scan` timeout + overlap | 🟠 High | ✅ Fixed + verified live | (uncommitted — pending "commit karo") |

---

## PART 2 — Telegram Alert System

### Architecture (1 Bot → 1 Channel)

```
Trading Engine ke andar kahin bhi
(entry hua, exit hua, F&O trade hua, error aaya...)
            │
            │  integrations/telegram_service.py import karke
            │  send(text)  ya  fire(text)  call karte hai
            ▼
   telegram_service._post(text)
            │
            │  Telegram Bot API ko HTTPS POST
            │  (3 retry, 30s timeout, agar test mode ya
            │   DISABLE_TELEGRAM env set hai to suppress)
            ▼
  ┌─────────────────────────────────┐
  │  Bot: @Avishktradesignalbot      │
  │  (display name: "AvishkTrade    │
  │   Pro Signal Bot")               │
  └────────────────┬─────────────────┘
                    │  post karta hai
                    ▼
  ┌─────────────────────────────────┐
  │  Channel: "CIS Autotrade test    │
  │  bot"  (chat_id: -1003866010991) │
  └─────────────────────────────────┘
```

Yaani **sirf ek hi bot aur ek hi channel** hai — poore system mein jitne bhi Telegram alerts jaate hai, sab isi bot se, isi channel mein aate hai. "cis auto trade" jo aap keh rahe the wo channel ka naam hai, aur "autotrade bot" us Signal Bot ka hi generic reference hai — dono actually ek hi pipeline ka hissa hai, alag-alag bots nahi hai.

- Config: `.env` mein `TELEGRAM_BOT_TOKEN` aur `TELEGRAM_CHAT_ID=-1003866010991`
- Code: `integrations/telegram_service.py` — saara system isi ek file se Telegram bhejta hai (~28 alag jagah se call hota hai poore codebase mein)
- `send(text)` = awaited/guaranteed delivery; `fire(text)` = fire-and-forget (background me chala jata hai, trading loop block nahi hota)

### Kaunse messages bhejte hai — poori catalog

#### 1. Trade Entry Alert (`fmt_entry`) — sabse detailed message
Jab bhi ek naya trade khulta hai (technical-only path), poora 7-factor breakdown ke saath bhejte hai.

```
🧪 [PAPER TRADE] VIRTUAL EXECUTION 🧪
🟢 TRADE EXECUTED — BUY 🟢
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 RELIANCE  ·  Hub Score +68  ·  BULL_TRENDING
🐂 Confidence 82%  ·  Strategy: SWING

💰 Entry :  ₹2,845.50
🛑 SL    :  ₹2,790.00  (ATR ₹32.10)
🎯 T1    :  ₹2,920.00  (+2.6%)
🎯 T2    :  ₹2,980.00  (+4.7%)  R:R 2.4×
📦 Qty   :  35 shares

📊 7-Factor Breakdown  (total +68)

1️⃣ Technical  ██████████░░░░  +45
  RSI(58.2) → ⚪ NEUTRAL
  MACD → 🟢 Bullish cross  hist=+1.20
  EMA trend → 🟢 BULL (20:2830 / 50:2790 / 200:2650)
  Supertrend → 🟢 BULLISH

2️⃣ News/Sentiment  ████████░░░░  +30
  • Reliance Q1 profit beats estimates on retail growth
  
3️⃣ Sector  ██████░░░░░░  +20  Oil & Gas
...
7️⃣ Options/Flow  █████░░░░░░  +15
  Nifty OI bias: +2

⚠️ Paper mode — virtual money only
```

#### 2. Trade Exit Alert (`fmt_exit`)
Position close hone pe (SL hit, target hit, ya koi bhi reason se).

```
🧪 [PAPER TRADE] VIRTUAL EXECUTION 🧪
✅ TRADE CLOSED ✅
━━━━━━━━━━━━━━━━━━━━
📌 RELIANCE  ·  BUY

Entry :  ₹2,845.50
Exit  :  ₹2,920.00
Qty   :  35 shares

P&L:  ▲ ₹2,608  (+2.6%)
Reason: TARGET_HIT

⚠️ Paper mode — virtual money only
```

#### 3. Shortlist / Watchlist Alert (`fmt_shortlist_alert`)
Jab koi stock STRONG_BUY/BUY candidate ban jata hai (chahe trade execute ho ya sirf watchlist mein rahe) — isme candle patterns aur web-research bhi shamil hote hai.

```
🔥 STRONG BUY SIGNAL 🔥
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 TCS  ·  Score: +72.0  ·  Signal: STRONG_BUY
🐂 Regime: BULL_TRENDING

💰 Entry :  ₹3,845.00
🛑 Stop  :  ₹3,780.00  (−₹65  ·  1.7%)
🎯 T1    :  ₹3,930.00  (+2.2%)
...
📊 Latest Bar:  O:3820  H:3850  L:3810  C:3845  ▲0.5%
🕯 Patterns:  Bullish Engulfing (Bullish)

🌐 Web Research:
Company recently announced a large deal win...

📋 WATCHLIST ALERT — monitoring only
⚠️ Paper mode — virtual money only
```

#### 4. F&O Option Buy Alert
```
🎯 F&O OPTION BUY
NIFTY 24500 CE
Premium: ₹185  |  2 lot × 50 = 100 qty
Expiry: 28-Aug-2026 (12d)
SL ₹140  ·  TP ₹260  ·  Breakeven 24,685
Premium paid (max loss): ₹18,500
Conviction: 74%
```

#### 5. F&O Futures Alert
```
📈 F&O FUTURES BUY
BANKNIFTY FUT  ·  28-Aug-2026 (12d)
Entry: 52,340  |  1 lot × 15 = 15 qty
SL 51,900  ·  TP 53,200
Margin: ₹1,25,000  |  Notional: ₹7,85,100
Conviction: 68%
```

#### 6. Corporate Action Detected
```
🔀 Corporate Action Detected — TATASTEEL
Type: SPLIT
Ratio: 1 old share → 10.00 new shares
Price: ₹1450.00 → ₹145.00
Positions adjusted: 1

📰 News: Tata Steel board approves stock split...
```

#### 7. Breakout / Momentum Auto-Discovery
```
🔍 Breakout Auto-Discovery — New stocks added to agent universe:

• DIXON  +8.2%  vol 3.4×avg  RSI 71
  Breakout above 20-day high on volume surge
```

#### 8. Market Shock / Emergency Flatten
```
⚠️ MARKET SHOCK — SEVERE
India VIX spiked 18% intraday
FLATTEN RELIANCE @ ₹2,780.00 (pnl ₹-4,200)
Tightened stops on 3 long(s)
```

#### 9. High-Impact News Alert
```
🔴 HIGH-IMPACT MARKET NEWS (3)
• RBI unexpectedly hikes repo rate by 50bps  [Reuters]
• US Fed signals more rate hikes ahead  [Bloomberg]
…+1 more
```

#### 10. Intraday MIS Entry Summary
```
🌅 Intraday MIS Entry — 04 Aug 09:20 IST
Placed: 3 trade(s)  |  Vetoed: 2

• AXISBANK  score=+55  ...
```

#### 11. Intraday Squareoff Complete
```
📊 Intraday Squareoff Complete
Closed: 3 MIS position(s)
Total P&L: ₹+4,850
Detail: AXISBANK +2100 · SBIN +1200 · LT +1550
```

#### 12. Weekly Portfolio Rebalance
```
⚖️ Weekly Portfolio Rebalance — 2026-08-03

🟢 BUY DIXON: increase allocation to 8%
🔴 SELL WIPRO: reduce allocation to 2%
```

#### 13. Weekly AI Portfolio Report
```
📈 Weekly Portfolio Report — 2026-08-03

[AI-generated performance summary: returns, top movers,
risk metrics, aur agle hafte ka outlook]
```

#### 14. System Health / Error Alerts (operations team ke liye)
Ye "trading" nahi, **system health** alerts hai — jab koi background pipeline break hoti hai:

- **FII/DII data stale**: `⚠️ FII/DII data stale — latest available is ..., today is ...`
- **Zerodha token expired/invalid**: error message + re-login instructions
- **Upstox token refresh failed** (3 attempts ke baad): `⚠️ Upstox token refresh failed...`
- **Candle staleness watchdog**: agar candles bahut purane ho jaye to alert
- Ye sab **throttled** hai (e.g. FII/DII alert sirf 1 ghante mein 1 baar) taaki spam na ho

### Kaunsa message kab jaata hai (trigger map)

| Trigger (kya hua) | Message Type | File |
|---|---|---|
| Naya trade khula (technical) | Trade Entry | `agent_loop.py`, `india_tasks.py` |
| Hub-override se trade khula | "TRADE PLACED" (short) | `agent_loop.py` |
| Position close hui (SL/TP/manual) | Trade Exit | `execution.py`, `india_tasks.py` |
| Stock BUY/STRONG_BUY ban gaya | Shortlist Alert | `agent_loop.py`, `india_tasks.py` |
| F&O option/futures trade | F&O Alert | `engine/fno/*.py` |
| Split/bonus/dividend detect hua | Corporate Action | `corporate_actions.py` |
| Naya breakout/momentum stock mila | Discovery Alert | `breakout_screener.py`, `momentum_screener.py` |
| VIX spike / crash detect hua | Market Shock | `india_tasks.py` |
| High-impact news headline | News Alert | `india_tasks.py` |
| Din ke end mein MIS square-off | Squareoff Summary | `india_tasks.py` |
| Har Sunday rebalance check | Weekly Rebalance | `india_tasks.py` |
| Har Sunday AI report | Weekly Report | `india_tasks.py` |
| Background pipeline fail | Health/Error Alert | multiple |

---

*Report generated: 2026-08-04, AutoTrade Pro session*
