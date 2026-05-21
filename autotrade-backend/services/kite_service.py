"""Zerodha KiteConnect service — read-only portfolio tracker.

Handles the OAuth flow, token lifecycle, holdings sync, and XIRR calculation.
No orders are ever placed; this is strictly a portfolio analytics reader.

PAPER TRADING ONLY — this system never places real orders through Kite.
"""

from __future__ import annotations

import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import KiteSession, PortfolioHolding
from utils.config import settings
from utils.logger import logger

_IST = ZoneInfo("Asia/Kolkata")


def _kite_token_expiry() -> datetime.datetime:
    """Next 06:00 IST (Zerodha invalidates tokens daily at 06:00 IST)."""
    now_ist = datetime.datetime.now(_IST)
    expiry_ist = now_ist.replace(hour=6, minute=0, second=0, microsecond=0)
    if now_ist >= expiry_ist:
        expiry_ist += datetime.timedelta(days=1)
    # Return as naive UTC for storage in TIMESTAMP WITHOUT TIME ZONE column
    return expiry_ist.astimezone(datetime.timezone.utc).replace(tzinfo=None)


class KiteService:
    """Wraps kiteconnect.KiteConnect with session management and DB persistence."""

    # ── OAuth helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def get_login_url() -> str:
        """Return the Zerodha login URL to redirect the user to."""
        from kiteconnect import KiteConnect  # imported lazily — optional dep
        kc = KiteConnect(api_key=settings.KITE_API_KEY)
        return kc.login_url()

    @staticmethod
    async def generate_session(
        session: AsyncSession,
        request_token: str,
    ) -> KiteSession:
        """Exchange *request_token* for an access token and persist it.

        Deactivates any existing active session for the default user first.
        """
        from kiteconnect import KiteConnect

        kc = KiteConnect(api_key=settings.KITE_API_KEY)
        data = kc.generate_session(request_token, api_secret=settings.KITE_API_SECRET)

        # Deactivate old sessions
        await session.execute(
            update(KiteSession)
            .where(KiteSession.user_id == "default", KiteSession.is_active == True)  # noqa: E712
            .values(is_active=False)
        )

        now_utc = datetime.datetime.utcnow()
        kite_sess = KiteSession(
            user_id="default",
            access_token=data["access_token"],
            public_token=data.get("public_token"),
            login_time=now_utc,
            expires_at=_kite_token_expiry(),
            is_active=True,
        )
        session.add(kite_sess)
        await session.flush()
        logger.info(f"[KiteService] New session created — expires {kite_sess.expires_at}")
        return kite_sess

    @staticmethod
    async def get_access_token(session: AsyncSession) -> str | None:
        """Return the current valid access token, or None if not connected."""
        now_utc = datetime.datetime.utcnow()
        result = await session.execute(
            select(KiteSession).where(
                KiteSession.user_id == "default",
                KiteSession.is_active == True,  # noqa: E712
                KiteSession.expires_at > now_utc,
            ).order_by(KiteSession.created_at.desc()).limit(1)
        )
        row = result.scalar_one_or_none()
        return row.access_token if row else None

    @staticmethod
    async def disconnect(session: AsyncSession) -> None:
        """Deactivate all sessions for the default user."""
        await session.execute(
            update(KiteSession)
            .where(KiteSession.user_id == "default")
            .values(is_active=False)
        )
        logger.info("[KiteService] Session disconnected")

    # ── Holdings sync ─────────────────────────────────────────────────────────

    @staticmethod
    async def sync_holdings(session: AsyncSession) -> list[dict]:
        """Fetch holdings from Kite and upsert into portfolio_holdings table."""
        token = await KiteService.get_access_token(session)
        if not token:
            raise RuntimeError("No active Kite session — please login first")

        from kiteconnect import KiteConnect
        kc = KiteConnect(api_key=settings.KITE_API_KEY)
        kc.set_access_token(token)

        raw: list[dict] = kc.holdings()
        logger.info(f"[KiteService] Fetched {len(raw)} holdings from Kite")

        synced_at = datetime.datetime.utcnow()

        for h in raw:
            sym = h["tradingsymbol"]
            exch = h["exchange"]

            result = await session.execute(
                select(PortfolioHolding).where(
                    PortfolioHolding.tradingsymbol == sym,
                    PortfolioHolding.exchange == exch,
                )
            )
            holding = result.scalar_one_or_none()

            qty     = int(h.get("quantity", 0))
            avg_prc = float(h.get("average_price", 0.0))
            ltp     = float(h.get("last_price", 0.0))
            cur_val = qty * ltp
            cost    = qty * avg_prc
            pnl     = cur_val - cost
            pnl_pct = ((ltp - avg_prc) / avg_prc * 100) if avg_prc else 0.0
            day_chg     = float(h.get("day_change", 0.0))
            day_chg_pct = float(h.get("day_change_percentage", 0.0))

            if holding is None:
                holding = PortfolioHolding(
                    tradingsymbol=sym,
                    exchange=exch,
                    isin=h.get("isin"),
                )
                session.add(holding)

            holding.quantity       = qty
            holding.avg_price      = avg_prc
            holding.last_price     = ltp
            holding.current_value  = cur_val
            holding.pnl            = pnl
            holding.pnl_pct        = round(pnl_pct, 4)
            holding.day_change     = day_chg
            holding.day_change_pct = round(day_chg_pct, 4)
            holding.synced_at      = synced_at

        await session.flush()
        return raw

    @staticmethod
    async def enrich_with_live_prices(session: AsyncSession) -> None:
        """Update `last_price` and recompute PnL from Kite LTP quotes.

        Called by the hourly Celery task between full syncs.
        """
        token = await KiteService.get_access_token(session)
        if not token:
            return

        result = await session.execute(
            select(PortfolioHolding).where(PortfolioHolding.quantity > 0)
        )
        holdings = result.scalars().all()
        if not holdings:
            return

        from kiteconnect import KiteConnect
        kc = KiteConnect(api_key=settings.KITE_API_KEY)
        kc.set_access_token(token)

        instruments = [f"{h.exchange}:{h.tradingsymbol}" for h in holdings]
        try:
            quotes: dict[str, Any] = kc.ltp(instruments)
        except Exception as exc:
            logger.warning(f"[KiteService] LTP fetch failed: {exc}")
            return

        for h in holdings:
            key = f"{h.exchange}:{h.tradingsymbol}"
            q = quotes.get(key, {})
            ltp = float(q.get("last_price", h.last_price))
            h.last_price     = ltp
            h.current_value  = h.quantity * ltp
            h.pnl            = h.current_value - h.quantity * h.avg_price
            h.pnl_pct        = round(
                (ltp - h.avg_price) / h.avg_price * 100 if h.avg_price else 0.0, 4
            )

        await session.flush()
        logger.debug(f"[KiteService] Enriched live prices for {len(holdings)} holdings")

    # ── XIRR ──────────────────────────────────────────────────────────────────

    @staticmethod
    def calculate_xirr(
        buy_date: datetime.date,
        avg_price: float,
        quantity: int,
        current_price: float,
    ) -> float | None:
        """Simple XIRR using scipy.optimize.brentq.

        Returns annualised return rate as a percentage, or None if not computable.
        """
        try:
            from scipy.optimize import brentq  # type: ignore

            today = datetime.date.today()
            days = (today - buy_date).days
            if days <= 0 or avg_price <= 0 or quantity <= 0:
                return None

            invested = avg_price * quantity
            current  = current_price * quantity
            t = days / 365.0

            def npv(rate: float) -> float:
                return -invested + current / ((1 + rate) ** t)

            try:
                rate = brentq(npv, -0.999, 100.0, maxiter=1000)
                return round(rate * 100, 4)
            except ValueError:
                return None
        except ImportError:
            return None

    @staticmethod
    async def update_xirr_for_all(session: AsyncSession) -> None:
        """Recompute XIRR for all holdings that have a buy_date."""
        result = await session.execute(
            select(PortfolioHolding).where(
                PortfolioHolding.buy_date.isnot(None),
                PortfolioHolding.quantity > 0,
            )
        )
        holdings = result.scalars().all()
        for h in holdings:
            h.xirr = KiteService.calculate_xirr(
                h.buy_date, h.avg_price, h.quantity, h.last_price
            )
        await session.flush()
