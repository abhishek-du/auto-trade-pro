"""ONE canonical symbol normaliser. Suffix-aware, idempotent, auditable.

WHY THIS EXISTS
---------------
Two measured production defects share one root cause: code that appends an
exchange suffix without checking whether the symbol already has one.

  * engine/direct_news_strategy.py built f"{ticker}.NS" from a ticker that
    already read "GKENERGY.NS", producing "GKENERGY.NS.NS". That symbol has no
    candles, so hist_df came back None and the WHOLE technical block behind
    `if hist_df is not None` was skipped -- silently disabling the 20-EMA trend
    filter and the volume-confirmation filter for every DIRECT_NEWS trade.

  * 807 .BO symbols carry no intraday data while an NSE twin exists. On
    2026-08-27, 29 of 136 LLM evaluations were .BO symbols that were
    structurally incapable of price/volume validation.

THE RULES
---------
1. A symbol that already ends in a known suffix is never given a second one.
2. A .BO symbol whose NSE twin exists in kite_instruments resolves to .NS.
3. A genuinely BSE-only symbol keeps .BO.
4. normalize(normalize(s)) == normalize(s) -- idempotent by construction.
5. The original is always preserved for audit; nothing is silently rewritten.

WHAT THIS IS NOT
----------------
It does not change the universe, does not delete BSE symbols, and does not
decide what is tradeable. It answers exactly one question: "which symbol string
should I use to look this instrument up?"
"""
from __future__ import annotations

from dataclasses import dataclass

NSE = ".NS"
BSE = ".BO"
_SUFFIXES = (NSE, BSE)

# Index/FX style symbols that must never be given an equity suffix.
_NON_EQUITY_PREFIXES = ("^",)
_NON_EQUITY_MARKERS = ("=",)


@dataclass(frozen=True)
class SymbolResolution:
    """The full audit record, not just the answer."""

    source_symbol: str            # exactly what came in
    canonical_trade_symbol: str   # what to look up / trade
    exchange_resolution: str      # NSE | BSE | NON_EQUITY | UNKNOWN
    resolution_reason: str        # why, in words

    @property
    def bare(self) -> str:
        return strip_suffix(self.canonical_trade_symbol)

    def as_dict(self) -> dict:
        return {
            "source_symbol": self.source_symbol,
            "canonical_trade_symbol": self.canonical_trade_symbol,
            "exchange_resolution": self.exchange_resolution,
            "resolution_reason": self.resolution_reason,
        }


def strip_suffix(symbol: str) -> str:
    """'GKENERGY.NS' -> 'GKENERGY'. Removes REPEATED suffixes.

    The repeat loop is not defensive padding: 'GKENERGY.NS.NS' is a string this
    codebase has actually produced and written into a candle query.
    """
    s = (symbol or "").strip().upper()
    changed = True
    while changed:
        changed = False
        for suf in _SUFFIXES:
            if s.endswith(suf):
                s = s[: -len(suf)]
                changed = True
    return s


def has_suffix(symbol: str) -> bool:
    return (symbol or "").strip().upper().endswith(_SUFFIXES)


def is_non_equity(symbol: str) -> bool:
    """Index (^NSEI) and FX (USDINR=X) symbols take no equity suffix."""
    s = (symbol or "").strip()
    return s.startswith(_NON_EQUITY_PREFIXES) or any(m in s for m in _NON_EQUITY_MARKERS)


def normalize(symbol: str, *, default: str = NSE) -> str:
    """Suffix-safe normalisation with NO database access.

    Use this everywhere a suffix is currently appended with an f-string. It
    cannot produce a double suffix and it leaves an existing suffix alone.
    Exchange RESOLUTION (.BO -> .NS) needs the instrument table; see resolve().
    """
    s = (symbol or "").strip().upper()
    if not s:
        return s
    if is_non_equity(s):
        return s
    bare = strip_suffix(s)
    if not bare:
        return s
    # Preserve an explicitly stated exchange; only supply one when absent.
    for suf in _SUFFIXES:
        if s.endswith(suf):
            return f"{bare}{suf}"
    return f"{bare}{default}"


