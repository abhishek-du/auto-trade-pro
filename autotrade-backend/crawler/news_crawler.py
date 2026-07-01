"""Async financial news crawler with FinBERT sentiment analysis.

Sources (in priority order):
  1. NewsAPI   — requires NEWSAPI_KEY  (optional)
  2. Finnhub   — requires FINNHUB_KEY  (optional)
  3. Free RSS  — no key required       (always attempted)

Sentiment is scored by ProsusAI/finbert when torch+transformers are present;
falls back to a keyword heuristic otherwise.
"""

import asyncio
import re
import html
import time
from datetime import datetime, timedelta
from functools import lru_cache

import httpx
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import NewsItem
from utils.config import settings
from utils.logger import logger

# How many recent matching headlines feed into the symbol-level sentiment average.
# Single source of truth so future tuning is easy.
_SENTIMENT_WINDOW: int = 10

# ── Constants ─────────────────────────────────────────────────────────────────

_NEWSAPI_BASE  = settings.NEWSAPI_BASE_URL
_FINNHUB_BASE  = settings.FINNHUB_BASE_URL
# India-first RSS feeds — configured via RSS_FEED_URLS in .env (comma-separated)
_RSS_FEEDS     = [u.strip() for u in settings.RSS_FEED_URLS.split(",") if u.strip()]
_FOREX_CODES   = {"EUR", "USD", "GBP", "JPY", "AUD", "CHF", "CAD"}
_FINBERT_MODEL = "ProsusAI/finbert"

# Per-source health: count of consecutive crawl cycles that returned 0 rows.
# Logged at error level when any source crosses _SOURCE_FAIL_THRESHOLD so an
# operator can notice a quiet outage instead of discovering it via empty news
# feeds in the UI. Keyed by the same source-name strings the source_counts
# dict uses.
_SOURCE_ZERO_STREAK: dict[str, int] = {"rss": 0, "newsdata": 0, "finnhub": 0, "newsapi": 0, "yfinance": 0}
_SOURCE_FAIL_THRESHOLD: int = 3

_POSITIVE_WORDS = [
    "rally", "gain", "surge", "bullish", "rise", "growth",
    "profit", "record high",
]
_NEGATIVE_WORDS = [
    "crash", "fall", "drop", "bearish", "decline", "loss",
    "recession", "plunge",
]

# FinBERT accuracy guards
_CONFIDENCE_THRESHOLD = 0.60   # below this → treat as neutral (model is unsure)
_UNCERTAINTY_PHRASES  = [
    "ahead of", "before decision", "awaiting", "pending",
    "holds steady", "flat ahead", "rangebound", "consolidates",
    "wait-and-see", "cautious ahead", "on hold",
]

# Indian broker recommendation patterns — FinBERT cannot parse these correctly.
# Headlines follow: "{Action} {Stock}; target of Rs {N}: {Broker}"
# We detect the leading action word and score directly, bypassing FinBERT.
_INDIA_BUY_WORDS  = frozenset({
    "buy", "strong buy", "add", "accumulate", "outperform", "overweight",
    "reiterate buy", "maintain buy", "upgrade", "top pick",
})
_INDIA_SELL_WORDS = frozenset({
    "sell", "reduce", "underperform", "underweight", "avoid",
    "downgrade", "exit", "book profit",
})


def _india_broker_score(headline: str) -> "dict | None":
    """Detect Indian broker recommendation headlines and score them directly.

    Returns a scored dict when the headline starts with a known action word,
    or None to fall through to FinBERT.  Score magnitude is 0.75 (strong signal
    but below 1.0 to leave room for earnings/macro context).
    """
    lower = headline.lower().lstrip()
    for word in _INDIA_BUY_WORDS:
        if lower.startswith(word + " ") or lower.startswith(word + ":"):
            return {"sentiment": "positive", "score": 0.75, "confidence": 0.75}
    for word in _INDIA_SELL_WORDS:
        if lower.startswith(word + " ") or lower.startswith(word + ":"):
            return {"sentiment": "negative", "score": -0.75, "confidence": 0.75}
    return None


def _is_uncertain(headline: str) -> bool:
    """Return True for wait-and-see headlines FinBERT mis-scores as directional."""
    lower = headline.lower()
    return any(phrase in lower for phrase in _UNCERTAINTY_PHRASES)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _struct_to_dt(t) -> datetime | None:
    """Convert a feedparser time.struct_time to a naive UTC datetime."""
    if t is None:
        return None
    try:
        return datetime(*t[:6])
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# PART 1 — News Fetching
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_newsapi_headlines(
    query: str = "forex stock market trading",
) -> list[dict]:
    """Fetch up to 20 English headlines from NewsAPI /v2/everything.

    Returns [] silently when NEWSAPI_KEY is absent — never raises.
    Each item: {headline, source, url, published_at}
    """
    if not settings.newsapi_available:
        # Debug level: NewsAPI is an optional secondary source. With the India
        # RSS stack as primary, missing this key is the expected configuration
        # and warning-level noise every 5 minutes adds nothing.
        logger.debug("NEWSAPI_KEY not configured — skipping NewsAPI fetch")
        return []

    params = {
        "q":        query,
        "language": "en",
        "sortBy":   "publishedAt",
        "pageSize": 20,
        "apiKey":   settings.NEWSAPI_KEY,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(_NEWSAPI_BASE, params=params)
            resp.raise_for_status()
            data = resp.json()

        articles = data.get("articles", [])
        result = [
            {
                "headline":     a.get("title", "").strip(),
                "source":       (a.get("source") or {}).get("name", "NewsAPI"),
                "url":          a.get("url"),
                "published_at": _parse_dt(a.get("publishedAt")),
            }
            for a in articles
            if a.get("title") and a["title"] != "[Removed]"
        ]
        logger.info(f"NewsAPI ✓  {len(result)} headlines  query={query!r}")
        return result

    except Exception as exc:
        logger.error(f"NewsAPI fetch failed: {exc}")
        return []


async def fetch_finnhub_news(category: str = "general") -> list[dict]:
    """Fetch general market news from Finnhub /api/v1/news.

    Returns [] silently when FINNHUB_KEY is absent — never raises.
    Each item: {headline, source, url, published_at}
    """
    if not settings.finnhub_available:
        logger.warning("FINNHUB_KEY not configured — skipping Finnhub fetch")
        return []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_FINNHUB_BASE}/news",
                params={"category": category, "token": settings.FINNHUB_KEY},
            )
            resp.raise_for_status()
            items = resp.json()

        result = [
            {
                "headline":     item.get("headline", "").strip(),
                "source":       item.get("source", "Finnhub"),
                "url":          item.get("url"),
                "published_at": (
                    datetime.utcfromtimestamp(item["datetime"])
                    if item.get("datetime") else None
                ),
            }
            for item in items
            if item.get("headline")
        ]
        logger.info(f"Finnhub ✓  {len(result)} headlines  category={category!r}")
        return result

    except Exception as exc:
        logger.error(f"Finnhub news fetch failed: {exc}")
        return []


