"""Parts 2-8: tactical signals. 4 sessions is the entire available sample.

Every feature is computed from bars at or before the signal timestamp.
Controls are drawn the same way. Nothing reads a future bar except the
forward-return measurement itself.
"""
import asyncio, json, sys, datetime as dt, random
from collections import defaultdict
sys.path.insert(0,"/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad")
from p2core import Session
from sqlalchemy import text
from db.database import AsyncSessionLocal
random.seed(20260825)

HOR = [1, 3, 5, 10, 15, 30, 60, 120, None]
SESSIONS = ["2026-08-20","2026-08-21","2026-08-24","2026-08-25"]
OUT = "/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad/tac_obs.jsonl"

def pre_features(sess, sym, ts):
    """Pre-signal state only — index at ts, look backwards."""
    i = sess.idx(sym, ts)
    if i is None: return None
    b = sess.bars[sym]
    px = b[i][1]
    if px <= 0: return None
    def ret(n):
        j = i - n
        return (px - b[j][1]) / b[j][1] * 100 if j >= 0 and b[j][1] > 0 else None
    win = b[max(0, i - 14):i + 1]
    rets = [(win[k][1] - win[k-1][1]) / win[k-1][1] for k in range(1, len(win)) if win[k-1][1] > 0]
    import statistics as _st
    vol15 = _st.pstdev(rets) * 100 if len(rets) >= 5 else None
    v_recent = sum(x[2] for x in b[max(0, i - 4):i + 1]) / min(5, i + 1)
    v_base = sum(x[2] for x in b[max(0, i - 29):i + 1]) / min(30, i + 1)
    surge = (v_recent / v_base) if v_base > 0 else None
    # session VWAP to this bar
    num = sum(x[1] * x[2] for x in b[:i + 1]); den = sum(x[2] for x in b[:i + 1])
    vwap = num / den if den > 0 else None
    hi = max(x[1] for x in b[:i + 1]); lo = min(x[1] for x in b[:i + 1])
    rng_pct = (px - lo) / (hi - lo) * 100 if hi > lo else None
    tv_sofar = sum(x[1] * x[2] for x in b[:i + 1])
    return dict(i=i, px=px, r1=ret(1), r3=ret(3), r5=ret(5), r15=ret(15), r30=ret(30),
                vol15=vol15, surge=surge,
                vwap_dist=((px - vwap) / vwap * 100) if vwap else None,
                rng_pct=rng_pct, tv=tv_sofar,
                tod=(ts.hour * 60 + ts.minute))

async def main():
    fh = open(OUT, "w"); total = 0
    for ds in SESSIONS:
        d = dt.date.fromisoformat(ds)
        async with AsyncSessionLocal() as db:
            sigs = (await db.execute(text("""
                SELECT id, symbol, strategy, signal_type, entry_price, stop_loss, target,
                       created_at, composite_score, sub_pipeline, routing_outcome, reason
                FROM tactical_signals WHERE created_at::date=:d ORDER BY created_at"""),
                {"d": d})).fetchall()
            pool = [r[0] for r in (await db.execute(text("""
                SELECT symbol FROM candles WHERE timeframe='1m' AND timestamp::date=:d
                GROUP BY symbol HAVING COUNT(*) > 250 AND SUM(volume*close) > 2e8"""),
                {"d": d})).fetchall()]
        need = sorted(set(pool) | {r[1] for r in sigs})
        sess = Session()
        async with AsyncSessionLocal() as db:
            for i in range(0, len(need), 300):
                rows = (await db.execute(text("""
                    SELECT symbol, timestamp, close, volume FROM candles
                    WHERE timeframe='1m' AND timestamp::date=:d AND symbol = ANY(:syms)
                    ORDER BY symbol, timestamp"""), {"d": d, "syms": need[i:i+300]})).fetchall()
                for sym, ts, cl, vol in rows:
                    sess.bars[sym].append((ts, float(cl), float(vol or 0)))
        sess.finalise()
        # pre-compute control-pool state per timestamp lazily
        kept = 0
        for (sid, sym, strat, side, ep, sl, tgt, ts, cs, sp, ro, reason) in sigs:
            is_long = (side or "BUY").upper() == "BUY"
            pf = pre_features(sess, sym, ts)
            if pf is None: continue
            f = {str(h): sess.fwd(sym, ts, h, is_long) for h in HOR}
            if f["5"] is None and f["None"] is None: continue
            mfe, mae, tf, tm = sess.excursions(sym, ts, 60, is_long)
            # ── controls, chosen from pre-signal state only ──────────────────
            cands = []
            for c in pool:
                if c == sym: continue
                cf = pre_features(sess, c, ts)
                if cf and cf["vol15"] is not None and cf["r15"] is not None and cf["tv"] > 0:
                    cands.append((c, cf))
            ctl = {}
            if len(cands) >= 20:
                mine = pf
                def pick(pred, n=5):
                    g = [x for x in cands if pred(x[1])]
                    if len(g) < 3: return None
                    random.shuffle(g); return g[:n]
                sel = {
                  "MKT":  cands,                                     # A: whole liquid market
                  "MATCH": pick(lambda z: mine["r15"] is not None
                               and abs(z["r15"] - mine["r15"]) <= 0.15
                               and abs(z["vol15"] - mine["vol15"]) <= 0.20 * max(mine["vol15"], .02)
                               and 0.5 <= z["tv"] / max(mine["tv"], 1) <= 2.0),
                }
                for k, g in sel.items():
                    if not g: ctl[k] = None; continue
                    use = g if k != "MKT" else random.sample(g, min(40, len(g)))
                    vals = {}
                    for h in HOR:
                        xs = [sess.fwd(x[0], ts, h, is_long) for x in use]
                        xs = [v for v in xs if v is not None]
                        vals[str(h)] = (sum(xs) / len(xs)) if xs else None
                    ctl[k] = dict(n=len(use), f=vals,
                                  r15=sum(x[1]["r15"] for x in use)/len(use),
                                  vol15=sum(x[1]["vol15"] for x in use)/len(use),
                                  tv=sum(x[1]["tv"] for x in use)/len(use))
            # C: random timestamp, SAME symbol + session + time-of-day bucket
            bucket_lo = max(0, pf["i"] - 60); bucket_hi = min(len(sess.bars[sym]) - 2, pf["i"] + 60)
            rt = {}
            if bucket_hi > bucket_lo + 5:
                j = random.randint(bucket_lo, bucket_hi)
                jts = sess.bars[sym][j][0]
                rt = {str(h): sess.fwd(sym, jts, h, is_long) for h in HOR}
            fh.write(json.dumps(dict(
                d=ds, sid=str(sid), sym=sym, strat=strat, side=side, is_long=is_long,
                ts=str(ts), ep=float(ep), sl=float(sl) if sl else None,
                tgt=float(tgt) if tgt else None, cs=cs, sp=sp, ro=ro,
                blocked=("Cash buffer" in (reason or "")),
                f=f, mfe=mfe, mae=mae, t_mfe=tf, t_mae=tm,
                pre={k: v for k, v in pf.items() if k != "i"},
                ctl=ctl, rt=rt)) + "\n")
            kept += 1
        total += kept
        print(f"  {ds}: {len(sigs):>5} signals -> {kept:>5} usable  "
              f"(pool {len(pool)} liquid symbols)", flush=True)
        del sess
    fh.close(); print(f"\nTOTAL: {total}", flush=True)
asyncio.run(main())