async def resolve(symbol: str, session=None) -> SymbolResolution:
    """Full resolution, including the .BO -> .NS twin lookup.

    Fails OPEN: if the instrument table is unreachable the symbol keeps whatever
    exchange it arrived with. A lookup failure must never silently move a trade
    to a different exchange.
    """
    src = (symbol or "").strip()
    s = src.upper()

    if not s:
        return SymbolResolution(src, s, "UNKNOWN", "empty symbol")

    if is_non_equity(s):
        return SymbolResolution(src, s, "NON_EQUITY", "index or FX symbol; no suffix applied")

    bare = strip_suffix(s)
    had_double = s.count(".NS") + s.count(".BO") > 1

    if not s.endswith(BSE):
        canon = normalize(s)
        reason = ("repaired duplicate suffix" if had_double
                  else "already NSE" if s.endswith(NSE)
                  else "no suffix present; defaulted to NSE")
        return SymbolResolution(src, canon, "NSE", reason)

    # .BO -- does an NSE twin exist?
    try:
        from sqlalchemy import text

        async def _lookup(sess):
            r = await sess.execute(
                text("SELECT 1 FROM kite_instruments "
                     "WHERE tradingsymbol = :s AND exchange = 'NSE' LIMIT 1"),
                {"s": bare},
            )
            return r.first() is not None

        if session is not None:
            twin = await _lookup(session)
        else:
            from db.database import AsyncSessionLocal

            async with AsyncSessionLocal() as own:
                twin = await _lookup(own)
    except Exception as exc:
        return SymbolResolution(
            src, f"{bare}{BSE}", "BSE",
            f"twin lookup unavailable ({type(exc).__name__}); kept BSE",
        )

    if twin:
        return SymbolResolution(
            src, f"{bare}{NSE}", "NSE",
            "dual-listed; resolved to the NSE twin (BSE has no intraday data here)",
        )
    return SymbolResolution(src, f"{bare}{BSE}", "BSE", "BSE-only listing; kept BSE")

# ─────────────────────────────────────────────────────────────────────────────
# NSE-ONLY EXCHANGE GATE (Step 2A, 2026-08-28)
# ─────────────────────────────────────────────────────────────────────────────
#
# THE INVARIANT: no BSE instrument may enter an ACTIVE path.
#
# "Active" means anything that can become a price request, a candidate, a
# signal, an order or a position. Historical and audit rows are untouched --
# 5 closed .BO paper_trades and 7 .BO tactical_signals remain queryable, and
# deleting them would rewrite forensic history.
#
# WHY THIS EXISTS. BSE was purged from kite_instruments and hub_universe on
# 2026-08-27, and NSE_ONLY_UNIVERSE=True was added. Neither stopped BSE:
#
#   * crawler/india_price_feed.py forced 45 hardcoded .BO symbols into every
#     crawl as "mandatory", producing 4,659 fresh .BO candle rows in two days
#     AFTER the purge.
#   * 474 .BO items sit in premarket_news_queue, 109 inside the drain window.
#   * decision_router, risk_manager and execution only STRIP the suffix. None
#     rejects on exchange, and 5 .BO trades have already executed.
#
# SCOPE BOUNDARY, stated precisely: this gate enforces EXCHANGE safety only.
# It deliberately does NOT decide whether an NSE symbol is a tradeable EQUITY.
# hub_universe still contains InvITs (-IV), rights entitlements (-RR), SME
# (-SM), trade-to-trade (-BE/-BZ/-ST). Excluding those is the master-equity
# redesign (Step 2B) and is out of scope here. A -RR symbol passes this gate.
#
# FAIL-CLOSED. Every ambiguity rejects. A false negative (refusing a
# questionable symbol) costs one missed candidate; a false positive puts a BSE
# instrument into the trading path.

class ExchangeRejection:
    """Telemetry reasons. Stable strings -- they are aggregated in counters."""

    BSE_SYMBOL = "BSE_SYMBOL"
    UNKNOWN_EXCHANGE = "UNKNOWN_EXCHANGE"
    NON_EQUITY_INSTRUMENT = "NON_EQUITY_INSTRUMENT"
    UNRESOLVED_BARE_SYMBOL = "UNRESOLVED_BARE_SYMBOL"
    AMBIGUOUS_IDENTITY = "AMBIGUOUS_IDENTITY"
    EMPTY_SYMBOL = "EMPTY_SYMBOL"