_NEWSDATA_BASE = "https://newsdata.io/api/1/news"


async def fetch_newsdata_india() -> list[dict]:
    """Fetch Indian business news from NewsData.io (covers ET, Mint, BS, NDTV).

    Returns [] silently when NEWSDATA_KEY is absent — never raises.
    Free tier: 200 requests/day. Each item: {headline, source, url, published_at}
    """
    if not settings.newsdata_available:
        return []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(_NEWSDATA_BASE, params={
                "apikey":   settings.NEWSDATA_KEY,
                "country":  "in",
                "category": "business",
                "language": "en",
            })
            resp.raise_for_status()
            items = resp.json().get("results", []) or []

        result = [
            {
                "headline":     (item.get("title") or "").strip(),
                "source":       (item.get("source_id") or "NewsData"),
                "url":          item.get("link"),
                "published_at": _parse_dt(item.get("pubDate")),
            }
            for item in items
            if item.get("title")
        ]
        logger.info(f"NewsData.io ✓  {len(result)} India headlines")
        return result

    except Exception as exc:
        logger.error(f"NewsData.io fetch failed: {exc}")
        return []


async def fetch_free_rss_news() -> list[dict]:
    """Fetch headlines from free RSS feeds — no API key required.

    Uses feedparser (synchronous) dispatched to a thread-pool executor.
    Each item: {headline, source, url, published_at}
    """
    import feedparser  # noqa: PLC0415  (optional dep, checked at runtime)

    def _parse_feed(url: str) -> list[dict]:
        try:
            import httpx
            # Use a standard user-agent to avoid blocks, and disable SSL verify
            # because some Indian news sites have misconfigured certs or proxies block them
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            with httpx.Client(verify=False, timeout=10.0) as client:
                res = client.get(url, headers=headers)
                res.raise_for_status()
                feed_data = res.text

            feed = feedparser.parse(feed_data)
            source = feed.feed.get("title") or url.split("/")[2]
            rows = []
            for entry in feed.entries:
                title = (entry.get("title") or "").strip()
                if not title:
                    continue
                rows.append({
                    "headline":     title,
                    "source":       source,
                    "url":          entry.get("link"),
                    "published_at": _struct_to_dt(entry.get("published_parsed")),
                })
            return rows
        except Exception as exc:
            logger.error(f"RSS parse failed [{url}]: {exc}")
            return []

    loop = asyncio.get_event_loop()
    all_rows: list[dict] = []
    for feed_url in _RSS_FEEDS:
        rows = await loop.run_in_executor(None, _parse_feed, feed_url)
        all_rows.extend(rows)
        if rows:
            logger.info(f"RSS ✓  {len(rows)} headlines  feed={feed_url.split('?')[0]}")

    return all_rows


