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
