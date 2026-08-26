# Research scripts — 2026-08-24 alpha investigation

Ad-hoc analysis scripts backing three reports in `docs/`. They are **research
code, not production code**: read-only against the database, no imports from
them anywhere in the app, and each was written to answer one question.

They are committed because the reports cite them by name and claim
reproducibility. That claim is only honest if the code is here.

## Hard-coded paths

Each script reads and writes JSON in a scratchpad path from the session that
produced it. To re-run, change the `SP=` constant at the top of the file to a
directory you can write to. The pipeline order matters — later scripts consume
earlier ones' output.

## Order

**Phase 1 — production forensics** (`docs/2026-08-24_FORENSIC_MISSED_OPPORTUNITIES.md`)

| Script | Answers |
|---|---|
| `conf_gate.py` | what the +1.5% entry gate costs on one session |
| `sweep.py` | same, swept across thresholds, one session, unbiased population |
| `multiday.py` | same across 14 sessions — the result the report relies on |

**Phase 2 — our own event stream** (`docs/2026-08-24_PHASE2_EVENT_REACTION_STUDY.md`)

| Script | Answers |
|---|---|
| `entity.py` | staged symbol-resolution funnel, raw string → tradable instrument |
| `halluc.py` | decomposes what fails to resolve: stale tickers vs company names vs non-existent |
| `elig.py` | eligibility funnel S0–S5 (mapping, session, dedup, confounding) |
| `react.py` | S6–S8 price validation, then reaction/MFE/MAE measurement |
| `curves.py` | distributions by horizon, direction, importance tier, category |
| `predrift.py` | pre-event drift, result concentration, per-session consistency |
| `clf.py` | classifier confusion matrices vs base rate, high-confidence subsets |

**Phase 3 — independent ground truth** (`docs/2026-08-24_PHASE3_GROUND_TRUTH_NEWS_ALPHA.md`)

| Script | Answers |
|---|---|
| `gt_build.py` | ground-truth event set from NSE announcements; deterministic taxonomy |
| `gt_react.py` | Study A (intraday 1m) and Study B (overnight gap/day) measurement |
| `gt_analyse.py` | bootstrap CIs, control comparison, cost adjustment, category breakdown |

`gt_build.py` deliberately does not read `causal_events` — that separation is
the whole point of Phase 3.

## Reproducibility limits

- Bootstrap uses `random.seed(7)`; CIs reproduce exactly on the same data.
- The data does not stand still: `candles` and `news_items` keep growing, so
  re-running later widens the window and shifts counts. The numbers in the
  reports are as of 2026-08-24.
- 1m candle history began 2026-06-18 and `causal_events` on 2026-07-16. Those
  two dates bound every intraday study here and are why Phase 2 has 22 usable
  sessions rather than 44.