# Common English / market-speak / industry tokens that collide with short NSE
# tradingsymbols and would produce false-positive ticker tags if left in the
# name map (e.g. "FOCUS" is a tradingsymbol but headlines say "in focus today").
# Industry words like "STEEL", "BANK", "POWER" appear in many company names AND
# in generic copy, so we never register them as needles.
_TICKER_STOPWORDS: frozenset[str] = frozenset({
    # Articles, prepositions, common short English
    "the","and","for","not","you","can","all","any","own","new","old",
    "big","low","top","day","key","est","one","two","via","yes",
    # Generic industry / sector words
    "bank","steel","power","cement","oil","gas","coal","auto","tyre",
    "tyres","metal","metals","mining","tech","textile","fashion","retail",
    "pharma","drugs","drug","sugar","tea","tobacco","food","foods","cable",
    "paint","paints","paper","glass","wood","carbon","chem","chemical",
    "chemicals","fertilizer","fertilizers","plastic","plastics","mfg",
    "manufacturing","industries","industrial","corp","corporation","ltd",
    "limited","company","group","international","intl","national","india",
    "bharat","global","world","systems","services","solutions","holdings",
    "enterprises","investments","capital","finance","financial","logistics",
    "telecom","communications","energy","resources","power","engineering",
    "construction","builders","developers","realty","estate","hospitality",
    "hotels","healthcare","health","medical","life","sciences","insurance",
    # Money / market jargon
    "ipo","gst","tax","fno","fdi","gdp","cpi","wpi","rbi","sebi","sec",
    "nse","bse","fii","dii","etf","eps","pat","ebitda","ebit","yoy","qoq",
    "mom","best","worst","live","next","last","more","less","over","under",
    "stock","stocks","share","shares","price","value","today","week","year",
    "month","time","data","news","report","alert","update","read","watch",
    "view","add","cut","raise","hike","fall","rise","gain","drop","jump",
    "slide","rally","surge","crash","plunge","soar","tumble","slump",
    "decline","outperform","underperform","accumulate","reduce","hold",
    "target","rs","inr","usd","trade","trades","close","open","cse","ats",
    "nfo","mcx","cdsl","nsdl","gmp","fpi","ofs","fpo","qib","hni","amfi",
    "amf","nav","sip","mfs","focus","reach","scope","peak","mark","level",
    "core","prime","major","minor","star","stars","fresh","first","next",
    "ace","arc","spot","plus","step","edge","mega","alpha","beta","delta",
    # Family brand names shared across many group companies — they have their
    # own bare tradingsymbol (RELIANCE, BAJFINANCE, …) so we don't need an
    # extra short alias for these, and aliasing them caused false positives.
    "icici","hdfc","tata","adani","bajaj","jindal","reliance","mahindra",
    "kotak","birla","godrej","murugappa","piramal","essar","essel","ruia",
    "dabur","mukesh","ambani","srei","aditya","wadia","sanmar","nilkamal",
    "saregama","jaiprakash","jsw",
    # Indian states / regions (often appear as company-name first tokens)
    "gujarat","andhra","kerala","punjab","haryana","rajasthan","odisha",
    "orissa","tamilnadu","maharashtra","karnataka","telangana","goa",
    "jammu","kashmir","uttar","bengal","bihar","assam","sikkim","mumbai",
    "chennai","delhi","kolkata","bangalore","hyderabad","pune","ahmedabad",
    "north","south","east","west","central",
    # Index names — appear in ETF names like "SBI NIFTY 50 ETF"
    "sensex","nifty","bharat","midcap","smallcap","largecap",
    # Corporate suffixes / generic name words
    "securities","industries","products","ventures","holdings","enterprises",
    "investments","developers","builders","carriers","telecommunications",
    # Common mutual-fund / ETF naming words (HDFC GROWTH FUND, ICICI VALUE …)
    "growth","value","balanced","dynamic","advantage","select","premier",
    "leader","leaders","vision","equity","income","bond","liquid","arbitrage",
    "hybrid","multicap","midcap","smallcap","largecap","focused","quality",
    "momentum","prudential","mutual","fund","scheme","plan","direct","regular",
    "dividend","reinvest","cumulative","series","tracker","passive",
})

# Module-level cache for the India name → NSE symbol map, populated by
# _build_india_name_map() at the start of each crawl. We deliberately avoid
# lru_cache here so each crawl can refresh from the DB; we cache via a TTL.
_india_name_map_cache: dict[str, str] = {}
_india_name_map_built_at: float = 0.0
_INDIA_MAP_TTL_SECONDS: int = 6 * 3600   # rebuild at most every 6 hours


