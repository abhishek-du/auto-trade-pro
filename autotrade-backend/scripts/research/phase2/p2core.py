"""Shared Phase 2 machinery. 1m candles only, per the accepted limitation."""
import bisect, random, statistics as st
from collections import defaultdict

random.seed(20260825)

# The project's own model (paper_trading/trade_simulator.py::estimate_trade_cost)
# at a Rs50k notional, plus its adverse-slippage band midpoint.
COST_STATUTORY = 0.2942
COST_SLIPPAGE  = 0.100
COST_RT        = COST_STATUTORY + COST_SLIPPAGE      # 0.394%

class Session:
    """1m closes for one trading date, with O(log n) as-of lookup."""
    __slots__ = ("bars", "times", "tv")
    def __init__(self):
        self.bars = defaultdict(list)     # sym -> [(ts, close, vol)]
        self.times = {}
        self.tv = {}
    def finalise(self):
        for s, v in self.bars.items():
            v.sort()
            self.times[s] = [b[0] for b in v]
            self.tv[s] = sum(b[1] * b[2] for b in v)
    def idx(self, sym, ts, max_stale_s=300):
        """Index of the last bar at or before ts. None if absent or stale."""
        t = self.times.get(sym)
        if not t:
            return None
        i = bisect.bisect_right(t, ts) - 1
        if i < 0:
            return None
        if (ts - t[i]).total_seconds() > max_stale_s:
            return None
        return i
    def fwd(self, sym, ts, mins, is_long):
        """Forward % return in the position's own direction. None if unusable."""
        i = self.idx(sym, ts)
        if i is None:
            return None
        b = self.bars[sym]
        if i + 1 >= len(b):
            return None
        ep = b[i][1]
        if ep <= 0:
            return None
        j = min(i + mins, len(b) - 1) if mins is not None else len(b) - 1
        if j <= i:
            return None
        px = b[j][1]
        return ((px - ep) if is_long else (ep - px)) / ep * 100
    def excursions(self, sym, ts, mins, is_long):
        """(MFE, MAE, t_to_mfe, t_to_mae) in % and minutes."""
        i = self.idx(sym, ts)
        if i is None:
            return (None,) * 4
        b = self.bars[sym]
        j = min(i + mins, len(b) - 1) if mins is not None else len(b) - 1
        if j <= i:
            return (None,) * 4
        ep = b[i][1]
        if ep <= 0:
            return (None,) * 4
        mfe = mae = 0.0
        tf = tm = 0
        for k in range(i + 1, j + 1):
            r = ((b[k][1] - ep) if is_long else (ep - b[k][1])) / ep * 100
            if r > mfe: mfe, tf = r, k - i
            if r < mae: mae, tm = r, k - i
        return mfe, mae, tf, tm

def boot_cluster(groups, n=2000, seed=None):
    """Bootstrap over CLUSTERS (symbols), not observations. Part 17.

    Resamples symbols with replacement and pools their observations, so 1,000
    signals on one stock count as one bet, not a thousand. Implemented on
    per-cluster (sum, count) pairs, which makes each resample O(#clusters)
    instead of O(#observations) — the identical estimator, just not recomputed
    from the raw values every draw.
    """
    ks = list(groups)
    if len(ks) < 6:
        return (None, None)
    sums = [0.0] * len(ks)
    cnts = [0] * len(ks)
    for i, k in enumerate(ks):
        v = groups[k]
        sums[i] = sum(v)
        cnts[i] = len(v)
    rng = random.Random(seed if seed is not None else 20260825)
    m = len(ks)
    rr = rng.randrange
    out = []
    for _ in range(n):
        ts = tc = 0.0
        for _ in range(m):
            j = rr(m)
            ts += sums[j]
            tc += cnts[j]
        if tc:
            out.append(ts / tc)
    if not out:
        return (None, None)
    out.sort()
    return (out[int(.025 * len(out))], out[int(.975 * len(out))])

def clustered(pairs):
    """[(symbol, value)] -> {symbol: [values]}"""
    g = defaultdict(list)
    for s, v in pairs:
        g[s].append(v)
    return g

def describe(pairs, label="", cost=COST_RT):
    """One clustered summary line."""
    if not pairs:
        return None
    vals = [v for _, v in pairs]
    g = clustered(pairs)
    lo, hi = boot_cluster(g)
    return dict(
        label=label, n=len(vals), symbols=len(g),
        mean=st.mean(vals), median=st.median(vals),
        win=100 * sum(1 for v in vals if v > 0) / len(vals),
        lo=lo, hi=hi, net=st.mean(vals) - cost,
    )

def fmt(d):
    if d is None:
        return "  (no data)"
    ci = f"[{d['lo']:+.3f}, {d['hi']:+.3f}]" if d['lo'] is not None else "[insufficient clusters]"
    return (f"  {d['label']:<26} n={d['n']:>6} sym={d['symbols']:>4}  "
            f"gross {d['mean']:+7.3f}%  net {d['net']:+7.3f}%  "
            f"med {d['median']:+7.3f}%  win {d['win']:5.1f}%  {ci}")
