"""Tier 0 — deterministic news router (2026-08-18).

Decides WHICH scorer a headline should go to, before any model runs.

Why this exists
---------------
FinBERT was the only scorer, and it is trained on company-earnings language.
Outside that domain it returns `neutral / 0.000` — correctly, that is the
model working as designed, verified directly (+0.948 on "record profit
growth", -0.963 on "posts massive loss, shares crash 20%", 0.000 on "Trump
says Iran should surrender, threatens to bomb Oman").

The problem was using a company-sentiment model as the gatekeeper for
EVERYTHING. Two downstream gates key off its score:

    abs(score) > 0.6   -> LLM classification (causal events)
    abs(score) >= 0.75 -> evaluate_news_flash (a trading strategy)

So macro/geopolitical news could never reach either. Observed live on
18-Aug: Brent above $91 on a US-Iran naval blockade, and the regime engine
reading `VIX 11.39 -> LOW` with `news market_wide_score 0.0`.

Why the router is NOT an LLM
----------------------------
The obvious design is "ask an LLM where to send it". At 823 headlines/day
(2,891 peak) against a 90 RPM ceiling shared with the trade loop and hub,
that is 823+ calls/day spent only on routing — and a routing call costs the
same as the classification it routes to. It would also re-create the failure
fixed on 17-Aug, where unbounded per-item LLM work in this exact crawl
produced 171 SoftTimeLimitExceeded and six hours with zero news persisted.

The signal is nearly free without a model: `source` alone separates most of
it (NSE-Announcements -> filing, Reuters/RBI -> macro-leaning), and the
crawler already extracts `tickers_affected`.
"""
from __future__ import annotations

import re

# ── Domains ──────────────────────────────────────────────────────────────────
COMPANY = "COMPANY"   # -> FinBERT (in-domain, free)
MACRO   = "MACRO"     # -> LLM (geopolitics, rates, currency, commodities)
FILING  = "FILING"    # -> existing classify_event path
NOISE   = "NOISE"     # -> low priority (market wraps, listicles, roundups)

# Sources that publish exchange filings verbatim.
_FILING_SOURCES = {"NSE-Announcements", "BSE-Announcements", "SSE-Announcements"}

# A filing headline is machine-generated and always shaped
# "<Company> Limited: <Action> — <body>".
_FILING_RE = re.compile(r"^.{3,80}?\bLimited:\s", re.I)

# Macro vocabulary. Deliberately about the ECONOMY/GEOPOLITICS rather than any
# single company — a headline naming a company is handled as COMPANY even if it
# also mentions oil.
#
# These MUST be matched on word boundaries, not as substrings. Validated against
# 8 days of live headlines, plain `term in headline` sent company earnings down
# the macro path: "war" matched softWARe and MurudeshWAR Ceramics, "rbi" matched
# TuRBIne (Triveni Turbine), "cpi" would match reCIPient. Each false MACRO is a
# wasted LLM call on news FinBERT already handles correctly.
_MACRO_TERMS = (
    "hormuz", "iran", "israel", "gaza", "ukraine", "russia", "opec", "houthi",
    "geopolitical", "geopolitics", "sanctions", "blockade", "missile",
    "airstrike", "ceasefire", "war", "tariff", "tariffs", "trade deal",
    "crude", "brent", "oil price", "oil prices", "gold price", "gold prices",
    "bullion",
    "fed", "federal reserve", "fomc", "rate cut", "rate hike", "interest rate",
    "rbi", "repo rate", "monetary policy", "inflation", "cpi", "gdp",
    "rupee", "dollar index", "bond yield", "bond yields", "treasury yield",
    "fii", "dii", "foreign investors",
)

# One alternation, longest-first so "oil prices" wins over "oil price".
_MACRO_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in sorted(_MACRO_TERMS, key=len, reverse=True)) + r")\b"
)

# Company-earnings vocabulary. A headline carrying these is about a firm's
# results even when it also mentions a macro term ("profit falls on higher crude
# costs"), and FinBERT is genuinely in-domain for it — so it must not be routed
# to the LLM regardless of what else it says.
_EARNINGS_RE = re.compile(
    r"\b(?:net profit|q[1-4] (?:profit|result|results|earnings)|profit (?:rises|declines|drops|falls|jumps|surges)"
    r"|revenue|ebitda|margins?|order book|dividend|bonus issue|stock split|buyback)\b"
)

# Market-wrap / listicle patterns. These are commentary about the market rather
# than a catalyst — they carry no tradable event and repeat many times a day.
_NOISE_PATTERNS = (
    "key things that changed", "top gainers", "top losers", "gainers &",
    "market live", "stock market live", "week ahead", "weekahead",
    "closing bell", "opening bell", "market wrap", "sensex today",
    "what changed for the market", "stocks to watch", "buy or sell",
    "trade setup", "technical view", "expert view",
)


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def route_headline(
    headline: str,
    source: str | None = None,
    tickers: list[str] | None = None,
) -> str:
    """Return COMPANY | MACRO | FILING | NOISE for a headline.

    Pure and side-effect free so it is cheap to call on every item and trivial
    to test. Order matters: filings are unambiguous, noise is checked before
    macro (a "7 key things… crude oil" listicle is a market wrap, not a
    catalyst), and anything naming a ticker is company news even when it also
    mentions macro terms ("Union Pacific fuel cost spike from Iran war").
    """
    h = _norm(headline)
    if not h:
        return NOISE

    # 1. Exchange filings — by source, or by their machine-generated shape.
    if source in _FILING_SOURCES or _FILING_RE.match(headline or ""):
        return FILING

    # 2. Market wraps / listicles. Before MACRO on purpose: these mention crude
    #    and the Fed constantly but describe a move that already happened.
    if any(p in h for p in _NOISE_PATTERNS):
        return NOISE

    # 3. A named ticker makes it company news, whatever else it mentions.
    if tickers:
        return COMPANY

    # 4. Earnings language means a company result even without a resolved
    #    ticker — the extractor misses small caps ("Murudeshwar Ceramics
    #    consolidated net profit declines 58.03%"). FinBERT is in-domain here.
    if _EARNINGS_RE.search(h):
        return COMPANY

    # 5. Macro vocabulary with no company attached.
    if _MACRO_RE.search(h):
        return MACRO

    # 6. Default: treat as company news. FinBERT is free, so an unrouted item
    #    costs nothing to score, whereas a wrong MACRO guess costs an LLM call.
    return COMPANY


def dedupe_key(headline: str) -> str:
    """Collapse syndicated near-duplicates.

    URL-based dedupe (in run_news_crawl) misses the same story republished by
    several outlets with different links. Live sample from 11-18 Aug: "Crude
    oil price steadies amid no progress in US-Iran talks" appeared ~15 times
    and "US stocks mixed as Iran tensions weigh" ~13 times. Each copy would
    otherwise cost its own LLM call on the MACRO path.

    Normalises case, punctuation, whitespace and the trailing " - Publisher"
    attribution Reuters/Bloomberg append.
    """
    h = _norm(headline)
    h = re.sub(r"\s+[-–—]\s+[a-z0-9 .&']{2,24}$", "", h)   # trailing " - Reuters"
    h = re.sub(r"[^a-z0-9 ]+", " ", h)
    h = re.sub(r"\s+", " ", h).strip()
    return h