async def _build_india_name_map(session: AsyncSession) -> dict[str, str]:
    """Build {needle.lower() → NSE symbol} from the Kite instrument master.

    Priority of sources:
      1. ``kite_instruments`` DB table (~9.6k NSE EQ rows when populated)
      2. In-memory ``INSTRUMENT_CACHE`` from ``zerodha_instruments``
      3. ``engine.portfolio_service.NSE_STOCK_LOOKUP`` fallback (~59 large-caps)

    For each EQ row we add three needles:
      • the bare tradingsymbol (``NMDC``, ``ZEEL``, ``BHEL`` …)
      • the full company ``name`` field as published by NSE
      • the first significant token of the name (``INTERGLOBE`` for IndiGo,
        ``CUMMINS`` for ``CUMMINS INDIA`` …) — most headlines use a short
        brand alias, not the full registered name.

    Short tokens (< 3 chars) and stopwords in :data:`_TICKER_STOPWORDS` are
    skipped to keep precision high.
    """
    global _india_name_map_cache, _india_name_map_built_at

    if _india_name_map_cache and (time.time() - _india_name_map_built_at) < _INDIA_MAP_TTL_SECONDS:
        return _india_name_map_cache

    out: dict[str, str] = {}

    def _accept(needle: str) -> bool:
        n = needle.strip().lower()
        return bool(n) and len(n) >= 3 and n not in _TICKER_STOPWORDS

    # ── Source 1: kite_instruments DB table (preferred — persistent across restarts)
    # Two passes: (A) bare tradingsymbols win the slot, (B) full names fill in the rest.
    # Multi-word company names always pass; single-word names must be > 4 chars and
    # not collide with stopwords. We never alias on a single name token — that's how
    # the earlier version ended up mapping "india" → ABB and "steel" → SAIL.
    try:
        from collections import Counter
        from db.models import KiteInstrument
        rows = (await session.execute(
            select(KiteInstrument.tradingsymbol, KiteInstrument.name).where(
                KiteInstrument.instrument_type == "EQ",
                KiteInstrument.segment == "NSE",
            )
        )).all()
        # Filter to pure equity tradingsymbols. Excludes:
        #   - delivery-series variants (BE/ST/SG suffix via "-")
        #   - ETFs and index trackers (ETF/IETF/BEES/BETA suffix)
        _ETF_SUFFIXES = ("ETF", "IETF", "BEES", "BETA")
        clean_rows = [
            ((ts or "").strip().upper(), (name or "").strip())
            for ts, name in rows
            if ts
            and "-" not in (ts or "").upper()
            and not any((ts or "").upper().endswith(s) for s in _ETF_SUFFIXES)
        ]

        # Pass A — bare tradingsymbols (e.g. "NMDC", "BHEL", "ZEEL", "INDIGO")
        for ts, _name in clean_rows:
            if _accept(ts):
                out.setdefault(ts.lower(), f"{ts}.NS")

        # Pass B — full registered company names (e.g. "ZEE ENTERTAINMENT ENT")
        for ts, name in clean_rows:
            if not name:
                continue
            name_low = name.lower()
            # Multi-word names are always safe — at least one token is distinctive.
            # Single-word names: require length > 4 and not a stopword.
            is_multi = len(name_low.split()) > 1
            if is_multi or (len(name_low) > 4 and name_low not in _TICKER_STOPWORDS):
                out.setdefault(name_low, f"{ts}.NS")

        # Pass C — first significant token of the name as a short-brand alias.
        # This catches headlines that use the common brand ("Cummins", "Zee")
        # instead of the full registered name ("CUMMINS INDIA", "ZEE
        # ENTERTAINMENT ENT"). Three guardrails keep precision high:
        #   1. Stopwords + isalpha + 5-char minimum filter generic words
        #      ("india", "steel", "power", "bank", "icici", "bajaj", ...).
        #   2. Bare tradingsymbols processed in Pass A keep their slot via
        #      setdefault (so "indigo" stays → INDIGO.NS, not INDIGOPNTS.NS).
        #   3. UNIQUENESS — we only alias on a first token if exactly one
        #      company in the universe has that token as its first word.
        #      Without this, "icici" would alias to whichever ICICI-* company
        #      hit setdefault first, and "bajaj" / "jindal" / "tata" likewise.
        first_tokens: list[tuple[str, str]] = []
        for ts, name in clean_rows:
            if not name:
                continue
            for tok in name.split():
                tok_norm = tok.strip(".,&()/").lower()
                if (
                    len(tok_norm) >= 5
                    and tok_norm not in _TICKER_STOPWORDS
                    and tok_norm.isalpha()
                ):
                    first_tokens.append((tok_norm, ts))
                    break

        token_counts = Counter(tok for tok, _ in first_tokens)
        for tok, ts in first_tokens:
            if token_counts[tok] == 1:
                out.setdefault(tok, f"{ts}.NS")
    except Exception as exc:
        logger.debug(f"[news_crawler] kite_instruments read failed: {exc}")

    # ── Source 2: in-memory Kite cache (when DB row count is zero on first run)
    if not out:
        try:
            from crawler.zerodha_instruments import INSTRUMENT_CACHE
            clean_mem = [
                (ts.upper(), (meta.get("name") or "").strip())
                for ts, meta in INSTRUMENT_CACHE.items()
                if meta.get("instrument_type") == "EQ"
                and meta.get("segment") == "NSE"
                and "-" not in ts.upper()
            ]
            for ts, _name in clean_mem:
                if _accept(ts):
                    out.setdefault(ts.lower(), f"{ts}.NS")
            for ts, name in clean_mem:
                if not name:
                    continue
                name_low = name.lower()
                if len(name_low.split()) > 1 or (len(name_low) > 4 and name_low not in _TICKER_STOPWORDS):
                    out.setdefault(name_low, f"{ts}.NS")
        except Exception as exc:
            logger.debug(f"[news_crawler] INSTRUMENT_CACHE read failed: {exc}")

    # ── Source 3: NSE_STOCK_LOOKUP fallback / supplement (curated aliases)
    try:
        from engine.portfolio_service import NSE_STOCK_LOOKUP
        for name, symbol in NSE_STOCK_LOOKUP.items():
            if _accept(name):
                out.setdefault(name.lower(), symbol)
            bare = symbol.replace(".NS", "").replace(".BO", "")
            if _accept(bare):
                out.setdefault(bare.lower(), symbol)
    except Exception as exc:
        logger.debug(f"[news_crawler] NSE_STOCK_LOOKUP read failed: {exc}")

    _india_name_map_cache = out
    _india_name_map_built_at = time.time()
    logger.info(f"[news_crawler] India name map built: {len(out)} needles")
    return out


def _india_name_map() -> dict[str, str]:
    """Return the cached India name → NSE symbol map.

    Returns a small fallback derived from :data:`NSE_STOCK_LOOKUP` if the
    crawler's pre-warm step (:func:`_build_india_name_map`) has not yet run
    in this process — keeps the sync extractor callable from any context.
    """
    if _india_name_map_cache:
        return _india_name_map_cache
    out: dict[str, str] = {}
    try:
        from engine.portfolio_service import NSE_STOCK_LOOKUP
        for name, symbol in NSE_STOCK_LOOKUP.items():
            out[name.lower()] = symbol
            bare = symbol.replace(".NS", "").replace(".BO", "")
            out[bare.lower()] = symbol
    except Exception:
        pass
    return out


def extract_tickers_from_headline(headline: str) -> list[str]:
    """Return stock tickers and forex currency codes found in a headline.

    Matches: (1) Indian company names + bare NSE tickers (case-insensitive),
    (2) US watchlist tickers (upper-case whole-word), (3) forex codes.
    """
    found: list[str] = []
    hl_lower = headline.lower()

    # Indian company names + bare tickers (e.g. "Reliance", "HDFC Bank", "INFY")
    for needle, symbol in _india_name_map().items():
        # whole-token match to avoid false hits (e.g. "it" inside "wait")
        if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", hl_lower):
            found.append(symbol)

    # US watchlist tickers — exact upper-case word match
    words = set(re.findall(r"\b[A-Z]{2,6}\b", headline))
    for sym in settings.stock_symbols:
        bare = sym.replace(".NS", "").replace(".BO", "")
        if bare.upper() in words and sym not in found:
            found.append(sym)

    # Forex currency codes
    for code in _FOREX_CODES:
        if re.search(rf"\b{code}\b", headline, re.IGNORECASE):
            found.append(code)

    return list(dict.fromkeys(found))  # preserve order, deduplicate


