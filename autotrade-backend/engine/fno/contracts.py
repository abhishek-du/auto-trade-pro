"""F&O contract resolution.

Turn a directional signal (underlying + side + spot) into a concrete, tradeable
NFO contract by *looking up* the KiteInstrument master — never by string-building
tradingsymbols (which is fragile and breaks across expiry-format changes).

The instrument master is populated by crawler.zerodha_market.refresh_instrument_tokens
when settings.ENABLE_FNO is True. If it's empty (e.g. Kite login unavailable),
every resolver returns None and callers must handle that gracefully.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import KiteInstrument
from utils.config import settings
from utils.logger import logger


# Minimum days-to-expiry we will trade — avoid expiry-day gamma/pin risk.
_MIN_DTE = 2


@dataclass
class ResolvedContract:
    """A concrete F&O contract chosen for a signal."""
    tradingsymbol:   str
    instrument_token: int
    exchange:        str            # "NFO"
    underlying:      str            # "NIFTY"
    instrument_type: str            # "CE" | "PE" | "FUT"
    strike:          float          # 0.0 for futures
    expiry:          date
    lot_size:        int
    tick_size:       float
    dte:             int            # days to expiry at resolution time


def _parse_expiry(raw: str) -> date | None:
    """Kite stores expiry as 'YYYY-MM-DD' (or '' for cash equity)."""
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw[:19], fmt).date()
        except ValueError:
            continue
    return None


async def _candidates(
    underlying: str,
    instrument_type: str,
    session: AsyncSession,
) -> list[KiteInstrument]:
    """All NFO rows for an underlying + instrument_type (CE/PE/FUT)."""
    rows = (await session.execute(
        select(KiteInstrument).where(
            KiteInstrument.exchange == "NFO",
            KiteInstrument.name == underlying.upper(),
            KiteInstrument.instrument_type == instrument_type,
        )
    )).scalars().all()
    return list(rows)


def _pick_expiry(rows: list[KiteInstrument], dte_pref: int) -> date | None:
    """Nearest tradeable expiry to the preferred days-to-expiry."""
    today = date.today()
    expiries = set()
    for r in rows:
        ex = _parse_expiry(r.expiry)
        if ex and (ex - today).days >= _MIN_DTE:
            expiries.add(ex)
    if not expiries:
        return None
    # Choose the expiry whose DTE is closest to the preference.
    return min(expiries, key=lambda ex: abs((ex - today).days - dte_pref))


async def resolve_option(
    underlying: str,
    option_type: str,           # "CE" | "PE"
    spot: float,
    session: AsyncSession,
    *,
    dte_pref: int | None = None,
    moneyness: float = 0.0,     # 0=ATM; +ve = ITM offset in strikes (signed by type)
) -> ResolvedContract | None:
    """Pick the ATM (or slightly-ITM) option at the expiry nearest dte_pref."""
    dte_pref = dte_pref if dte_pref is not None else settings.FNO_DEFAULT_DTE
    rows = await _candidates(underlying, option_type, session)
    if not rows:
        logger.debug(f"[fno/contracts] no {option_type} rows for {underlying} (master empty?)")
        return None

    expiry = _pick_expiry(rows, dte_pref)
    if expiry is None:
        return None

    at_expiry = [r for r in rows if _parse_expiry(r.expiry) == expiry and r.strike > 0]
    if not at_expiry:
        return None

    # ATM = strike closest to spot. moneyness shifts toward ITM:
    #   CE ITM = lower strike, PE ITM = higher strike.
    strikes = sorted({r.strike for r in at_expiry})
    atm = min(strikes, key=lambda k: abs(k - spot))
    if moneyness:
        step = _strike_step(strikes, atm)
        shift = int(round(moneyness)) * step
        target = atm - shift if option_type == "CE" else atm + shift
        atm = min(strikes, key=lambda k: abs(k - target))

    chosen = next(r for r in at_expiry if r.strike == atm)
    return _to_resolved(chosen, underlying, expiry)


async def resolve_future(
    underlying: str,
    session: AsyncSession,
    *,
    dte_pref: int | None = None,
) -> ResolvedContract | None:
    """Pick the futures contract at the expiry nearest dte_pref (usually near-month)."""
    dte_pref = dte_pref if dte_pref is not None else settings.FNO_DEFAULT_DTE
    rows = await _candidates(underlying, "FUT", session)
    if not rows:
        return None
    expiry = _pick_expiry(rows, dte_pref)
    if expiry is None:
        return None
    chosen = next((r for r in rows if _parse_expiry(r.expiry) == expiry), None)
    if chosen is None:
        return None
    return _to_resolved(chosen, underlying, expiry)


async def resolve_contract(
    underlying: str,
    side: str,                  # "BUY" | "SELL"
    spot: float,
    session: AsyncSession,
    *,
    instrument: str = "OPTION", # "OPTION" | "FUTURE"
    dte_pref: int | None = None,
) -> ResolvedContract | None:
    """High-level: directional signal → concrete contract.

    OPTION: BUY → buy CE, SELL → buy PE (defined-risk option buying).
    FUTURE: BUY → long future, SELL → short future (same contract either way).
    """
    if instrument == "FUTURE":
        return await resolve_future(underlying, session, dte_pref=dte_pref)
    option_type = "CE" if side.upper() == "BUY" else "PE"
    return await resolve_option(underlying, option_type, spot, session, dte_pref=dte_pref)


async def resolve_option_from_snapshot(
    underlying: str,
    option_type: str,           # "CE" | "PE"
    spot: float,
    session: AsyncSession,
    *,
    dte_pref: int | None = None,
    moneyness: float = 0.0,
) -> ResolvedContract | None:
    """PAPER-mode resolver: build a contract from the live NSE option chain
    (OptionContractSnapshot) + standard lot sizes — NO Kite instrument master.

    Lets the paper agent trade real NSE strikes/expiries/premiums on virtual
    money without the broker login. Tradingsymbol is synthesized for display.
    """
    from db.models import OptionContractSnapshot

    dte_pref = dte_pref if dte_pref is not None else settings.FNO_DEFAULT_DTE
    und = underlying.upper()

    rows = (await session.execute(
        select(OptionContractSnapshot).where(
            OptionContractSnapshot.underlying == und,
            OptionContractSnapshot.option_type == option_type,
        )
    )).scalars().all()
    if not rows:
        return None

    today = date.today()
    expiries = {r.expiry_date for r in rows if r.expiry_date and (r.expiry_date - today).days >= _MIN_DTE}
    if not expiries:
        return None
    expiry = min(expiries, key=lambda ex: abs((ex - today).days - dte_pref))

    at_expiry = [r for r in rows if r.expiry_date == expiry and r.strike > 0]
    if not at_expiry:
        return None
    strikes = sorted({r.strike for r in at_expiry})
    atm = min(strikes, key=lambda k: abs(k - spot))
    if moneyness:
        step = _strike_step(strikes, atm)
        shift = int(round(moneyness)) * step
        target = atm - shift if option_type == "CE" else atm + shift
        atm = min(strikes, key=lambda k: abs(k - target))

    lot = settings.fno_lot_sizes.get(und, 1)
    tsym = f"{und}{expiry:%y%b%d}{int(atm)}{option_type}".upper()
    return ResolvedContract(
        tradingsymbol=tsym, instrument_token=0, exchange="NFO",
        underlying=und, instrument_type=option_type, strike=float(atm),
        expiry=expiry, lot_size=lot, tick_size=0.05,
        dte=(expiry - today).days,
    )


def _strike_step(strikes: list[float], atm: float) -> float:
    """Infer the strike interval (e.g. 50 for NIFTY, 100 for BANKNIFTY)."""
    if len(strikes) < 2:
        return 50.0
    diffs = sorted(round(b - a, 2) for a, b in zip(strikes, strikes[1:]) if b > a)
    return diffs[len(diffs) // 2] if diffs else 50.0


def _to_resolved(r: KiteInstrument, underlying: str, expiry: date) -> ResolvedContract:
    return ResolvedContract(
        tradingsymbol    = r.tradingsymbol,
        instrument_token = r.instrument_token,
        exchange         = "NFO",
        underlying       = underlying.upper(),
        instrument_type  = r.instrument_type,
        strike           = float(r.strike),
        expiry           = expiry,
        lot_size         = int(r.lot_size or 1),
        tick_size        = float(r.tick_size or 0.05),
        dte              = (expiry - date.today()).days,
    )
