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
import time
from datetime import datetime
from functools import lru_cache

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import NewsItem
from utils.config import settings
from utils.logger import logger

# ── Constants ─────────────────────────────────────────────────────────────────

_NEWSAPI_BASE  = "https://newsapi.org/v2/everything"
_FINNHUB_BASE  = "https://finnhub.io/api/v1"
# India-first RSS feeds (no key, no rate limit). Empty/blocked feeds are
# skipped gracefully by fetch_free_rss_news(). Moneycontrol, Business Standard
# and Mint reliably return market headlines; ET is best-effort.
_RSS_FEEDS     = [
    "https://www.moneycontrol.com/rss/latestnews.xml",
    "https://www.business-standard.com/rss/markets-106.rss",
    "https://www.livemint.com/rss/markets",
    "https://economictimes.indiatimes.com/markets/rss.cms",
]
_FOREX_CODES   = {"EUR", "USD", "GBP", "JPY", "AUD", "CHF", "CAD"}
_FINBERT_MODEL = "ProsusAI/finbert"

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
        logger.warning("NEWSAPI_KEY not configured — skipping NewsAPI fetch")
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
            feed = feedparser.parse(url)
            source = feed.feed.get("title", url.split("/")[2])
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
    """Load ProsusAI/finbert once per process, cached via lru_cache."""
    try:
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
        logger.warning(f"FinBERT unavailable ({exc}) — using keyword fallback")
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
# PART 3 — Main crawl function
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

    # ── Fetch from all sources ────────────────────────────────────────────────
    newsapi_rows, finnhub_rows, newsdata_rows, rss_rows = await asyncio.gather(
        fetch_newsapi_headlines(),
        fetch_finnhub_news(),
        fetch_newsdata_india(),
        fetch_free_rss_news(),
        return_exceptions=True,
    )

    def _unwrap(name: str, result) -> list[dict]:
        if isinstance(result, Exception):
            errors.append(f"{name}: {result}")
            logger.error(f"News source {name} raised: {result}")
            return []
        return result  # type: ignore[return-value]

    newsapi_rows  = _unwrap("newsapi",  newsapi_rows)
    finnhub_rows  = _unwrap("finnhub",  finnhub_rows)
    newsdata_rows = _unwrap("newsdata", newsdata_rows)
    rss_rows      = _unwrap("rss",      rss_rows)

    source_counts = {
        "newsapi":  len(newsapi_rows),
        "finnhub":  len(finnhub_rows),
        "newsdata": len(newsdata_rows),
        "rss":      len(rss_rows),
    }

    # RSS first — India-first priority; then NewsData, NewsAPI, Finnhub
    all_raw: list[dict] = rss_rows + newsdata_rows + newsapi_rows + finnhub_rows
    total_fetched = len(all_raw)

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
    for item, sent in zip(new_items, sentiments):
        tickers = extract_tickers_from_headline(item["headline"])
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

    await session.flush()

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


async def get_market_sentiment(symbol: str, session: AsyncSession) -> float:
    """Return the average sentiment score for the last 10 news items mentioning symbol.

    Checks both tickers_affected (JSON array) and the raw headline text.
    Returns 0.0 when no relevant news is found.
    """
    # Fetch a recent window and filter in Python — avoids JSON operator dialect differences
    result = await session.execute(
        select(NewsItem)
        .where(NewsItem.tickers_affected.isnot(None))
        .order_by(NewsItem.crawled_at.desc())
        .limit(200)
    )
    candidates = result.scalars().all()

    scores: list[float] = []
    for news in candidates:
        if symbol in (news.tickers_affected or []):
            scores.append(news.score)
        if len(scores) >= 10:
            break

    if not scores:
        # Fallback: headline text search in the same recent window
        result2 = await session.execute(
            select(NewsItem)
            .order_by(NewsItem.crawled_at.desc())
            .limit(200)
        )
        for news in result2.scalars().all():
            if re.search(rf"\b{re.escape(symbol)}\b", news.headline, re.IGNORECASE):
                scores.append(news.score)
            if len(scores) >= 10:
                break

    if not scores:
        return 0.0

    avg = sum(scores) / len(scores)
    logger.debug(
        f"Market sentiment  {symbol}  n={len(scores)}  avg={avg:+.4f}"
    )
    return round(avg, 4)