# ═══════════════════════════════════════════════════════════════════════════════
# PART 2 — FinBERT Sentiment
# ═══════════════════════════════════════════════════════════════════════════════

@lru_cache(maxsize=1)
def _load_finbert_pipeline():
    """Load ProsusAI/finbert once per process, cached via lru_cache.

    transformers v5 uses lazy submodule loading via a ``LazyModule`` proxy.
    Inside the Celery prefork worker, that proxy occasionally loses its
    module-resolution state after the worker forks, producing
    ``ModuleNotFoundError: Could not import module 'pipeline'`` when the
    convenience ``from transformers import pipeline`` shim tries to resolve.
    Import from the explicit submodule path to bypass the lazy proxy.
    """
    try:
        try:
            from transformers.pipelines import pipeline as hf_pipeline
        except ImportError:
            # transformers < 5 fallback
            from transformers import pipeline as hf_pipeline
        logger.info(f"Loading FinBERT '{_FINBERT_MODEL}' — first call may take a moment")
        pipe = hf_pipeline(
            "text-classification",
            model=_FINBERT_MODEL,
            tokenizer=_FINBERT_MODEL,
            truncation=True,
            max_length=512,
        )
        logger.info("FinBERT loaded successfully")
        return pipe
    except Exception as exc:
        logger.warning(
            f"FinBERT unavailable ({exc.__class__.__name__}: {exc}) — using keyword fallback",
            exc_info=True,
        )
        return None


def _keyword_score(headline: str) -> dict:
    """Keyword heuristic fallback when torch/transformers are unavailable."""
    text   = headline.lower()
    words  = re.findall(r"\b\w+\b", text)
    n      = max(len(words), 1)

    pos_count = sum(1 for w in _POSITIVE_WORDS if w in text)
    neg_count = sum(1 for w in _NEGATIVE_WORDS if w in text)

    raw   = (pos_count - neg_count) / n
    score = max(-1.0, min(1.0, raw))

    if score > 0.01:
        sentiment = "positive"
    elif score < -0.01:
        sentiment = "negative"
    else:
        sentiment = "neutral"

    return {
        "sentiment":  sentiment,
        "score":      round(score, 4),
        "confidence": round(abs(score), 4),
    }


class SentimentAnalyser:
    """FinBERT-based financial sentiment analyser with keyword fallback.

    The model is loaded once at instantiation and shared across all calls
    via an lru_cache on the loader function.
    """

    def __init__(self) -> None:
        self._pipe = _load_finbert_pipeline()
        self._available = self._pipe is not None

    def analyse(self, headline: str) -> dict:
        """Score a single headline.

        Returns:
            sentiment   — 'positive' | 'negative' | 'neutral'
            score       — float in [-1, +1]
            confidence  — raw model probability [0, 1]

        Two accuracy guards are applied:
          1. Uncertainty pre-filter — wait-and-see headlines are forced to
             neutral before calling FinBERT (model mis-scores these as directional).
          2. Confidence threshold — calls below 60% confidence are returned as
             neutral; low-confidence predictions are worse than no prediction.
        """
        if not headline.strip():
            return {"sentiment": "neutral", "score": 0.0, "confidence": 0.0}

        if not self._available:
            return _keyword_score(headline)

        # Guard 0: Indian broker recommendation format — FinBERT mis-scores these
        broker_result = _india_broker_score(headline)
        if broker_result is not None:
            return broker_result

        # Guard 1: uncertainty/consolidation headlines → neutral
        if _is_uncertain(headline):
            return {"sentiment": "neutral", "score": 0.0, "confidence": 0.5}

        try:
            result     = self._pipe(headline[:512])[0]
            label      = result["label"].lower()
            confidence = float(result["score"])

            # Guard 2: low-confidence calls are unreliable
            if confidence < _CONFIDENCE_THRESHOLD:
                return {"sentiment": "neutral", "score": 0.0, "confidence": round(confidence, 4)}

            if label == "positive":
                score = confidence
            elif label == "negative":
                score = -confidence
            else:
                score = 0.0

            return {
                "sentiment":  label,
                "score":      round(score, 4),
                "confidence": round(confidence, 4),
            }
        except Exception as exc:
            logger.error(f"FinBERT inference failed: {exc}")
            return {"sentiment": "neutral", "score": 0.0, "confidence": 0.0}

    def analyse_batch(self, headlines: list[str]) -> list[dict]:
        """Score multiple headlines, processing up to 8 at a time.

        Applies the same two accuracy guards as analyse():
          - Uncertainty pre-filter runs before FinBERT to skip wait-and-see headlines.
          - Confidence threshold converts low-confidence results to neutral.

        Returns one result dict per input headline (same order).
        """
        if not headlines:
            return []

        if not self._available:
            return [_keyword_score(h) for h in headlines]

        results: list[dict] = []
        chunk_size = 8

        for i in range(0, len(headlines), chunk_size):
            chunk_headlines = headlines[i : i + chunk_size]

            # Pre-classify: slot each headline as pre-decided neutral or FinBERT-bound
            slot_results: list[dict | None] = []
            finbert_jobs: list[tuple[int, str]] = []  # (slot index, truncated text)

            for h in chunk_headlines:
                if not h.strip():
                    slot_results.append(
                        {"sentiment": "neutral", "score": 0.0, "confidence": 0.0}
                    )
                elif _india_broker_score(h) is not None:
                    slot_results.append(_india_broker_score(h))
                elif _is_uncertain(h):
                    slot_results.append(
                        {"sentiment": "neutral", "score": 0.0, "confidence": 0.5}
                    )
                else:
                    slot_results.append(None)
                    finbert_jobs.append((len(slot_results) - 1, h[:512]))

            # Run FinBERT only on the non-pre-decided headlines
            if finbert_jobs:
                try:
                    batch_out = self._pipe([text for _, text in finbert_jobs])
                    for (slot_idx, _), item in zip(finbert_jobs, batch_out):
                        label      = item["label"].lower()
                        confidence = float(item["score"])
                        if confidence < _CONFIDENCE_THRESHOLD:
                            slot_results[slot_idx] = {
                                "sentiment": "neutral",
                                "score":     0.0,
                                "confidence": round(confidence, 4),
                            }
                        else:
                            score = (
                                confidence  if label == "positive" else
                                -confidence if label == "negative" else 0.0
                            )
                            slot_results[slot_idx] = {
                                "sentiment":  label,
                                "score":      round(score, 4),
                                "confidence": round(confidence, 4),
                            }
                except Exception as exc:
                    logger.error(f"FinBERT batch inference failed: {exc}")
                    for slot_idx, _ in finbert_jobs:
                        slot_results[slot_idx] = {
                            "sentiment": "neutral", "score": 0.0, "confidence": 0.0
                        }

            results.extend(slot_results)  # type: ignore[arg-type]

        return results


