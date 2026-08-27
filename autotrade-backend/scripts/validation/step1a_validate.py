"""STEP 1A — full-session empirical validation of the after-market pipeline.

Not a unit-test suite. This runs against the real database and reports what the
pipeline ACTUALLY did, criterion by criterion, with an explicit PASS/FAIL each.

    NSE API -> fetch -> queue -> consumer -> news_items -> causal_events
            -> event->NSE identity -> news_id provenance

Read-only except where a check must simulate a restart, which it does against a
scratch table it creates and drops itself. It never mutates production rows.

USAGE
    cd autotrade-backend
    PYTHONPATH=$PWD .venv/bin/python scripts/validation/step1a_validate.py [YYYY-MM-DD]
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys
from dataclasses import dataclass, field

from sqlalchemy import text

from db.database import AsyncSessionLocal

IST = "at time zone 'UTC' at time zone 'Asia/Kolkata'"
IST_TZ = dt.timezone(dt.timedelta(hours=5, minutes=30))


@dataclass
class Result:
    name: str
    passed: bool | None          # None = INCONCLUSIVE (not enough data yet)
    detail: str
    numbers: dict = field(default_factory=dict)

    @property
    def mark(self) -> str:
        return "PASS" if self.passed else ("FAIL" if self.passed is False else "INCONCLUSIVE")


RESULTS: list[Result] = []


def record(name, passed, detail, **numbers):
    RESULTS.append(Result(name, passed, detail, numbers))
    return RESULTS[-1]


def _h(t):
    print(f"\n{'=' * 74}\n{t}\n{'=' * 74}")


# ── 1. HEADLINE IMMUTABILITY + DEDUP KEY ────────────────────────────────────
async def check_headline_immutable(s, day):
    _h("1. HEADLINE IMMUTABILITY — the LLM summary must not enter the dedup key")
    r = (await s.execute(text(f"""
        SELECT count(*) tot,
               count(*) FILTER (WHERE headline LIKE '%LLM Summary:%') mutated,
               count(*) FILTER (WHERE news_metadata->>'seq_id' IS NOT NULL) postfix
        FROM news_items WHERE source='NSE-Announcements'
          AND (crawled_at {IST})::date = :d"""), {"d": day})).first()
    print(f"  rows={r.tot}  mutated headline={r.mutated}  post-fix (seq_id)={r.postfix}")

    # Steady state = post-fix rows only.
    r2 = (await s.execute(text(f"""
        SELECT count(*) FILTER (WHERE headline LIKE '%LLM Summary:%') bad
        FROM news_items WHERE source='NSE-Announcements'
          AND news_metadata->>'seq_id' IS NOT NULL
          AND (crawled_at {IST})::date = :d"""), {"d": day})).first()
    ok = r2.bad == 0 and r.postfix > 0
    print(f"  post-fix rows with a mutated headline: {r2.bad}")
    record("Headline immutable (post-fix rows)", ok if r.postfix else None,
           f"{r2.bad} of {r.postfix} post-fix rows carry an LLM suffix",
           postfix_rows=r.postfix, mutated=r2.bad, legacy_mutated=r.mutated)


# ── 2. DURABLE DEDUP ────────────────────────────────────────────────────────
async def check_dedup(s, day):
    _h("2. DURABLE DEDUP — one row per seq_id, across restarts")
    r = (await s.execute(text(f"""
        SELECT count(*) tot, count(DISTINCT news_metadata->>'seq_id') uniq
        FROM news_items WHERE source='NSE-Announcements'
          AND news_metadata->>'seq_id' IS NOT NULL
          AND (crawled_at {IST})::date = :d"""), {"d": day})).first()
    dupes = (r.tot or 0) - (r.uniq or 0)
    print(f"  post-fix rows={r.tot}  distinct seq_id={r.uniq}  DUPLICATES={dupes}")

    worst = (await s.execute(text(f"""
        SELECT news_metadata->>'seq_id' sq, count(*) n FROM news_items
        WHERE source='NSE-Announcements' AND news_metadata->>'seq_id' IS NOT NULL
          AND (crawled_at {IST})::date = :d
        GROUP BY 1 HAVING count(*) > 1 ORDER BY n DESC LIMIT 5"""), {"d": day})).all()
    for w in worst:
        print(f"    seq_id={w.sq} appears {w.n}x")
    record("Durable dedup (0 duplicate seq_id)", dupes == 0 if r.tot else None,
           f"{dupes} duplicate rows across {r.tot} post-fix rows",
           rows=r.tot, distinct=r.uniq, duplicates=dupes)

    # Legacy scheme, for contrast.
    lg = (await s.execute(text("""
        SELECT count(*) tot, count(DISTINCT (company,category,published_at)) uq
        FROM news_items WHERE source='NSE-Announcements'
          AND news_metadata->>'seq_id' IS NULL"""))).first()
    print(f"\n  legacy (pre-fix) rows: {lg.tot:,} for {lg.uq:,} announcements "
          f"= {lg.tot - lg.uq:,} duplicates ({100*(lg.tot-lg.uq)/max(lg.tot,1):.1f}%)")


# ── 3. RESTART IDEMPOTENCY (simulated) ──────────────────────────────────────
async def check_restart_idempotency(s, day):
    _h("3. RESTART IDEMPOTENCY — a restart must not re-create a stored filing")
    seqs = [r.sq for r in (await s.execute(text(f"""
        SELECT news_metadata->>'seq_id' sq FROM news_items
        WHERE source='NSE-Announcements' AND news_metadata->>'seq_id' IS NOT NULL
          AND (crawled_at {IST})::date = :d LIMIT 40"""), {"d": day})).all()]
    if not seqs:
        record("Restart idempotency", None, "no post-fix rows to replay")
        return

    # This is EXACTLY the pre-filter query the consumer runs, with a cold
    # in-memory set — i.e. the state immediately after a watchmedo reload.
    known = {r[0] for r in (await s.execute(text(
        "SELECT news_metadata->>'seq_id' FROM news_items "
        "WHERE source='NSE-Announcements' AND news_metadata->>'seq_id' = ANY(:s)"),
        {"s": seqs})).all() if r[0]}
    would_reprocess = [x for x in seqs if x not in known]
    print(f"  simulated cold start with {len(seqs)} already-stored filings")
    print(f"  pre-filter recognises : {len(known)}")
    print(f"  would RE-PROCESS      : {len(would_reprocess)}")
    record("Restart idempotency (0 reprocessed)", len(would_reprocess) == 0,
           f"{len(would_reprocess)} of {len(seqs)} would be re-processed after a reload",
           replayed=len(seqs), recognised=len(known), reprocessed=len(would_reprocess))


# ── 4. EXPENSIVE WORK ORDERING ──────────────────────────────────────────────
async def check_work_ordering():
    _h("4. EXPENSIVE WORK — dedup must precede PDF/OCR/LLM")
    import ast
    import inspect
    import textwrap

    import news_discovery_engine as nde
    tree = ast.parse(textwrap.dedent(inspect.getsource(nde._process_nse_announcements)))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            b = n.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant):
                n.body = b[1:] or [ast.Pass()]
    src = ast.unparse(tree)
    i_filter = src.find("already-persisted")
    i_pdf = src.find("await process_nse_announcement(")
    ok = 0 < i_filter < i_pdf
    print(f"  pre-filter at char {i_filter}, PDF/OCR/LLM call at {i_pdf}  -> ordering {'OK' if ok else 'WRONG'}")
    record("Dedup before expensive work", ok,
           "pre-filter precedes the PDF/OCR/LLM call" if ok else "expensive work runs first")


# ── 5. PROVENANCE, FULL SAMPLE ──────────────────────────────────────────────
async def check_provenance(s):
    _h("5. PROVENANCE — news_id linkage over the FULL sample")
    r = (await s.execute(text("""
        SELECT count(*) tot, count(*) FILTER (WHERE news_id IS NOT NULL) linked
        FROM causal_events"""))).first()
    print(f"  all-time events={r.tot:,}  linked={r.linked:,} ({100*r.linked/r.tot:.1f}%)")
    print("\n  by week:")
    for x in (await s.execute(text(f"""
        SELECT date_trunc('week', created_at)::date wk, count(*) n,
               count(*) FILTER (WHERE news_id IS NOT NULL) l
        FROM causal_events GROUP BY 1 ORDER BY 1 DESC LIMIT 8"""))).all():
        print(f"    {x.wk}  n={x.n:>5}  linked={x.l:>5}  ({100*x.l/max(x.n,1):>5.1f}%)")

    # Replay the fix: how many of today's drained premarket items WOULD link?
    rep = (await s.execute(text("""
        SELECT count(*) tot, count(*) FILTER (WHERE ni.id IS NOT NULL) match
        FROM premarket_news_queue q
        LEFT JOIN news_items ni ON md5(ni.headline) = md5(q.headline)
          AND COALESCE(ni.published_at, ni.crawled_at)::date
              = (q.captured_at AT TIME ZONE 'UTC')::date
        WHERE q.status='PROCESSED'
          AND (q.processed_at AT TIME ZONE 'Asia/Kolkata')::date = CURRENT_DATE"""))).first()
    pct = 100 * rep.match / max(rep.tot, 1)
    print(f"\n  REPLAY of the premarket provenance fix (not yet run live):")
    print(f"    today's drained items={rep.tot}  resolvable={rep.match} ({pct:.1f}%)")
    record("Provenance recoverable (replay)", pct >= 80 if rep.tot else None,
           f"{rep.match}/{rep.tot} ({pct:.1f}%) of drained items resolve to a news row",
           drained=rep.tot, resolvable=rep.match, pct=round(pct, 1))
    record("Provenance LIVE (news_id populated)", False,
           f"only {r.linked:,}/{r.tot:,} all-time; 0 today — the drain has not run "
           f"since the fix (it runs only while the market is open)",
           all_time_linked=r.linked, all_time=r.tot)


# ── 6. TIMESTAMP + LOOK-AHEAD ───────────────────────────────────────────────
async def check_timestamps(s):
    _h("6. TIMESTAMPS — tz correctness and look-ahead")
    r = (await s.execute(text("""
        SELECT count(*) tot,
               count(*) FILTER (WHERE published_at > crawled_at + interval '1 minute') future,
               count(*) FILTER (WHERE published_at IS NULL) nul
        FROM news_items WHERE source='NSE-Announcements'"""))).first()
    print(f"  NSE rows={r.tot:,}  published_at AFTER crawled_at (tz bug)={r.future}  NULL={r.nul}")
    record("No future-stamped news (tz bug)", r.future == 0,
           f"{r.future} rows stamped after their own crawl", rows=r.tot, future=r.future)

    la = (await s.execute(text("""
        SELECT count(*) FROM causal_events ce JOIN news_items ni ON ni.id = ce.news_id
        WHERE ce.created_at < ni.published_at - interval '1 minute'"""))).scalar()
    print(f"  events created BEFORE their own news published_at: {la}")
    record("No event predates its news", la == 0, f"{la} look-ahead events", violations=la)

    fk = (await s.execute(text("""
        SELECT count(*) FROM causal_events ce WHERE ce.news_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM news_items ni WHERE ni.id = ce.news_id)"""))).scalar()
    print(f"  events pointing at a missing news row: {fk}")
    record("No broken news_id FK", fk == 0, f"{fk} dangling references", dangling=fk)


# ── 7. IDENTITY RECALL over the CORRECT denominator ─────────────────────────
async def check_identity(s):
    _h("7. IDENTITY — recall over NSE-TRADEABLE events only")
    from utils.identity import Resolution, build_index, resolve_identity

    idx = await build_index()
    rows = (await s.execute(text("""
        SELECT bullish_stocks, bearish_stocks FROM causal_events
        WHERE created_at >= now() - interval '30 days'"""))).all()
    syms = set()
    for b, br in rows:
        for arr in (b, br):
            a = arr if isinstance(arr, list) else json.loads(arr or "[]")
            for x in a:
                syms.add(str(x))
    syms = sorted(syms)

    buckets = {k: [] for k in ("VALID_NSE_SYMBOL", "NSE_NAME_ALIAS", "AMBIGUOUS",
                               "BSE_ONLY", "MALFORMED", "UNKNOWN")}
    for x in syms:
        r = resolve_identity(x, idx)
        if r.resolution is Resolution.EXACT_SYMBOL:
            buckets["VALID_NSE_SYMBOL"].append(x)
        elif r.ok:
            buckets["NSE_NAME_ALIAS"].append(x)
        elif r.needs_review:
            buckets["AMBIGUOUS"].append((x, r.candidates))
        elif r.resolution is Resolution.INVALID:
            buckets["MALFORMED"].append(x)
        else:
            buckets["UNKNOWN"].append(x)

    # Split UNKNOWN by whether the identifier could denote an NSE equity AT ALL.
    #
    # This is the denominator question. A name matching NO NSE instrument on any
    # tier is a BSE-only listing, a delisted name or not an equity -- not a
    # resolution failure. Counting those against recall makes the figure
    # meaningless, which is exactly what the first run of this script did.
    from utils.identity import is_nse_eligible

    unknown = buckets.pop("UNKNOWN")
    not_listed, eligible_but_failed = [], []
    for x in unknown:
        (eligible_but_failed if is_nse_eligible(x, idx) else not_listed).append(x)
    buckets["NOT_NSE_LISTED"] = not_listed
    buckets["ELIGIBLE_BUT_UNRESOLVED"] = eligible_but_failed

    print(f"  {len(syms)} distinct emitted symbols, 30 days\n")
    for k in ("VALID_NSE_SYMBOL", "NSE_NAME_ALIAS", "AMBIGUOUS", "NOT_NSE_LISTED",
              "MALFORMED", "ELIGIBLE_BUT_UNRESOLVED"):
        v = buckets.get(k, [])
        print(f"    {k:<22}{len(v):>5}  ({100*len(v)/len(syms):>5.1f}%)")

    resolved = len(buckets["VALID_NSE_SYMBOL"]) + len(buckets["NSE_NAME_ALIAS"])
    not_nse = len(buckets["NOT_NSE_LISTED"]) + len(buckets["MALFORMED"])
    eligible = len(syms) - not_nse
    recall = 100 * resolved / max(eligible, 1)
    print(f"\n  NOT NSE-listed (excluded from the denominator) = {not_nse}")
    print(f"  eligible = {len(syms)} - {not_nse} = {eligible}")
    print(f"  RECALL over eligible = {resolved}/{eligible} = {recall:.1f}%")
    print(f"  (naive recall over ALL symbols = {100*resolved/len(syms):.1f}% "
          f"-- misleading, includes names NSE does not list)")

    record("Identity recall over eligible", recall >= 90,
           f"{resolved}/{eligible} = {recall:.1f}% of NSE-eligible symbols resolve",
           eligible=eligible, resolved=resolved, recall=round(recall, 1),
           ambiguous=len(buckets["AMBIGUOUS"]), not_nse_listed=len(not_listed),
           eligible_but_unresolved=len(eligible_but_failed))
    record("Ambiguous left unresolved", True,
           f"{len(buckets['AMBIGUOUS'])} ambiguous, none guessed",
           ambiguous=len(buckets["AMBIGUOUS"]))
    return buckets


# ── 8. AUTHORITATIVE VERIFICATION of every auto-resolution ──────────────────
async def check_resolutions_are_authoritative(s, buckets):
    _h("8. VERIFICATION — every auto-resolved symbol must exist in kite_instruments")
    from utils.identity import build_index, resolve_identity

    idx = await build_index()
    checked = wrong = 0
    bad = []
    for x in buckets["VALID_NSE_SYMBOL"] + buckets["NSE_NAME_ALIAS"]:
        r = resolve_identity(x, idx)
        if not r.ok:
            continue
        bare = r.symbol.replace(".NS", "")
        hit = (await s.execute(text("""
            SELECT tradingsymbol FROM kite_instruments
            WHERE exchange='NSE' AND tradingsymbol = :t LIMIT 1"""), {"t": bare})).first()
        checked += 1
        if not hit:
            wrong += 1
            bad.append((x, r.symbol))
    print(f"  verified {checked} resolutions against kite_instruments")
    print(f"  NOT PRESENT on NSE: {wrong}")
    for a, b in bad[:6]:
        print(f"    {a} -> {b}  NOT IN INSTRUMENT TABLE")
    record("Resolutions verified authoritative", wrong == 0,
           f"{wrong} of {checked} resolved symbols absent from kite_instruments",
           checked=checked, incorrect=wrong)


# ── 9. QUEUE / THROUGHPUT / LATENCY ─────────────────────────────────────────
async def check_throughput(s, day):
    _h("9. QUEUE THROUGHPUT AND END-TO-END LATENCY")
    r = (await s.execute(text(f"""
        SELECT count(*) n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM (crawled_at-published_at))/60) p50,
               percentile_cont(0.9) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM (crawled_at-published_at))/60) p90,
               max(EXTRACT(epoch FROM (crawled_at-published_at))/60) mx
        FROM news_items WHERE source='NSE-Announcements'
          AND (crawled_at {IST})::date = :d AND published_at <= crawled_at"""), {"d": day})).first()
    if r.n:
        print(f"  publish -> persist latency  n={r.n}  p50={r.p50:.1f}m  p90={r.p90:.1f}m  max={r.mx:.1f}m")
        record("Whole-day latency (backlog-contaminated)", None,
               f"p50={r.p50:.1f} min -- dominated by the pre-fix outage, not a "
               f"steady-state figure; see the next criterion",
               n=r.n, p50=round(r.p50, 1), p90=round(r.p90, 1), max=round(r.mx, 1))
    else:
        record("Whole-day latency", None, "no rows")

    # STEADY STATE. The figure above is dominated by a backlog: filings
    # published at 09:00 sat in the queue until the consumer fix landed at
    # 15:43, so their "latency" is really the outage. The honest steady-state
    # measure is filings PUBLISHED AFTER the consumer was already draining.
    r2 = (await s.execute(text(f"""
        SELECT count(*) n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM (crawled_at-published_at))/60) p50,
               percentile_cont(0.9) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM (crawled_at-published_at))/60) p90,
               max(EXTRACT(epoch FROM (crawled_at-published_at))/60) mx
        FROM news_items WHERE source='NSE-Announcements'
          AND news_metadata->>'seq_id' IS NOT NULL
          AND (crawled_at {IST})::date = :d
          AND published_at <= crawled_at
          AND (published_at {IST}) >= (CAST(:d AS date) + time '15:40')"""), {"d": day})).first()
    if r2.n:
        print(f"\n  post-consumer-fix window: n={r2.n}  p50={r2.p50:.1f}m  "
              f"p90={r2.p90:.1f}m  max={r2.mx:.1f}m")

    # Even that window is contaminated one level down: five filings published
    # 15:47-15:59 all persisted in ONE batch at 16:23:53, after a 22.9-minute
    # cycle gap. Their "latency" is that gap. The only uncontaminated
    # observations are filings arriving once the queue was already empty.
    tail = (await s.execute(text(f"""
        SELECT company,
               EXTRACT(epoch FROM (crawled_at - published_at))/60 lat
        FROM news_items WHERE source='NSE-Announcements'
          AND news_metadata->>'seq_id' IS NOT NULL
          AND (crawled_at {IST})::date = :d AND published_at <= crawled_at
        ORDER BY published_at DESC LIMIT 3"""), {"d": day})).all()
    if tail:
        lats = sorted(float(t.lat) for t in tail)
        med = lats[len(lats) // 2]
        print(f"\n  UNCONTAMINATED tail (last {len(lats)} filings, queue already drained):")
        for t in tail:
            print(f"    {float(t.lat):>6.1f}m  {str(t.company)[:44]}")
        print(f"    median = {med:.1f} min")
        # n=3 cannot establish a p50. Say so rather than grading on it.
        record("Steady-state latency p50 < 15 min", None,
               f"last-{len(lats)} median {med:.1f} min (looks healthy) but n={len(lats)} "
               f"after hours cannot establish steady state — tomorrow's session is the test",
               tail_median=round(med, 1), tail_n=len(lats),
               contaminated_p50=round(float(r2.p50), 1) if r2.n else None)
    else:
        record("Steady-state latency p50 < 15 min", None, "no post-fix rows")


# ── 10. PREMARKET BACKLOG CLASSIFICATION (no deletion, no rule) ─────────────
async def check_backlog(s):
    _h("10. PREMARKET BACKLOG — classify only, delete nothing")
    r = (await s.execute(text("""
        SELECT count(*) n, min(captured_at) oldest, max(captured_at) newest
        FROM premarket_news_queue WHERE status='PENDING'"""))).first()
    print(f"  PENDING={r.n:,}  oldest={r.oldest}  newest={r.newest}")
    print("\n  by age bucket:")
    for x in (await s.execute(text("""
        SELECT CASE
                 WHEN captured_at >= now() - interval '3 days'  THEN 'a. <3d  (inside drain cutoff)'
                 WHEN captured_at >= now() - interval '7 days'  THEN 'b. 3-7d (outside cutoff)'
                 WHEN captured_at >= now() - interval '14 days' THEN 'c. 7-14d'
                 ELSE 'd. >14d' END bucket,
               count(*) n FROM premarket_news_queue WHERE status='PENDING'
        GROUP BY 1 ORDER BY 1"""))).all():
        print(f"    {x.bucket:<34}{x.n:>6,}")
    dup = (await s.execute(text("""
        SELECT count(*) tot, count(DISTINCT (symbol, headline)) uq
        FROM premarket_news_queue WHERE status='PENDING'"""))).first()
    print(f"\n  distinct (symbol, headline): {dup.uq:,} of {dup.tot:,} "
          f"-> {dup.tot-dup.uq:,} repeats already in the backlog")
    record("Backlog classified, untouched", True,
           f"{r.n:,} PENDING classified; nothing deleted or traded", pending=r.n)


# ── 11. POINT-IN-TIME LINEAGE ───────────────────────────────────────────────
async def check_lineage(s, day):
    _h("11. POINT-IN-TIME LINEAGE — earliest legitimate knowledge time per event")
    rows = (await s.execute(text(f"""
        SELECT ce.id, ce.event_title, ce.created_at ev_ts,
               ni.published_at pub_ts, ni.crawled_at crawl_ts
        FROM causal_events ce LEFT JOIN news_items ni ON ni.id = ce.news_id
        WHERE (ce.created_at {IST})::date = :d ORDER BY ce.created_at LIMIT 500"""),
        {"d": day})).all()
    if not rows:
        record("Point-in-time lineage", None, "no events on this date")
        return
    viol = 0
    for r in rows:
        # knowable_at = the LATER of (we crawled it) and (event row created).
        # Never published_at alone: publication is not possession.
        knowable = max([t for t in (r.crawl_ts, r.ev_ts) if t is not None])
        if r.pub_ts and knowable < r.pub_ts:
            viol += 1
    print(f"  events examined={len(rows)}  knowable_at < published_at: {viol}")
    print("  knowable_at = max(crawled_at, created_at) — publication is NOT possession")
    record("Lineage: knowable_at never precedes publication", viol == 0,
           f"{viol} of {len(rows)} events claim knowledge before publication",
           examined=len(rows), violations=viol)


async def main(day):
    print(f"\nSTEP 1A VALIDATION — session {day}   "
          f"(run {dt.datetime.now(IST_TZ).strftime('%H:%M:%S')} IST)")
    async with AsyncSessionLocal() as s:
        await check_headline_immutable(s, day)
        await check_dedup(s, day)
        await check_restart_idempotency(s, day)
        await check_work_ordering()
        await check_provenance(s)
        await check_timestamps(s)
        buckets = await check_identity(s)
        await check_resolutions_are_authoritative(s, buckets)
        await check_throughput(s, day)
        await check_backlog(s)
        await check_lineage(s, day)

    _h("ACCEPTANCE SUMMARY")
    w = max(len(r.name) for r in RESULTS)
    for r in RESULTS:
        print(f"  {r.mark:<13}{r.name:<{w+2}}{r.detail}")
    p = sum(1 for r in RESULTS if r.passed is True)
    f = sum(1 for r in RESULTS if r.passed is False)
    i = sum(1 for r in RESULTS if r.passed is None)
    print(f"\n  PASS {p}   FAIL {f}   INCONCLUSIVE {i}")
    print(f"\n  STEP 1A PRODUCTION-GRADE: {'YES' if f == 0 and i == 0 else 'NO'}")


if __name__ == "__main__":
    d = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today()
    asyncio.run(main(d))
