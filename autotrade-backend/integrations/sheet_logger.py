# Trade journal → spreadsheet.
#
# Logs the full lifecycle of every paper trade to a spreadsheet: why it was
# bought, the targets, an AI-estimated ETA to Target 1, which target actually
# hit, how long it took, the realised P&L and an AI expert post-mortem.
#
# The backend is PLUGGABLE. Today it writes a local .xlsx (openpyxl). Switching
# to Google Sheets is a one-line config change (SHEET_LOG_BACKEND="google") —
# the column schema and the idempotent sync below are backend-agnostic, so the
# Google sink just has to implement the same three primitives.
#
# Design: sync_journal(session) is IDEMPOTENT. It reads the trade-ids already in
# the sheet, appends rows for new trades, and updates the close-columns for
# trades that have since closed. First run therefore back-fills all history; it
# is safe to call on a schedule and after every trade cycle. All LLM/IO work
# runs off the event loop via asyncio.to_thread, and nothing here ever raises
# into the caller — journal failures must never affect trading.

from __future__ import annotations

import asyncio
import os
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import PaperTrade, MasterIntelligenceScore, TradeStatus
from integrations.trade_explainer import (
    build_expert_note, build_postmortem_note, estimate_eta_to_target,
)
from utils.config import settings
from utils.logger import logger

_IST = timezone(timedelta(hours=5, minutes=30))
_FILE_LOCK = threading.Lock()   # serialises local-xlsx writes across thread-pool

# ── Column schema (shared by every backend) ───────────────────────────────────
# (key, human header). Order defines the columns A, B, C, …
SCHEMA: list[tuple[str, str]] = [
    ("trade_id",        "Trade ID"),
    ("opened_at",       "Bought (IST)"),
    ("symbol",          "Symbol"),
    ("direction",       "Direction"),
    ("entry",           "Entry ₹"),
    ("stop",            "Stop ₹"),
    ("target_1",        "Target 1 ₹"),
    ("target_2",        "Target 2 ₹"),
    ("confidence",      "Confidence %"),
    ("hub_breakdown",   "Hub 7-factor"),
    ("why_bought",      "Why bought (AI)"),
    ("eta_t1",          "ETA to Target 1"),
    ("status",          "Status"),
    ("target_achieved", "Target achieved"),
    ("achieved_at",     "Closed (IST)"),
    ("duration",        "Duration"),
    ("pnl",             "P&L ₹ / %"),
    ("expert_note",     "Expert note (AI)"),
]
HEADERS = [h for _, h in SCHEMA]
KEYS    = [k for k, _ in SCHEMA]
# Columns written only when a trade closes (the rest are written on open).
CLOSE_KEYS = {"status", "target_achieved", "achieved_at", "duration", "pnl", "expert_note"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ist(dt: datetime | None) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_IST).strftime("%Y-%m-%d %H:%M")


def _fmt_duration(start: datetime, end: datetime) -> str:
    secs = max(0, (end - start).total_seconds())
    days, rem = divmod(int(secs), 86400)
    hours = rem // 3600
    if days:
        return f"{days}d {hours}h"
    mins = (rem % 3600) // 60
    return f"{hours}h {mins}m" if hours else f"{mins}m"


def _hub_dict(mis: MasterIntelligenceScore | None) -> dict | None:
    if mis is None:
        return None
    return {
        "technical":   round(mis.technical_score, 0),
        "news":        round(mis.news_score, 0),
        "sector":      round(mis.sector_score, 0),
        "macro":       round(mis.macro_score, 0),
        "earnings":    round(mis.earnings_score, 0),
        "fundamental": round(mis.fundamental_score, 0),
        "options":     round(mis.options_score, 0),
    }


def _trade_mgmt(trade: PaperTrade) -> dict:
    snap = trade.indicator_snapshot or {}
    return snap.get("trade_mgmt", {}) if isinstance(snap, dict) else {}


# ── Sinks ─────────────────────────────────────────────────────────────────────
# Each sink implements: existing_ids() -> {trade_id: (row_number, status)},
# append(row_dict), update(row_number, partial_dict). All synchronous/blocking.