# ═══════════════════════════════════════════════════════════════════════════════
# PART 3 — yfinance news (symbol-tagged, no key required)
# ═══════════════════════════════════════════════════════════════════════════════

_YF_MAX_SYMBOLS:    int = 60    # symbols to query per crawl cycle
_YF_MAX_PER_SYMBOL: int = 8     # articles to take per symbol
_YF_MAX_AGE_HOURS:  int = 168   # 7 days — Yahoo Finance NSE news is often 3-5 days old


async def _yf_news_symbols(session: AsyncSession, limit: int = _YF_MAX_SYMBOLS) -> list[str]:
    """Return the most Hub-relevant symbols for yfinance news fetching.

    Always includes WATCHLIST_NSE_LARGE_CAP (most Yahoo Finance coverage) plus
    the top-N market_shortlist symbols (Hub's active universe). Deduped, capped
    at `limit`.
    """
    from db.models import MarketShortlist
    from sqlalchemy import desc as _desc

    # Always include config large-caps and mid-caps (best Yahoo Finance coverage)
    seed = (
        [f"{s}.NS" for s in settings.WATCHLIST_NSE_LARGE_CAP] +
        [f"{s}.NS" for s in settings.WATCHLIST_NSE_MID_CAP]
    )

    # Supplement with live shortlist (hub-relevant universe)
    try:
        rows = (await session.execute(
            select(MarketShortlist.symbol)
            .order_by(_desc(MarketShortlist.master_score))
            .limit(limit)
        )).scalars().all()
        seed.extend(rows)
    except Exception as exc:
        logger.debug(f"[yfinance_news] market_shortlist query failed: {exc}")

    # Dedup preserving order, cap at limit
    seen: set[str] = set()
    out: list[str] = []
    for s in seed:
        if s not in seen:
            seen.add(s)
            out.append(s)
        if len(out) >= limit:
            break
    return out


def _parse_yf_news_item(raw: dict, queried_sym: str, cutoff: datetime) -> dict | None:
    """Parse one yfinance news dict — handles both the old flat format (≤0.2.x)
    and the new nested-content format (≥1.3.0).

    Returns a crawl-row dict or None if the item should be skipped.
    """
    # yfinance ≥1.3 wraps everything under a "content" key.
    content = raw.get("content") or raw

    title = (content.get("title") or "").strip()
    if not title:
        return None

    # Publisher ---------------------------------------------------------------
    provider = content.get("provider") or {}
    publisher = (
        provider.get("displayName")
        or raw.get("publisher")
        or "Yahoo Finance"
    )

    # URL ---------------------------------------------------------------------
    canonical = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
    url = canonical.get("url") or raw.get("link")

    # Published-at ------------------------------------------------------------
    pub_dt: datetime | None = None
    pub_str = content.get("pubDate") or content.get("displayTime")
    if pub_str:
        try:
            pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass
    if pub_dt is None:
        # Old format: Unix timestamp
        ts = raw.get("providerPublishTime")
        if ts:
            try:
                pub_dt = datetime.utcfromtimestamp(ts)
            except Exception:
                pass

    if pub_dt and pub_dt < cutoff:
        return None  # too old

    # Related tickers (old format only — new format dropped this field) -------
    related = [
        r if ("." in r or r.startswith("^") or "=" in r) else f"{r}.NS"
        for r in (raw.get("relatedTickers") or [])
    ]
    tickers_affected = list(dict.fromkeys([queried_sym, *related]))

    return {
        "headline":         title,
        "source":           f"yf:{publisher[:30]}",
        "url":              url,
        "published_at":     pub_dt,
        "tickers_affected": tickers_affected,
    }