# Exchange-qualified prefixes seen in this codebase ("NSE:RELIANCE" from Kite,
# "BSE:RELIANCE" from the live-price cache).
_PREFIX_NSE = "NSE:"
_PREFIX_BSE = "BSE:"


def nse_gate(symbol: str | None, *, allow_bare: bool = False) -> tuple[bool, str | None]:
    """Exchange-safety gate. Returns (allowed, rejection_reason).

    SYNCHRONOUS and database-free, so it is safe on per-tick paths.

    `allow_bare=False` (the default) rejects a suffix-less symbol, because
    "RELIANCE" does not say which exchange it means and normalize() would
    silently make it NSE. A caller that has ALREADY resolved the symbol through
    the authoritative resolver may pass allow_bare=True.
    """
    if symbol is None:
        return False, ExchangeRejection.EMPTY_SYMBOL
    s = str(symbol).strip().upper()
    if not s:
        return False, ExchangeRejection.EMPTY_SYMBOL

    # Exchange-qualified forms resolve on their prefix, before suffix logic.
    if s.startswith(_PREFIX_BSE):
        return False, ExchangeRejection.BSE_SYMBOL
    if s.startswith(_PREFIX_NSE):
        return True, None

    # Indices (^NSEI), FX (USDINR=X), commodities (GC=F) are not equities and
    # must never reach a candidate path, whatever their exchange.
    if is_non_equity(s):
        return False, ExchangeRejection.NON_EQUITY_INSTRUMENT

    if s.endswith(BSE):
        return False, ExchangeRejection.BSE_SYMBOL
    if s.endswith(NSE):
        # A repaired double suffix is still NSE; strip_suffix handles it.
        return True, None

    # Some other dotted suffix -- .BS, .NSE, anything unrecognised.
    if "." in s:
        return False, ExchangeRejection.UNKNOWN_EXCHANGE

    if allow_bare:
        return True, None
    return False, ExchangeRejection.UNRESOLVED_BARE_SYMBOL


def is_nse_tradeable(symbol: str | None, *, allow_bare: bool = False) -> bool:
    """Boolean form of nse_gate() for filter expressions."""
    return nse_gate(symbol, allow_bare=allow_bare)[0]


def filter_nse_only(symbols, *, allow_bare: bool = False) -> tuple[list, dict]:
    """Split an iterable into (kept, {reason: count}).

    Counters rather than per-symbol logs: the price feed runs on a 30-second
    cadence over ~1,500 symbols, and a line each would be ~700k lines a day.
    """
    kept, rejected = [], {}
    for sym in symbols or []:
        ok, reason = nse_gate(sym, allow_bare=allow_bare)
        if ok:
            kept.append(sym)
        else:
            rejected[reason] = rejected.get(reason, 0) + 1
    return kept, rejected


async def resolve_nse_tradeable(symbol: str | None, session=None) -> tuple[bool, str | None, str | None]:
    """Async gate that CAN accept a bare symbol, by resolving it authoritatively.

    Returns (allowed, canonical_symbol, rejection_reason).

    A bare symbol is admitted only if utils.identity resolves it to exactly one
    NSE instrument. Ambiguity rejects -- never a guess. Everything else defers
    to the synchronous gate.
    """
    ok, reason = nse_gate(symbol, allow_bare=False)
    if ok:
        return True, normalize(symbol), None
    if reason != ExchangeRejection.UNRESOLVED_BARE_SYMBOL:
        return False, None, reason

    try:
        from utils.identity import build_index, resolve_identity

        idx = await build_index(session)
        r = resolve_identity(str(symbol), idx)
        if r.ok:
            return True, r.symbol, None
        if r.needs_review:
            return False, None, ExchangeRejection.AMBIGUOUS_IDENTITY
        return False, None, ExchangeRejection.UNRESOLVED_BARE_SYMBOL
    except Exception:
        # Fail CLOSED: an unreachable instrument table must not admit a symbol
        # whose exchange we cannot establish.
        return False, None, ExchangeRejection.UNRESOLVED_BARE_SYMBOL

