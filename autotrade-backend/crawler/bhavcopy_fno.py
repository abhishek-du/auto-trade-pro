"""NSE F&O bhavcopy fetcher + dual-format parser (Phase 1 of the F&O backfill).

Downloads the End-Of-Day derivatives bhavcopy for a given trading date and
normalises it into a single flat record type, regardless of which of NSE's two
historical formats applies:

  • UDiFF  (>= 2024-07-08) — `BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip`
                             carries the underlying price and board lot size.
  • Legacy (<= 2024-07-05) — `fo<DDMMMYYYY>bhav.csv.zip`. No underlying price or
                             lot size column — those are derived downstream.

NSE blocks non-browser clients, so we reuse the same two-step browser-session
pattern (warm up the homepage for a cookie, then fetch) and the shared
BROWSER_HEADERS used by every other NSE crawler in this package.

This module is intentionally pure-ish: `fetch_fno_bhavcopy()` returns normalised
rows and writes nothing to the database — IV/Greeks computation and persistence
are Phase 2.

Public API
----------
fetch_fno_bhavcopy(trade_date, symbols=...) -> list[BhavContract] | None
parse_udiff(csv_bytes, trade_date, symbols)  -> list[BhavContract]
parse_legacy(csv_bytes, trade_date, symbols) -> list[BhavContract]
"""
from __future__ import annotations

import asyncio
import csv
import datetime
import io
import zipfile
from dataclasses import dataclass

import httpx

from crawler.fii_dii_crawler import BROWSER_HEADERS
from utils.config import settings
from utils.logger import logger

# ── Constants ─────────────────────────────────────────────────────────────────

_NSE_HOME = "https://www.nseindia.com"

# NSE switched the F&O bhavcopy to the UDiFF format on 2024-07-08 (Circular
# 62424). Anything on/after this date is UDiFF; older dates use the legacy file.
_UDIFF_START = datetime.date(2024, 7, 8)

# Default universe — the index underlyings the F&O engine actually trades.
# (Stock options are out of scope for the directional/vol backtest.)
_DEFAULT_SYMBOLS: frozenset[str] = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY"})

# Instrument-type codes that denote an *index* future / option in each format.
_UDIFF_INDEX_FUT = "IDF"
_UDIFF_INDEX_OPT = "IDO"
_LEGACY_INDEX_FUT = "FUTIDX"
_LEGACY_INDEX_OPT = "OPTIDX"


# ── Normalised record ─────────────────────────────────────────────────────────

@dataclass(slots=True)
class BhavContract:
    """One derivatives contract's EOD row, normalised across both formats."""
    underlying:      str                    # "NIFTY"
    instrument_type: str                    # "CE" | "PE" | "FUT"
    strike:          float                  # 0.0 for futures
    expiry:          datetime.date
    trade_date:      datetime.date
    settle:          float                  # settlement price (premium for options)
    close:           float                  # last/close price
    oi:              int                    # open interest
    oi_change:       int                    # change in open interest
    volume:          int                    # traded volume/contracts (unit varies by format)
    spot:            float | None           # underlying price (UDiFF only; None for legacy)
    lot_size:        int | None             # board lot (UDiFF only; None for legacy)


# ── URL builders ──────────────────────────────────────────────────────────────