async def fetch_yfinance_news(symbols: list[str]) -> list[dict]:
    """Fetch recent news headlines from Yahoo Finance for a batch of NSE symbols.

    Unlike RSS/Finnhub/NewsAPI, every article is **pre-tagged** to the
    symbol it was fetched for — no ticker extraction needed.

    Runs yfinance synchronously in a thread-pool executor so the event loop
    stays unblocked. A per-symbol exception never kills the batch.
    """
    if not symbols:
        return []

    cutoff = datetime.utcnow() - timedelta(hours=_YF_MAX_AGE_HOURS)

    def _fetch_all(syms: list[str]) -> list[dict]:
        import yfinance as yf
        rows: list[dict] = []
        for sym in syms:
            try:
                news_list = yf.Ticker(sym).news or []
                for raw in news_list[:_YF_MAX_PER_SYMBOL]:
                    parsed = _parse_yf_news_item(raw, sym, cutoff)
                    if parsed:
                        rows.append(parsed)
            except Exception as exc:
                logger.debug(f"[yfinance_news] {sym}: {exc}")
        return rows

    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, _fetch_all, symbols)
    if rows:
        logger.info(f"yfinance ✓  {len(rows)} headlines  symbols_queried={len(symbols)}")
    return rows


# ═══════════════════════════════════════════════════════════════════════════════
# PART 4 — Main crawl function
# ═══════════════════════════════════════════════════════════════════════════════

async def run_news_crawl(session: AsyncSession) -> dict:
    """Fetch news from all sources, score sentiment, and persist to DB.

    Deduplicates within the batch (by URL) and against the DB before inserting.

    Returns:
        total_fetched   — raw headline count across all sources
        total_saved     — new rows inserted into news_items
        sources         — per-source headline counts
        errors          — list of "<source>: <reason>" strings
    """
    errors: list[str] = []

    # ── Pre-warm the India name → NSE symbol map from kite_instruments ────────
    # Cheap (single SELECT, TTL-cached for 6h); ensures ticker extraction can
    # tag mid- and small-caps (NMDC, ZEEL, BHEL, INDIGO, …) and not just the
    # ~59 names in NSE_STOCK_LOOKUP.
    await _build_india_name_map(session)

    # ── Identify symbols for yfinance (DB shortlist — async, must run first) ────
    yf_symbols = await _yf_news_symbols(session)

    # ── Fetch from all sources in parallel ───────────────────────────────────
    newsapi_rows, finnhub_rows, newsdata_rows, rss_rows, yf_rows = await asyncio.gather(
        fetch_newsapi_headlines(),
        fetch_finnhub_news(),
        fetch_newsdata_india(),
        fetch_free_rss_news(),
        fetch_yfinance_news(yf_symbols),
        return_exceptions=True,
    )

    def _unwrap(name: str, result) -> list[dict]:
        if isinstance(result, Exception):
            errors.append(f"{name}: {result}")
            logger.error(f"News source {name} raised: {result}")
            return []
        return result  # type: ignore[return-value]

    newsapi_rows  = _unwrap("newsapi",   newsapi_rows)
    finnhub_rows  = _unwrap("finnhub",   finnhub_rows)
    newsdata_rows = _unwrap("newsdata",  newsdata_rows)
    rss_rows      = _unwrap("rss",       rss_rows)
    yf_rows       = _unwrap("yfinance",  yf_rows)

    source_counts = {
        "newsapi":   len(newsapi_rows),
        "finnhub":   len(finnhub_rows),
        "newsdata":  len(newsdata_rows),
        "rss":       len(rss_rows),
        "yfinance":  len(yf_rows),
    }

    # ── Per-source health check ───────────────────────────────────────────────
    # Only flag sources that actually have a configured key (or RSS, which has
    # no key gate). NewsAPI/Finnhub/NewsData empty when their key is unset is
    # expected, not an outage.
    _expects_data = {
        "rss":      True,
        "yfinance": True,          # no key required — always expected
        "finnhub":  bool(getattr(settings, "FINNHUB_KEY", "")),
        "newsdata": bool(getattr(settings, "NEWSDATA_KEY", "")),
        "newsapi":  bool(getattr(settings, "NEWSAPI_KEY", "")),
    }
    for src, count in source_counts.items():
        if not _expects_data.get(src, False):
            _SOURCE_ZERO_STREAK[src] = 0
            continue
        if count == 0:
            _SOURCE_ZERO_STREAK[src] += 1
            if _SOURCE_ZERO_STREAK[src] == _SOURCE_FAIL_THRESHOLD:
                logger.error(
                    f"[news_crawler] source '{src}' has returned 0 rows for "
                    f"{_SOURCE_FAIL_THRESHOLD} consecutive cycles — likely upstream outage"
                )
        else:
            _SOURCE_ZERO_STREAK[src] = 0

    # RSS + yfinance first (India-first, symbol-tagged); then NewsData, NewsAPI, Finnhub
    all_raw: list[dict] = rss_rows + yf_rows + newsdata_rows + newsapi_rows + finnhub_rows
    total_fetched = len(all_raw)

    # Normalize headlines: RSS feeds often double-escape (`&amp;amp;`), Finnhub
    # passes through raw HTML entities, NewsData.io occasionally returns
    # smart-quotes as numeric refs. Two passes of html.unescape() handle the
    # common double-escape case; strip() trims feed-leading whitespace.
    for item in all_raw:
        h = item.get("headline") or ""
        item["headline"] = html.unescape(html.unescape(h)).strip()

    # ── Deduplicate within batch by URL ───────────────────────────────────────
    seen_urls: set[str] = set()
    deduped: list[dict] = []
    for item in all_raw:
        url = item.get("url") or ""
        key = url.strip().lower()
        if key and key in seen_urls:
            continue
        if key:
            seen_urls.add(key)
        deduped.append(item)

    # ── Filter against DB (skip already-saved URLs) ───────────────────────────
    batch_urls = [item["url"] for item in deduped if item.get("url")]
    if batch_urls:
        existing_result = await session.execute(
            select(NewsItem.url).where(NewsItem.url.in_(batch_urls))
        )
        existing_urls = set(existing_result.scalars().all())
    else:
        existing_urls = set()

    new_items = [
        item for item in deduped
        if item.get("url") not in existing_urls
    ]

    if not new_items:
        logger.info(
            f"━━ News crawl DONE ━━  fetched={total_fetched}  "
            f"saved=0 (all duplicates)  sources={source_counts}"
        )
        return {
            "total_fetched": total_fetched,
            "total_saved":   0,
            "sources":       source_counts,
            "errors":        errors,
        }

    # ── Run sentiment in one batch ────────────────────────────────────────────
    analyser   = SentimentAnalyser()
    headlines  = [item["headline"] for item in new_items]
    sentiments = analyser.analyse_batch(headlines)

    # ── Persist to DB ─────────────────────────────────────────────────────────
    total_saved = 0
    broadcast_payloads: list[dict] = []
    for item, sent in zip(new_items, sentiments):
        # yfinance items carry pre-tagged tickers — no extraction needed.
        # RSS/NewsAPI/Finnhub items get tickers extracted from the headline text.
        tickers = (
            item["tickers_affected"]
            if item.get("tickers_affected")
            else extract_tickers_from_headline(item["headline"])
        )
        row = NewsItem(
            headline=item["headline"],
            source=item["source"],
            url=item.get("url"),
            sentiment=sent["sentiment"],
            score=sent["score"],
            tickers_affected=tickers or None,
            published_at=item.get("published_at"),
        )
        session.add(row)
        total_saved += 1
        # Payload mirrors NewsItemOut so the WS listener can drop straight
        # into the same React state shape that GET /api/v1/news/ returns.
        broadcast_payloads.append({
            "type":             "news_item",
            "headline":         item["headline"],
            "source":           item["source"],
            "url":              item.get("url"),
            "sentiment":        sent["sentiment"],
            "score":            sent["score"],
            "tickers_affected": tickers,
            "published_at": (
                item["published_at"].isoformat()
                if item.get("published_at") else None
            ),
        })

    await session.flush()

    # Push each new headline to any connected WebSocket subscribers so the
    # frontend doesn't have to poll /api/v1/news/ every few seconds. Fire-
    # and-forget — a broadcast failure must not block the crawl persistence.
    if broadcast_payloads:
        try:
            from api.websocket import live_price_manager
            for payload in broadcast_payloads:
                await live_price_manager.broadcast_event(payload)
        except Exception as exc:
            logger.debug(f"[news_crawler] WS broadcast skipped: {exc}")

    logger.info(
        f"━━ News crawl DONE ━━  fetched={total_fetched}  "
        f"saved={total_saved}  sources={source_counts}  errors={len(errors)}"
    )
    return {
        "total_fetched": total_fetched,
        "total_saved":   total_saved,
        "sources":       source_counts,
        "errors":        errors,
    }