class LocalExcelSink:
    def __init__(self, path: str):
        self.path = path

    def _load(self):
        import openpyxl
        if os.path.exists(self.path):
            wb = openpyxl.load_workbook(self.path)
            ws = wb.active
        else:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Trades"
            ws.append(HEADERS)
            ws.freeze_panes = "A2"
        return wb, ws

    def existing_ids(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        import openpyxl
        wb = openpyxl.load_workbook(self.path, read_only=True)
        ws = wb.active
        out = {}
        status_col = KEYS.index("status")
        for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not row or row[0] in (None, ""):
                continue
            try:
                tid = int(row[0])
            except (ValueError, TypeError):
                continue
            status = row[status_col] if len(row) > status_col else ""
            out[tid] = (i, status or "")
        wb.close()
        return out

    def append(self, row: dict):
        with _FILE_LOCK:
            wb, ws = self._load()
            ws.append([row.get(k, "") for k in KEYS])
            wb.save(self.path)

    def update(self, row_number: int, partial: dict):
        with _FILE_LOCK:
            wb, ws = self._load()
            for k, v in partial.items():
                if k in KEYS:
                    ws.cell(row=row_number, column=KEYS.index(k) + 1, value=v)
            wb.save(self.path)


class GoogleSheetsSink:
    """Same contract as LocalExcelSink, backed by a Google Sheet via gspread.

    Uses OAuth 2.0 Desktop credentials (your own Google account — no sheet
    sharing required). First call opens a browser for one-time authorisation;
    subsequent calls reuse the saved token (logs/google_token.pickle).

    Falls back to service-account credentials if
    GOOGLE_OAUTH_CLIENT_SECRET_JSON is not set.
    """
    def __init__(self):
        self._ws = None

    def _get_credentials(self):
        import pickle, os
        from google.auth.transport.requests import Request

        token_path = settings.GOOGLE_OAUTH_TOKEN_PATH
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]

        # Load cached token if available
        creds = None
        if os.path.exists(token_path):
            with open(token_path, "rb") as f:
                creds = pickle.load(f)

        # Refresh or re-authorise
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(token_path, "wb") as f:
                    pickle.dump(creds, f)
                return creds
            except Exception:
                pass  # fall through to re-authorise

        # First-time / re-auth: open browser
        secret_json = settings.GOOGLE_OAUTH_CLIENT_SECRET_JSON
        if secret_json and os.path.exists(secret_json):
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(secret_json, scopes)
            creds = flow.run_local_server(port=0)
            os.makedirs(os.path.dirname(token_path) or ".", exist_ok=True)
            with open(token_path, "wb") as f:
                pickle.dump(creds, f)
            return creds

        # Fallback: service-account
        if settings.GOOGLE_SERVICE_ACCOUNT_JSON:
            from google.oauth2.service_account import Credentials
            return Credentials.from_service_account_file(
                settings.GOOGLE_SERVICE_ACCOUNT_JSON, scopes=scopes)

        raise RuntimeError("No Google credentials configured "
                           "(set GOOGLE_OAUTH_CLIENT_SECRET_JSON or GOOGLE_SERVICE_ACCOUNT_JSON)")

    def _worksheet(self):
        if self._ws is not None:
            return self._ws
        import gspread
        creds = self._get_credentials()
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(settings.GOOGLE_SHEETS_ID)
        title = settings.GOOGLE_SHEETS_WORKSHEET
        try:
            ws = sh.worksheet(title)
        except Exception:
            ws = sh.add_worksheet(title=title, rows=1000, cols=len(HEADERS))
        # Ensure header row.
        if ws.row_values(1) != HEADERS:
            ws.update([HEADERS], "A1")
        self._ws = ws
        return ws

    def existing_ids(self) -> dict:
        ws = self._worksheet()
        records = ws.get_all_values()
        out = {}
        status_col = KEYS.index("status")
        for i, row in enumerate(records[1:], start=2):
            if not row or not row[0]:
                continue
            try:
                tid = int(row[0])
            except (ValueError, TypeError):
                continue
            status = row[status_col] if len(row) > status_col else ""
            out[tid] = (i, status or "")
        return out

    def append(self, row: dict):
        ws = self._worksheet()
        ws.append_row([row.get(k, "") for k in KEYS], value_input_option="USER_ENTERED")

    def update(self, row_number: int, partial: dict):
        ws = self._worksheet()
        for k, v in partial.items():
            if k in KEYS:
                ws.update_cell(row_number, KEYS.index(k) + 1, v)


def _make_sink():
    backend = (getattr(settings, "SHEET_LOG_BACKEND", "local") or "local").lower()
    if backend == "google":
        if not settings.google_sheets_available:
            logger.warning("[sheet_logger] backend=google but no service-account/ID set — skipping")
            return None
        return GoogleSheetsSink()
    return LocalExcelSink(settings.SHEET_LOG_LOCAL_PATH)


# ── Row builders ──────────────────────────────────────────────────────────────

def _open_row(trade: PaperTrade, hub: dict | None) -> dict:
    mgmt   = _trade_mgmt(trade)
    entry  = trade.entry_price
    t1     = mgmt.get("target_1", trade.take_profit)
    t2     = mgmt.get("target_2", trade.take_profit)
    atr    = mgmt.get("atr", 0.0)
    direction = trade.direction.value
    hub_txt = ""
    if hub:
        hub_txt = " ".join(f"{k[:4]}:{int(v):+d}" for k, v in hub.items())
    note = build_expert_note(trade.symbol, direction, entry, trade.stop_loss,
                             t1, t2, trade.signal_confidence, hub, trade.ai_reason)
    return {
        "trade_id":      trade.id,
        "opened_at":     _ist(trade.opened_at),
        "symbol":        trade.symbol,
        "direction":     direction,
        "entry":         round(entry, 2),
        "stop":          round(trade.stop_loss, 2),
        "target_1":      round(t1, 2),
        "target_2":      round(t2, 2),
        "confidence":    round(trade.signal_confidence, 0),
        "hub_breakdown": hub_txt,
        "why_bought":    note,
        "eta_t1":        estimate_eta_to_target(entry, t1, atr, direction),
        "status":        "OPEN",
    }


