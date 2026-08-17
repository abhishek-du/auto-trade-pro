# AutoTrade Pro — Loss Investigation Report (3–17 August 2026)

*Har finding neeche DB query, code line, ya web-search se verify kiya gaya hai — sirf assumption nahi.*

---

## Overall Number (proof: `paper_trades` table + `virtual_wallet`)

**Window (3–17 Aug, closed_at basis):** 203 trades closed, 80 wins / 123 losses → **win rate 39.4%**, net PnL **+₹1,791** (लगभग breakeven, profit nahi ke barabar).

**Since 21 July pivot (poore account ka history):** 254 trades, 92 wins (36.2% win rate), realised PnL **−₹10,997**, unrealised (open positions) +₹3,861, equity ₹19,92,864 (start था ₹20,00,000) → **net ~−₹7,136, max drawdown 2.46%**.

Yani account "crash" nahi hua — flat/slightly negative hai. Lekin **din-wise pattern bahut uneven hai**, aur last 3 trading days mein bahut bura hua:

| Date | Trades | PnL |
|---|---|---|
| 08-03 | 18 | +6,623 |
| 08-04 | 18 | +1,233 |
| 08-05 | 32 | **+15,995** |
| 08-06 | 33 | +1,922 |
| 08-07 | 22 | −4,867 |
| 08-10 | 29 | −3,908 |
| 08-11 | 16 | +9,568 |
| 08-12 | 20 | +1,656 |
| 08-13 | 3 | −5,532 |
| 08-14 | 6 | −2,889 |
| **08-17 (उस दिन)** | **6** | **−18,010** |

3-12 August tak system profitable tha (+27,089 total). **13-17 August mein poora edge udh gaya (−26,431)** — sirf 15 trades mein. Ye "market crash" nahi hai (Nifty is poore period mein sirf −1.7% gira, 24774 → 24348 — [proof: apni candles table se pulled]), balki ek specific structural pattern hai jo neeche detail mein hai.

---

## Root Cause #1 — 97% portfolio ek hi strategy pe concentrated hai, aur uska confidence score **noise** hai

`strategy_name` se group karke dekha:

| Strategy | Trades | Win rate | PnL |
|---|---|---|---|
| **PRE_EVENT_EXPECTATION_GAP** | **197 (97%)** | 39.6% | +3,506 |
| NEWS_DIRECT | 3 | 33.3% | −997 |
| DIRECT_NEWS | 3 | 33.3% | −719 |

Ye strategy quarterly results se PEHLE stock buy karta hai, LLM ke "nowcast" (results POSITIVE aayenge ya NEGATIVE, ye guess) ke basis pe. Code check kiya (`engine/pre_event_expectation_gap/scoring.py:61`):

```python
def _nowcast_subscore(nc):
    return _clamp01(0.5 + 0.5 * dir_val * nc.confidence)
```

Isse pura score card nikala aur raw model confidence vs actual score-mein-count-hua value compare kiya:

| Symbol | Model ka apna raw confidence | Score mein count hua | Points diye |
|---|---|---|---|
| CMRGREEN | **6%** | 0.528 | 13.2 / 25 |
| MSTCLTD, WABAG, KIRLOSENG, HCC, FINPIPE... | **~10%** | 0.548 | 13.7 / 25 |
| IKIO, SAKSOFT | 24% | 0.620 | 15.5 / 25 |

**Formula ka floor 0.5 hai** — matlab model chahe 6% confidence bole (yaani "mujhe pata hi nahi"), phir bhi usse minimum ~13 points (52%) mil jaate hai us factor ke max 25 mein se. Isi 25%-weight factor ki wajah se 60-74 ka buy-threshold cross ho jaata hai, chahe underlying prediction ek coin-flip se better na ho.

**Proof ki ye scoring wakai meaningless hai** — confidence_bucket (jo isi score se banta hai) ke against actual outcome dekha:

| Confidence bucket | Trades | Win rate | PnL |
|---|---|---|---|
| 60 (sabse kam) | 28 | **46.4%** | −7,054 |
| 70 (middle) | 155 | 38.7% | **+12,110** |
| 80 (sabse zyada) | 14 | **35.7%** | −1,549 |