def _udiff_url(d: datetime.date) -> str:
    return (
        "https://nsearchives.nseindia.com/content/fo/"
        f"BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    )


def _legacy_url(d: datetime.date) -> str:
    mmm = d.strftime("%b").upper()      # JAN, FEB, ...
    return (
        "https://nsearchives.nseindia.com/content/historical/DERIVATIVES/"
        f"{d:%Y}/{mmm}/fo{d:%d}{mmm}{d:%Y}bhav.csv.zip"
    )


# ── Small parse helpers ───────────────────────────────────────────────────────

def _col(fieldnames: list[str], *aliases: str) -> str | None:
    """Resolve the first matching header (case/space-insensitive) from aliases."""
    norm = {f.strip().lower(): f for f in fieldnames}
    for a in aliases:
        hit = norm.get(a.strip().lower())
        if hit is not None:
            return hit
    return None


def _f(value: object, default: float = 0.0) -> float:
    try:
        s = str(value).strip().replace(",", "")
        return float(s) if s not in ("", "-", "nan") else default
    except (TypeError, ValueError):
        return default


def _i(value: object, default: int = 0) -> int:
    return int(round(_f(value, default)))


def _unzip_first_csv(blob: bytes) -> bytes:
    """Return the bytes of the first .csv member inside a zip archive."""
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
        if name is None:
            raise ValueError("no .csv member in bhavcopy zip")
        return zf.read(name)


def _parse_date(raw: str) -> datetime.date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(raw[:19], fmt).date()
        except ValueError:
            continue
    return None


# ── Parsers ───────────────────────────────────────────────────────────────────

def parse_udiff(
    csv_bytes: bytes,
    trade_date: datetime.date,
    symbols: frozenset[str] = _DEFAULT_SYMBOLS,
) -> list[BhavContract]:
    """Parse a UDiFF F&O CSV into normalised index futures/options rows."""
    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8", "replace")))
    fn = reader.fieldnames or []
    c_sym   = _col(fn, "TckrSymb")
    c_tp    = _col(fn, "FinInstrmTp")
    c_strk  = _col(fn, "StrkPric")
    c_optn  = _col(fn, "OptnTp")
    c_xpry  = _col(fn, "XpryDt")
    c_settl = _col(fn, "SttlmPric")
    c_close = _col(fn, "ClsPric")
    c_oi    = _col(fn, "OpnIntrst")
    c_oichg = _col(fn, "ChngInOpnIntrst")
    c_vol   = _col(fn, "TtlTradgVol")
    c_undp  = _col(fn, "UndrlygPric")
    c_lot   = _col(fn, "NewBrdLotQty")
    if not (c_sym and c_tp and c_xpry):
        logger.warning("[bhav/udiff] unexpected header — missing key columns")
        return []

    out: list[BhavContract] = []
    for row in reader:
        sym = (row.get(c_sym) or "").strip().upper()
        if sym not in symbols:
            continue
        tp = (row.get(c_tp) or "").strip().upper()
        if tp == _UDIFF_INDEX_FUT:
            itype, strike = "FUT", 0.0
        elif tp == _UDIFF_INDEX_OPT:
            itype = (row.get(c_optn) or "").strip().upper()      # CE | PE
            if itype not in ("CE", "PE"):
                continue
            strike = _f(row.get(c_strk))
        else:
            continue
        expiry = _parse_date(row.get(c_xpry) or "")
        if expiry is None:
            continue
        spot = _f(row.get(c_undp)) if c_undp else 0.0
        lot  = _i(row.get(c_lot)) if c_lot else 0
        out.append(BhavContract(
            underlying=sym, instrument_type=itype, strike=strike, expiry=expiry,
            trade_date=trade_date,
            settle=_f(row.get(c_settl)) if c_settl else 0.0,
            close=_f(row.get(c_close)) if c_close else 0.0,
            oi=_i(row.get(c_oi)) if c_oi else 0,
            oi_change=_i(row.get(c_oichg)) if c_oichg else 0,
            volume=_i(row.get(c_vol)) if c_vol else 0,
            spot=spot if spot > 0 else None,
            lot_size=lot if lot > 0 else None,
        ))
    return out


def parse_legacy(
    csv_bytes: bytes,
    trade_date: datetime.date,
    symbols: frozenset[str] = _DEFAULT_SYMBOLS,
) -> list[BhavContract]:
    """Parse a legacy `foDDMMMYYYYbhav.csv` into normalised index rows.

    The legacy file has no underlying-price or lot-size column, so `spot` and
    `lot_size` are left None for downstream derivation.
    """
    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8", "replace")))
    fn = reader.fieldnames or []
    c_inst  = _col(fn, "INSTRUMENT")
    c_sym   = _col(fn, "SYMBOL")
    c_xpry  = _col(fn, "EXPIRY_DT")
    c_strk  = _col(fn, "STRIKE_PR")
    c_optn  = _col(fn, "OPTION_TYP")
    c_close = _col(fn, "CLOSE")
    c_settl = _col(fn, "SETTLE_PR")
    c_oi    = _col(fn, "OPEN_INT")
    c_oichg = _col(fn, "CHG_IN_OI")
    c_vol   = _col(fn, "CONTRACTS")
    if not (c_inst and c_sym and c_xpry):
        logger.warning("[bhav/legacy] unexpected header — missing key columns")
        return []

    out: list[BhavContract] = []
    for row in reader:
        sym = (row.get(c_sym) or "").strip().upper()
        if sym not in symbols:
            continue
        inst = (row.get(c_inst) or "").strip().upper()
        if inst == _LEGACY_INDEX_FUT:
            itype, strike = "FUT", 0.0
        elif inst == _LEGACY_INDEX_OPT:
            itype = (row.get(c_optn) or "").strip().upper()
            if itype not in ("CE", "PE"):
                continue
            strike = _f(row.get(c_strk))
        else:
            continue
        expiry = _parse_date(row.get(c_xpry) or "")
        if expiry is None:
            continue
        out.append(BhavContract(
            underlying=sym, instrument_type=itype, strike=strike, expiry=expiry,
            trade_date=trade_date,
            settle=_f(row.get(c_settl)) if c_settl else 0.0,
            close=_f(row.get(c_close)) if c_close else 0.0,
            oi=_i(row.get(c_oi)) if c_oi else 0,
            oi_change=_i(row.get(c_oichg)) if c_oichg else 0,
            volume=_i(row.get(c_vol)) if c_vol else 0,
            spot=None,
            lot_size=None,
        ))
    return out


# ── Fetch ─────────────────────────────────────────────────────────────────────

async def _download_zip(url: str, *, retries: int = 3) -> bytes | None:
    """Fetch a bhavcopy zip with the NSE browser-session warm-up.

    Returns the zip bytes, or None on a 404 (market holiday / not yet published).
    Raises on repeated transport/server errors so the caller can decide to stop.
    """
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                # Warm up for the bot-detection cookie, then fetch the archive.
                await client.get(_NSE_HOME, headers=BROWSER_HEADERS)
                await asyncio.sleep(1.0)
                resp = await client.get(url, headers=BROWSER_HEADERS)
            if resp.status_code == 404:
                return None                      # holiday / missing — caller skips
            if resp.status_code == 200 and resp.content[:2] == b"PK":
                return resp.content
            last_exc = ValueError(
                f"HTTP {resp.status_code} ({len(resp.content)} bytes, "
                f"magic={resp.content[:2]!r})"
            )
        except (httpx.TransportError, httpx.HTTPError) as exc:
            last_exc = exc
        await asyncio.sleep(1.5 * attempt)       # polite backoff between retries
    raise RuntimeError(f"bhavcopy download failed for {url}: {last_exc}")


async def fetch_fno_bhavcopy(
    trade_date: datetime.date,
    symbols: frozenset[str] | set[str] | None = None,
) -> list[BhavContract] | None:
    """Download + parse the F&O bhavcopy for one trading date.

    Picks the UDiFF or legacy format/URL by date automatically. Returns the
    normalised rows, or None when no file exists for that date (weekend/holiday).
    """
    syms = frozenset(s.upper() for s in (symbols or _DEFAULT_SYMBOLS))
    is_udiff = trade_date >= _UDIFF_START
    url = _udiff_url(trade_date) if is_udiff else _legacy_url(trade_date)

    blob = await _download_zip(url)
    if blob is None:
        logger.info(f"[bhav] {trade_date} — no file (holiday/weekend) — skipped")
        return None

    try:
        csv_bytes = _unzip_first_csv(blob)
    except (zipfile.BadZipFile, ValueError) as exc:
        logger.warning(f"[bhav] {trade_date} — bad archive: {exc}")
        return None

    rows = (parse_udiff if is_udiff else parse_legacy)(csv_bytes, trade_date, syms)
    fmt = "udiff" if is_udiff else "legacy"
    n_opt = sum(1 for r in rows if r.instrument_type in ("CE", "PE"))
    n_fut = sum(1 for r in rows if r.instrument_type == "FUT")
    logger.info(f"[bhav] {trade_date} ({fmt}): {n_opt} option + {n_fut} future rows "
                f"for {sorted(syms)}")
    return rows


# ── Manual smoke test ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    async def _main() -> None:
        d = (datetime.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1
             else datetime.date.today() - datetime.timedelta(days=1))
        rows = await fetch_fno_bhavcopy(d)
        if not rows:
            print(f"{d}: no data")
            return
        print(f"{d}: {len(rows)} normalised rows")
        for r in rows[:8]:
            tag = r.instrument_type if r.instrument_type == "FUT" else f"{r.strike:.0f}{r.instrument_type}"
            print(f"  {r.underlying:9} {tag:10} exp={r.expiry} "
                  f"settle={r.settle:9.2f} oi={r.oi:>10} spot={r.spot} lot={r.lot_size}")

    asyncio.run(_main())