def _close_partial(trade: PaperTrade) -> dict:
    mgmt = _trade_mgmt(trade)
    t1   = mgmt.get("target_1", trade.take_profit)
    exit_p = trade.exit_price or 0.0
    # Which target did price actually reach?
    if trade.status == TradeStatus.STOPPED:
        status, achieved = "STOPPED", "Stopped out (no target)"
    else:
        hit_t1 = (exit_p >= t1) if trade.direction.value == "BUY" else (exit_p <= t1)
        if hit_t1:
            status, achieved = "T1_HIT", f"Target 1 ₹{t1:,.2f} reached"
        else:
            status, achieved = "CLOSED", "Closed before Target 1"
    dur = _fmt_duration(trade.opened_at, trade.closed_at) if trade.closed_at else ""
    pnl     = trade.pnl or 0.0
    pnl_pct = trade.pnl_percent or 0.0
    note = build_postmortem_note(trade.symbol, trade.direction.value, trade.entry_price,
                                 exit_p, pnl, pnl_pct, status, achieved, dur)
    return {
        "status":          status,
        "target_achieved": achieved,
        "achieved_at":     _ist(trade.closed_at),
        "duration":        dur,
        "pnl":             f"₹{pnl:,.0f} ({pnl_pct:+.1f}%)",
        "expert_note":     note,
    }


# ── Public entry point ────────────────────────────────────────────────────────

def _sync_blocking(open_rows: list[dict], close_updates: list[tuple[int, dict]]):
    sink = _make_sink()
    if sink is None:
        return 0, 0
    appended = updated = 0
    for row in open_rows:
        try:
            sink.append(row)
            appended += 1
        except Exception as exc:
            logger.warning(f"[sheet_logger] append failed for trade {row.get('trade_id')}: {exc}")
    for row_number, partial in close_updates:
        try:
            sink.update(row_number, partial)
            updated += 1
        except Exception as exc:
            logger.warning(f"[sheet_logger] update failed for row {row_number}: {exc}")
    return appended, updated


async def sync_journal(session: AsyncSession, *, limit: int = 500) -> dict:
    """Idempotently reconcile the spreadsheet with the trades table.

    - New trades  → append an open-row.
    - Trades whose sheet status is still OPEN but are now closed → update.
    Returns a small summary dict; never raises.
    """
    if not getattr(settings, "SHEET_LOG_ENABLED", False):
        return {"enabled": False}
    try:
        sink = _make_sink()
        if sink is None:
            return {"enabled": True, "skipped": "no_sink"}

        existing = await asyncio.to_thread(sink.existing_ids)

        rows = (await session.execute(
            select(PaperTrade).order_by(PaperTrade.opened_at.desc()).limit(limit)
        )).scalars().all()

        # Pre-load Hub scores for the symbols we may append (latest per symbol).
        need_hub = [t.symbol for t in rows if t.id not in existing]
        hub_map: dict[str, MasterIntelligenceScore] = {}
        if need_hub:
            ns = {f"{s.split('.')[0]}.NS" for s in need_hub}
            mis_rows = (await session.execute(
                select(MasterIntelligenceScore)
                .where(MasterIntelligenceScore.symbol.in_(ns))
                .order_by(MasterIntelligenceScore.scored_at.desc())
            )).scalars().all()
            for m in mis_rows:
                hub_map.setdefault(m.symbol, m)   # first = most recent

        open_rows: list[dict] = []
        close_updates: list[tuple[int, dict]] = []
        for t in rows:
            if t.id not in existing:
                hub = _hub_dict(hub_map.get(f"{t.symbol.split('.')[0]}.NS"))
                open_rows.append(_open_row(t, hub))
            else:
                row_number, sheet_status = existing[t.id]
                if sheet_status == "OPEN" and t.status != TradeStatus.OPEN:
                    close_updates.append((row_number, _close_partial(t)))

        if not open_rows and not close_updates:
            return {"enabled": True, "appended": 0, "updated": 0, "in_sheet": len(existing)}

        appended, updated = await asyncio.to_thread(_sync_blocking, open_rows, close_updates)
        logger.info(f"[sheet_logger] journal sync: +{appended} new, ~{updated} closed "
                    f"(backend={settings.SHEET_LOG_BACKEND})")
        return {"enabled": True, "appended": appended, "updated": updated}
    except Exception as exc:
        logger.warning(f"[sheet_logger] sync_journal failed (non-fatal): {exc}")
        return {"enabled": True, "error": str(exc)}