**Highest-confidence bucket ka win rate sabse KHARAB hai.** Confidence score ka outcome se koi positive correlation nahi — agar kuch hai to ulta hai. Ye clearly bata raha hai ki score ek fake conviction number bana raha hai, real edge nahi.

---

## Root Cause #2 — 17 Aug ka −₹18,010: 3-10 din tak position hold karke, event ke baad bhi der se exit

Us din ke 6 trades sab **PRE_EVENT_EXPECTATION_GAP** the, sab BUY, aur sab **73 se 240 ghante (3-10 din) hold** kiye gaye — matlab results date nikal chuki thi, phir bhi position khuli rahi:

| Symbol | Entry | Event date | Exit | Hold | PnL% |
|---|---|---|---|---|---|
| GENESYS | 08-13 | 08-14 | 08-17 | 99h | **−17.76%** |
| MCLOUD | 08-13 | 08-14 | 08-17 | 99h | −7.85% |
| CPPLUS | 08-07 | 08-12 | 08-17 | **240h** | −8.80% |
| LLOYDSENT | 08-07 | 08-11 | 08-17 | 240h | −8.97% |
| GODREJIND | 08-07 | 08-13 | 08-17 | 240h | −7.15% |
| SHREEJISPG | 08-12 | 08-14 | 08-17 | 123h | −6.61% |

Code mein ek "post-event tightening" fix pehle se tha (`0a69edb` commit, "found 2026-08-06, EPACKPEB.NS" incident se) — jo event date nikalne ke baad, agar position 3%+ adverse ho jaaye, stop-loss ko tighten karta hai. Lekin **ye sirf EK BAAR tighten karta hai jab pehli baar -3% cross ho** — us waqt tak price already bahut door ja chuka ho sakta hai. Poore window mein **9 trades** aisi mili jinka final exit price unke apne recorded stop-loss se bhi neeche tha (GENESYS ka mae_pct -19.6% tak gaya, exit -17.76% pe hua — matlab designed risk se kahi zyada loss ho gaya before the tightened stop finally caught it).

### Web se verify kiya — real earnings outcomes ne is theory ko confirm kiya:

