"""Deterministic instrument identity resolution. Never guesses.

THE PROBLEM THIS SOLVES
-----------------------
Measured 2026-08-27 over 7 days of causal_events: of 220 distinct symbols the
classifier emitted, only 111 (50.5%) resolved to an NSE instrument. Every one
of the other 109 was a REAL, liquid NSE stock under a different identifier:

    event says                    NSE uses
    ADANITOTALGAS                 ATGL
    ADITYABIRLACAPITAL            ABCAPITAL
    BANK OF MAHARASHTRA           MAHABANK
    BHARAT ELECTRONICS LIMITED    BEL
    BALRAMPUR CHINI               BALRAMCHIN
    DATA PATTERNS                 DATAPATTNS
    CAPRI GLOBAL CAPITAL          CGCL

Two failure modes: company NAMES in a ticker field, and plausible-but-wrong
tickers. An event that cannot be resolved cannot become a trade -- no candles,
no price, no validation -- so roughly half of correctly-classified events were
unreachable. The classification was never the problem.

WHY THIS IS NOT FUZZY MATCHING
------------------------------
Every rule here is exact and reproducible. There is no edit distance, no
similarity score, no "closest match". The ladder is tried in confidence order
and STOPS at the first tier that yields exactly one candidate:

    1 EXACT_SYMBOL   the string is already a tradingsymbol
    2 ALIAS          a curated, human-reviewed mapping
    3 EXACT_NAME     normalised legal name matches exactly
    4 UNIQUE_PREFIX  exactly one legal name starts with the key
                     (covers 'BALRAMPUR CHINI' -> 'BALRAMPUR CHINI MILLS')

Anything that matches MORE than one instrument returns AMBIGUOUS and is
REJECTED, with every candidate recorded for review. Anything that matches none
returns UNRESOLVED. Neither ever produces a symbol.

A wrong resolution is worse than no resolution: it trades the wrong company on
another company's news. So the failure mode is always "refuse", never "guess".

WHAT IS DELIBERATELY EXCLUDED
-----------------------------
Bonds, SGBs, government securities, ETF INAV feeds and mutual-fund series are
filtered out of the candidate pool. 1,669 NSE instruments carry no name at all
and can never be name-resolved; they remain reachable by exact symbol only.
Of 8,264 named tradeable equities, 351 normalised names are ambiguous -- those
are exactly the cases this module refuses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from utils.symbols import normalize, strip_suffix


class Resolution(str, Enum):
    EXACT_SYMBOL = "EXACT_SYMBOL"
    ALIAS = "ALIAS"
    EXACT_NAME = "EXACT_NAME"
    UNIQUE_PREFIX = "UNIQUE_PREFIX"
    ALL_TOKENS = "ALL_TOKENS"
    AMBIGUOUS = "AMBIGUOUS"        # >1 candidate -> REJECT, needs review
    UNRESOLVED = "UNRESOLVED"      # 0 candidates -> REJECT
    INVALID = "INVALID"            # empty / unusable input


# Tiers that produce a usable symbol. Everything else is a refusal.
_RESOLVED = (Resolution.EXACT_SYMBOL, Resolution.ALIAS,
             Resolution.EXACT_NAME, Resolution.UNIQUE_PREFIX, Resolution.ALL_TOKENS)

# Words that carry no identifying information in an Indian listed-company name.
# Removing them stops "INDIA" or "LIMITED" from doing any matching work.
_STOPWORDS = frozenset({
    "LIMITED", "LTD", "INDIA", "THE", "AND", "CO", "COMPANY", "PVT", "PRIVATE",
    "INC", "CORP", "CORPORATION", "PLC",
})

# Curated aliases. Hand-verified only; this file is the review queue's output,
# never a place to dump guesses. Keys are normalised by _key().
_ALIASES: dict[str, str] = {
    # Common press/wire forms that are not the legal name and not the ticker.
    "BANKOFBARODA": "BANKBARODA",
    "STATEBANKOFINDIA": "SBIN",
    "LARSENTOUBRO": "LT",
    "LARSENANDTOUBRO": "LT",
    "HDFCBANKLTD": "HDFCBANK",
    "TATACONSULTANCYSERVICES": "TCS",
    "OILANDNATURALGASCORPORATION": "ONGC",
    "OILNATURALGASCORPORATION": "ONGC",
    "POWERGRIDCORPORATIONOFINDIA": "POWERGRID",
    "BHARATHEAVYELECTRICALS": "BHEL",
    "HINDUSTANAERONAUTICS": "HAL",
    "LIFEINSURANCECORPORATIONOFINDIA": "LICI",
    # Short forms the wires use that are too brief for prefix matching (the
    # prefix tier needs >=6 chars to be discriminating). Each hand-verified
    # against kite_instruments on 2026-08-27.
    "LIC": "LICI",
    "SBI": "SBIN",
    "AKCAPFIN": "AKCAPIT",
    "LALITHAJEWELLERY": "LALITHAA",
    "AHLUWALIACONTRACTSINDIA": "AHLUCONT",
    "AHLUWALIACONTRACTS": "AHLUCONT",
}

# A DELIBERATE NON-FEATURE: first-token matching.
#
# Measured 2026-08-27: keying on a company name's first token (>=7 chars) gives
# 999 unique tokens out of 1,149 -- but it rescued only ONE of five real
# unresolved cases (AHLUWALIA CONTRACTS) while introducing a collision class
# where two unrelated companies share a first word. The single case it rescued
# is now a curated alias above, which is exact.
#
# Unresolved names are routed to review, not guessed. That is the trade this
# module exists to make.

# Corporate suffixes stripped before comparison. Order matters: longest first,
# and they are removed repeatedly so "X LIMITED LTD" collapses correctly.
_SUFFIX_WORDS = ("PRIVATELIMITED", "LIMITED", "LTD", "PVTLTD", "PLC", "CORP",
                 "CORPORATION", "INCORPORATED", "INC", "COMPANY")

# Non-equity instrument patterns excluded from the name-candidate pool.
_NON_EQUITY_SYMBOL = re.compile(
    r"-(N[0-9A-Z]|SF|GB|RL|GS|Y[0-9A-Z]|Z[0-9A-Z]|A[A-Z]|B[A-Z]|D[0-9])$")


@dataclass(frozen=True)
class IdentityResult:
    """Full audit record. `symbol` is None unless resolution succeeded."""

    source: str
    symbol: str | None
    resolution: Resolution
    reason: str
    candidates: tuple = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.resolution in _RESOLVED and self.symbol is not None

    @property
    def needs_review(self) -> bool:
        """Ambiguity is a data-quality signal, not a dead end."""
        return self.resolution is Resolution.AMBIGUOUS

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "canonical_symbol": self.symbol,
            "resolution": self.resolution.value,
            "reason": self.reason,
            "candidates": list(self.candidates),
        }


def _key(text: str) -> str:
    """Normalise to a comparison key: uppercase alphanumerics, no corporate
    suffix. 'Bharat Electronics Limited' and 'BHARAT ELECTRONICS' agree."""
    k = re.sub(r"[^A-Za-z0-9]", "", (text or "")).upper()
    changed = True
    while changed:
        changed = False
        for suf in _SUFFIX_WORDS:
            if k.endswith(suf) and len(k) > len(suf) + 2:
                k = k[: -len(suf)]
                changed = True
    return k


def _tokens(text: str) -> frozenset:
    """Significant, identifying tokens of a company name."""
    raw = re.split(r"[^A-Za-z0-9]+", (text or "").upper())
    return frozenset(t for t in raw if len(t) >= 3 and t not in _STOPWORDS)


def is_non_equity_symbol(tradingsymbol: str) -> bool:
    """Bonds, SGBs, G-secs, INAV feeds — never event candidates."""
    return bool(_NON_EQUITY_SYMBOL.search((tradingsymbol or "").upper()))


class IdentityIndex:
    """In-memory deterministic index built once from kite_instruments.

    Holds three exact maps and never mutates after build. A name that maps to
    more than one symbol is kept as a LIST so ambiguity is detectable rather
    than silently collapsed by a last-write-wins dict.
    """

    def __init__(self) -> None:
        self.by_symbol: dict[str, str] = {}
        self.by_name: dict[str, list[str]] = {}
        self._name_keys: list[tuple[str, str]] = []   # (key, symbol), sorted
        self._name_tokens: list[tuple[frozenset, str]] = []   # (tokens, symbol)
        self.built = False

    def add(self, tradingsymbol: str, name: str | None) -> None:
        ts = (tradingsymbol or "").strip().upper()
        if not ts:
            return
        self.by_symbol[ts] = ts
        if is_non_equity_symbol(ts):
            return                      # reachable by exact symbol, not by name
        nk = _key(name or "")
        if nk:
            self.by_name.setdefault(nk, [])
            if ts not in self.by_name[nk]:
                self.by_name[nk].append(ts)
        tk = _tokens(name or "")
        if tk:
            self._name_tokens.append((tk, ts))

    def finalise(self) -> "IdentityIndex":
        self._name_keys = sorted(
            (k, s) for k, syms in self.by_name.items() for s in syms)
        self.built = True
        return self

    def all_token_candidates(self, query: str, cap: int = 8) -> list[str]:
        """Instruments whose name contains EVERY significant token of `query`.

        Set containment, not similarity: a token is present or it is not. There
        is no score, no threshold and no ordering by closeness. More than one
        hit is ambiguity and is refused by the caller.
        """
        q = _tokens(query)
        if not q:
            return []
        out: list[str] = []
        for name_toks, sym in self._name_tokens:
            if q <= name_toks and sym not in out:
                out.append(sym)
                if len(out) > cap:
                    break
        return out

    def prefix_candidates(self, key: str, cap: int = 8) -> list[str]:
        """Names that START WITH key. Exact, not fuzzy — a prefix either holds
        or it does not."""
        if not key or len(key) < 6:      # too short to be discriminating
            return []
        out: list[str] = []
        for k, sym in self._name_keys:
            if k.startswith(key) and sym not in out:
                out.append(sym)
                if len(out) > cap:
                    break
        return out

    def stats(self) -> dict:
        amb = sum(1 for v in self.by_name.values() if len(v) > 1)
        return {"symbols": len(self.by_symbol), "name_keys": len(self.by_name),
                "ambiguous_name_keys": amb}


async def build_index(session=None) -> IdentityIndex:
    """Build from kite_instruments. NSE equities only."""
    from sqlalchemy import text

    idx = IdentityIndex()

    async def _load(sess):
        rows = (await sess.execute(text(
            "SELECT tradingsymbol, name FROM kite_instruments "
            "WHERE exchange = 'NSE' AND instrument_type = 'EQ'"
        ))).all()
        for r in rows:
            idx.add(r.tradingsymbol, r.name)

    if session is not None:
        await _load(session)
    else:
        from db.database import AsyncSessionLocal

        async with AsyncSessionLocal() as own:
            await _load(own)
    return idx.finalise()


def resolve_identity(raw: str, index: IdentityIndex) -> IdentityResult:
    """The ladder. Returns a symbol only when exactly one candidate survives."""
    src = (raw or "").strip()
    if not src:
        return IdentityResult(src, None, Resolution.INVALID, "empty input")

    bare = strip_suffix(src).upper()
    if not bare:
        return IdentityResult(src, None, Resolution.INVALID, "no usable token")

    # 1 — already a tradingsymbol.
    if bare in index.by_symbol:
        return IdentityResult(src, normalize(bare), Resolution.EXACT_SYMBOL,
                              "input is already an NSE tradingsymbol")

    key = _key(bare)
    if not key:
        return IdentityResult(src, None, Resolution.INVALID, "key is empty after normalisation")

    # 2 — curated alias.
    if key in _ALIASES:
        target = _ALIASES[key]
        if target in index.by_symbol:
            return IdentityResult(src, normalize(target), Resolution.ALIAS,
                                  f"curated alias -> {target}")

    # 3 — exact normalised legal name.
    hits = index.by_name.get(key, [])
    if len(hits) == 1:
        return IdentityResult(src, normalize(hits[0]), Resolution.EXACT_NAME,
                              f"legal name matched {hits[0]}")
    if len(hits) > 1:
        return IdentityResult(src, None, Resolution.AMBIGUOUS,
                              f"{len(hits)} instruments share this name",
                              tuple(hits))

    # 4 — unique prefix.
    pref = index.prefix_candidates(key)
    if len(pref) == 1:
        return IdentityResult(src, normalize(pref[0]), Resolution.UNIQUE_PREFIX,
                              f"unique name prefix -> {pref[0]}")
    if len(pref) > 1:
        return IdentityResult(src, None, Resolution.AMBIGUOUS,
                              f"{len(pref)} names start with this key",
                              tuple(pref[:8]))

    # 5 — every significant token of the query present in exactly one name.
    #
    # Covers abbreviated exchange names the earlier tiers cannot reach:
    # "Automobile Corporation Of Goa" -> ACGL, "FSN" -> NYKAA,
    # "CCL Products (India) Limited" -> CCL. Stopwords are removed first so
    # "INDIA" and "LIMITED" do no matching work.
    #
    # This is set CONTAINMENT, not similarity -- a token is present or it is
    # not. Measured on 543 unresolved symbols: 72 resolved uniquely, 28 were
    # ambiguous and refused, 443 matched nothing (they are not NSE-listed).
    # A 14-case hand spot-check against kite_instruments was 14/14 correct.
    tok = index.all_token_candidates(bare)
    if len(tok) == 1:
        return IdentityResult(src, normalize(tok[0]), Resolution.ALL_TOKENS,
                              f"all name tokens matched exactly one instrument -> {tok[0]}")
    if len(tok) > 1:
        return IdentityResult(src, None, Resolution.AMBIGUOUS,
                              f"{len(tok)} instruments contain every token",
                              tuple(tok[:8]))

    return IdentityResult(src, None, Resolution.UNRESOLVED,
                          "no instrument matched by symbol, alias, name, prefix or tokens")


def is_nse_eligible(raw: str, index: IdentityIndex) -> bool:
    """Could this identifier POSSIBLY denote an NSE-listed equity?

    The denominator for a recall figure. A name that matches no NSE instrument
    on any tier is not a resolution failure -- it is a BSE-only listing, a
    delisted name, or not an equity at all. Counting those against recall makes
    the number meaningless.

    Measured 2026-08-27: 443 of 543 unresolved symbols (81.6%) matched nothing,
    which is why the naive "recall" of 68.5% understated the resolver badly.
    """
    r = resolve_identity(raw, index)
    if r.ok or r.needs_review:
        return True
    return bool(index.all_token_candidates(strip_suffix(raw).upper()))