async def get_market_sentiment(
    symbol: str,
    session: AsyncSession,
    bar_date: datetime | None = None,
) -> float:
    """Return the average sentiment score for the last N news items mentioning symbol.

    Primary lookup: PostgreSQL JSON containment on ``tickers_affected``. The
    column stores full NSE symbols (``["INFY.NS", ...]``) from
    :func:`extract_tickers_from_headline`, and the input ``symbol`` is the same
    form (``"INFY.NS"``), so we query the JSON directly with ``@>``. The cast
    works on both ``json`` and ``jsonb`` columns — no migration required for
    callers, and once the column is upgraded to ``jsonb`` with a GIN index the
    same query becomes index-backed.

    Fallback: headline text search via ``ilike`` — only used when the primary
    lookup is empty (covers ADRs, forex codes, and any ticker the
    kite_instruments map doesn't know about).

    When bar_date is provided (backtest mode) only news crawled on or before
    that date is considered, eliminating look-ahead bias.

    Returns 0.0 when no relevant news is found.
    """
    sym = (symbol or "").strip()
    if not sym:
        return 0.0

    # JSON containment — the same query against jsonb when the column is
    # ALTER'd, or a per-row cast when it's still json. PostgreSQL accepts
    # both via the ::jsonb cast on the bind parameter.
    payload = f'["{sym}"]'

    date_filter = [text("tickers_affected::jsonb @> :payload ::jsonb").bindparams(payload=payload)]
    if bar_date is not None:
        date_filter.append(NewsItem.crawled_at <= bar_date)

    result = await session.execute(
        select(NewsItem.score)
        .where(*date_filter)
        .order_by(NewsItem.crawled_at.desc())
        .limit(_SENTIMENT_WINDOW)
    )
    scores: list[float] = [s for s in result.scalars().all() if s is not None]

    # Fallback for tickers not in kite_instruments (ADRs, forex, indices).
    # Strip exchange suffix so e.g. "RELIANCE.NS" → "RELIANCE" before the
    # whole-word substring match, otherwise the suffix never appears in the
    # raw headline text and the fallback always misses.
    if not scores:
        bare = sym.replace(".NS", "").replace(".BO", "")
        bare_filter = [NewsItem.headline.ilike(f"%{bare}%")]
        if bar_date is not None:
            bare_filter.append(NewsItem.crawled_at <= bar_date)
        result2 = await session.execute(
            select(NewsItem.score)
            .where(*bare_filter)
            .order_by(NewsItem.crawled_at.desc())
            .limit(_SENTIMENT_WINDOW)
        )
        scores = [s for s in result2.scalars().all() if s is not None]

    if not scores:
        return 0.0

    avg = sum(scores) / len(scores)
    logger.debug(f"Market sentiment  {sym}  n={len(scores)}  avg={avg:+.4f}")
    return round(avg, 4)