- **GENESYS International**: Humare system ne "nowcast POSITIVE" bola. Web search confirm karta hai results genuinely achhe the (revenue +26%, EBITDA +41%, PAT +32% YoY) — **lekin stock phir bhi 14 Aug ko 8.4% gir gaya**, kyunki brokerage MarketsMojo ne isse pehle hi (4 Aug ko, entry se pehle) "Strong Sell" downgrade kar diya tha — valuation bahut expensive thi (P/E 37.68). "Sell the news" — achhe results ke bawajood stock gira kyunki valuation already priced-in / overextended thi. Humara system sirf "result achha aayega ya bura" predict karta hai, **valuation-stretch ya existing analyst sentiment ko weigh hi nahi karta** — ye ek genuine strategy blind-spot hai. — [Source](https://univest.in/blogs/why-genesys-international-corporation-ltd-share-price-fall-2026-08-14), [Source](https://www.marketsmojo.com/news/stock-recommendation/genesys-international-corporation-ltd-downgraded-to-strong-sell-amid-valuation-and-financial-concerns-4137266)
- **Godrej Industries**: Q1 FY27 mein revenue +19% (achha) lekin **net profit 19% GIRA** (₹349cr → ₹284cr YoY) — margin compression. Humara nowcast POSITIVE bola tha 10% confidence ke saath — actual result mixed/negative nikla, exactly wahi jo ek 10%-confidence guess se expect hoga. — [Source](https://www.investywise.com/godrej-industries-board-approves-financial-results-leadership-changes/)
- **Broad market (17 Aug)**: Sensex/Nifty red khula, geopolitical tension (US-Iran, Strait of Hormuz), crude oil $88-89/barrel high. Lekin index-level move sirf −0.3% se −0.44% tha — humara −₹18,010 sirf 6 trades se hai, market-wide crash se nahi. — [Source](https://www.indiatvnews.com/business/markets/17-august-2026-stock-market-updates-sensex-nifty-open-in-red-amid-persistent-geopolitical-tensions-2026-08-17-1051581)

---

## Root Cause #3 — 100% BUY-only, no hedge, correlated bets

Poore window ke 197 PRE_EVENT trades mein se **sab ke sab BUY (long) hai — ek bhi SELL/short nahi**. Sab same thesis pe (upcoming quarterly results, positive nowcast). Jab bhi results season overall mixed/negative surprises deta hai (jaisa is baar hua — GENESYS, GODREJIND, dono), **poora basket ek saath correlated loss leta hai**, kyunki koi diversification/hedge nahi hai different-direction bets ka.

---

## Exit-reason se breakdown (kaha se profit, kaha se loss)

| Exit Reason | Trades | Total PnL |
|---|---|---|
| STOP_LOSS | 109 | **−29,945** |
| POST_EVENT_REVERSAL | 9 | **−14,360** |
| SECTOR_REVERSAL | 61 | −5,690 |
| T1_REVERSAL_EXIT | 14 | +19,044 |
| TAKE_PROFIT | 10 | **+32,741** |

Pattern clear hai: **sirf 24 trades (12%) ne +51,785 profit banaya** (bade winners), jabki **179 trades (88%) ne −49,994 nuksaan diya** (chhote-chhote losses jo dhire dhire jama hote hai). Ye ek "low win-rate, high payoff-ratio" system hai — tab hi kaam karta hai jab winners consistently losers se bade rahein. Us hafte wo ratio toot gaya kyunki bade losers (GENESYS −17.8%, JNKINDIA/KRISHNADEF/CONFIPET/VIDYAWIRES sab −7% se −10.75%) aa gaye jo normal stop-loss se bhi bade the.

---

## Fix Applied (17 August 2026, same session)

Investigation ke turant baad root cause fix kiya gaya aur `main` branch mein merge/push ho chuka hai (commit `6ca53cc`, merged as `f31ac8f`):

1. **`engine/pre_event_expectation_gap/decision.py`** — naya hard gate `MIN_NOWCAST_CONFIDENCE = 0.15` add kiya, existing `MIN_EVENT_CONFIDENCE` gate ke pattern pe. Pehle event ki *timing* confidence gate hoti thi, lekin nowcast ki apni *direction-call* confidence kabhi gate nahi hoti thi. 0.15 threshold har sector adapter ke achievable range se neeche hai (sabse kam ceiling banking ka 0.20 hai), isliye sirf thin-data tail exclude hoti hai, poori strategy disable nahi hoti.

2. **`engine/pre_event_expectation_gap/scoring.py`** — `_nowcast_subscore` ab confidence ko square karta hai apply karne se pehle (0.10 → subscore 0.505 instead of 0.55; 0.40 → 0.58 instead of 0.70), taaki weak-but-passing confidence ko near-full neutral+ credit na mile.

**Verification:**
- 55 existing tests (`test_pre_event_gap_phase4.py`, `test_pre_event_gap_foundation.py`, `test_pre_event_reversal_exit.py`) sab pass hue, koi fixture 0.15 se kam confidence use nahi karta tha.
- Investigation ke actual loss-cluster confidences (0.06–0.11: CMRGREEN, SANDUMA, ZYDUSLIFE, MSTCLTD, WABAG, KIRLOSENG, HCC, FINPIPE, GODREJIND, LLOYDSENT, CPPLUS, SHREEJISPG, GREAVESCOT) replay karke check kiya — **sab naye gate se BLOCKED hote**. Higher-conviction reads (0.24, 0.40) still pass karte hai.
- Backend (uvicorn + celery-worker + celery-beat) restart karke live confirm kiya gaya — fix production mein chal raha hai.

**Abhi tak open hai (out of scope, is fix ka part nahi):**
- Post-event tightening abhi bhi "ek-baar-tighten" hai, na ki continuous — GENESYS-type overnight-gap risk se poora protect nahi karta.
- Confidence bucket 80 (highest) ka reverse-correlation abhi bhi investigate nahi hua — kyu highest-confidence trades sabse zyada lose kar rahe the.
- Valuation/analyst-sentiment context entry gate mein add nahi hua — GENESYS jaisa case (achhe results, phir bhi crash due to rich valuation) isse avoid nahi hoga.
- 100% BUY-only, no hedge — koi diversification/short-side change nahi kiya gaya.

---

*Report generated: 2026-08-17, AutoTrade Pro session.*
