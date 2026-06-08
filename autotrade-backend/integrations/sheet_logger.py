# Trade journal → spreadsheet.
#
# Schema (36 cols):
#   #, Date, Symbol, Dir, Entry, SL, SL%, T1, T1%, T2, T2%, R:R, ATR, Conf%,
#   Size₹, Risk₹, Tech, News, Sector, Macro, Earn, Fund, Opts,
#   Why Bought(AI), ETA,
#   Live ₹ (*), Live P&L ₹ (*), Live %, Days Held,   ← Google Sheets formulas
#   Status, Target Hit, Closed, Duration, P&L ₹, P&L %, Expert Note(AI)
#
# (*) Live ₹ uses =GOOGLEFINANCE("NSE:SYMBOL","price") — auto-refreshes ~5 min.
#     Live P&L auto-recalculates from live price while Status = OPEN.
#
# Idempotent sync: new open-rows append; closed-rows update their close-columns.

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

_IST       = timezone(timedelta(hours=5, minutes=30))
_FILE_LOCK = threading.Lock()

# ── Column schema ─────────────────────────────────────────────────────────────
SCHEMA: list[tuple[str, str]] = [
    # ── Trade identity ─────────────────────────────────────────────────────
    ("trade_id",      "#"),
    ("opened_at",     "Bought (IST)"),
    ("symbol",        "Symbol"),
    ("direction",     "Dir"),
    # ── Levels ─────────────────────────────────────────────────────────────
    ("entry",         "Entry ₹"),
    ("stop",          "SL ₹"),
    ("stop_pct",      "SL%"),
    ("target_1",      "T1 ₹"),
    ("t1_pct",        "T1%"),
    ("target_2",      "T2 ₹"),
    ("t2_pct",        "T2%"),
    ("rr",            "R:R"),
    ("atr",           "ATR"),
    # ── Signal quality ─────────────────────────────────────────────────────
    ("confidence",    "Conf %"),
    ("size_rs",       "Size ₹"),
    ("max_risk_rs",   "Max Risk ₹"),
    # ── Hub 7-factor breakdown ──────────────────────────────────────────────
    ("hub_tech",      "Tech"),
    ("hub_news",      "News"),
    ("hub_sector",    "Sector"),
    ("hub_macro",     "Macro"),
    ("hub_earn",      "Earn"),
    ("hub_fund",      "Fund"),
    ("hub_opts",      "Opts"),
    # ── AI context ─────────────────────────────────────────────────────────
    ("why_bought",    "Why Bought (AI)"),
    ("eta_t1",        "ETA to T1"),
    # ── LIVE data (Google Sheets formulas — auto-refresh) ──────────────────
    ("live_price",    "Live ₹ 🔴"),
    ("live_pnl_rs",   "Live P&L ₹"),
    ("live_pnl_pct",  "Live P&L %"),
    ("days_held",     "Days Held"),
    # ── Outcome (filled when trade closes) ─────────────────────────────────
    ("status",        "Status"),
    ("target_hit",    "Target Hit"),
    ("closed_at",     "Closed (IST)"),
    ("duration",      "Duration"),
    ("pnl_rs",        "Final P&L ₹"),
    ("pnl_pct",       "Final P&L %"),
    ("expert_note",   "Expert Note (AI)"),
]
HEADERS   = [h for _, h in SCHEMA]
KEYS      = [k for k, _ in SCHEMA]
CLOSE_KEYS = {"status", "target_hit", "closed_at", "duration", "pnl_rs", "pnl_pct", "expert_note"}

_CI  = {k: i for i, k in enumerate(KEYS)}   # key → 0-based column index
_COL = {}                                    # key → column letter (filled after _col_letter defined)

def _col_letter(idx: int) -> str:
    s, i = "", idx + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s

# Pre-compute column letters for formula use
for _k, _i in _CI.items():
    _COL[_k] = _col_letter(_i)

# Column widths (pixels) – 36 cols
_COL_WIDTHS = [
    50, 130, 110, 50,           # #, Date, Symbol, Dir
    80, 80, 55, 80, 55, 80, 55, 50, 60,   # Entry→ATR
    60, 90, 90,                  # Conf, Size, Risk
    50, 50, 55, 55, 50, 50, 50,  # Hub 7
    340, 130,                    # Why, ETA
    85, 100, 80, 75,             # Live: price, P&L₹, P&L%, Days
    80, 170, 130, 80, 90, 75, 340,  # Status→Expert
]

# ── Colour palette ─────────────────────────────────────────────────────────────
def _rgb(r, g, b):
    return {"red": r/255, "green": g/255, "blue": b/255}

C_HEADER_BG   = _rgb(15,  23,  42)   # slate-900
C_HEADER_FG   = _rgb(248, 250, 252)
C_BUY_BG      = _rgb(240, 253, 244)  # green-50
C_SELL_BG     = _rgb(255, 241, 242)  # rose-50
C_LIVE_HEADER = _rgb(6,   182, 212)  # cyan accent for live section
C_HUB_POS     = _rgb(220, 252, 231)
C_HUB_NEG     = _rgb(254, 226, 226)
C_STATUS_OPEN = _rgb(254, 243, 199)
C_STATUS_T1   = _rgb(209, 250, 229)
C_STATUS_STOP = _rgb(254, 226, 226)
C_PNL_POS     = _rgb(209, 250, 229)
C_PNL_NEG     = _rgb(254, 226, 226)
C_SECTION_BG  = _rgb(248, 250, 252)

# ── Helpers ───────────────────────────────────────────────────────────────────
def _ist(dt):
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_IST).strftime("%Y-%m-%d %H:%M")

def _fmt_duration(start, end):
    secs = max(0, (end - start).total_seconds())
    days, rem = divmod(int(secs), 86400)
    hours = rem // 3600
    if days:
        return f"{days}d {hours}h"
    mins = (rem % 3600) // 60
    return f"{hours}h {mins}m" if hours else f"{mins}m"

def _hub_scores(mis):
    if not mis:
        return {}
    return {
        "hub_tech":   round(mis.technical_score),
        "hub_news":   round(mis.news_score),
        "hub_sector": round(mis.sector_score),
        "hub_macro":  round(mis.macro_score),
        "hub_earn":   round(mis.earnings_score),
        "hub_fund":   round(mis.fundamental_score),
        "hub_opts":   round(mis.options_score),
    }

def _trade_mgmt(trade):
    snap = trade.indicator_snapshot or {}
    return snap.get("trade_mgmt", {}) if isinstance(snap, dict) else {}

def _pct(a, b):
    try:
        return round((a - b) / b * 100, 2) if b else 0.0
    except Exception:
        return 0.0

def _rr(entry, stop, t1):
    try:
        risk = abs(entry - stop)
        return round(abs(t1 - entry) / risk, 2) if risk else 0.0
    except Exception:
        return 0.0

# ── Google Sheets live formulas ────────────────────────────────────────────────
# These are written as formula strings; gspread interprets them when
# value_input_option="USER_ENTERED". {R} is replaced with the actual row number.

def _live_formulas(row_num="{ROW}") -> dict:
    """Return formula strings for the 4 live-data columns.

    row_num may be a concrete int or the default sentinel "{ROW}".
    GoogleSheetsSink.append() replaces "{ROW}" with the actual row number.
    """
    R = row_num
    C  = _COL                      # shorthand
    # Symbol in our sheet has .NS stripped → "DALBHARAT", "RELIANCE" etc.
    live_col  = C["live_price"]    # Z
    entry_col = C["entry"]         # E
    dir_col   = C["direction"]     # D
    size_col  = C["size_rs"]       # O
    stat_col  = C["status"]        # AD
    pnl_col   = C["pnl_rs"]       # AH
    pnl_pct_c = C["pnl_pct"]      # AI
    cl_col    = C["closed_at"]     # AF
    op_col    = C["opened_at"]     # B

    return {
        # Live price via GOOGLEFINANCE (auto-refreshes ~5 min, 15-min delay on free tier)
        "live_price": (
            f'=IFERROR(GOOGLEFINANCE("NSE:"&{C["symbol"]}{R},"price"),"⏳")'
        ),
        # Live P&L ₹ — shows unrealized while OPEN, final once closed
        "live_pnl_rs": (
            f'=IFERROR(IF(ISNUMBER({pnl_col}{R}),{pnl_col}{R},'
            f'IF({live_col}{R}="","—",'
            f'IF({dir_col}{R}="SELL",'
            f'({entry_col}{R}-{live_col}{R})*{size_col}{R}/{entry_col}{R},'
            f'({live_col}{R}-{entry_col}{R})*{size_col}{R}/{entry_col}{R}'
            f'))),"—")'
        ),
        # Live P&L % (same logic, returns percentage)
        "live_pnl_pct": (
            f'=IFERROR(IF(ISNUMBER({pnl_col}{R}),{pnl_pct_c}{R},'
            f'IF({live_col}{R}="","—",'
            f'IF({dir_col}{R}="SELL",'
            f'({entry_col}{R}-{live_col}{R})/{entry_col}{R}*100,'
            f'({live_col}{R}-{entry_col}{R})/{entry_col}{R}*100'
            f'))),"—")'
        ),
        # Days held (integer)
        "days_held": (
            f'=IFERROR(IF({cl_col}{R}<>"",'
            f'DAYS(LEFT({cl_col}{R},10),LEFT({op_col}{R},10)),'
            f'DAYS(TEXT(TODAY(),"yyyy-mm-dd"),LEFT({op_col}{R},10))),"—")'
        ),
    }

# ── Row builders ──────────────────────────────────────────────────────────────
def _open_row(trade: PaperTrade, hub) -> dict:
    mgmt      = _trade_mgmt(trade)
    entry     = trade.entry_price
    stop      = trade.stop_loss
    t1        = mgmt.get("target_1", trade.take_profit)
    t2        = mgmt.get("target_2", trade.take_profit)
    atr       = mgmt.get("atr", 0.0)
    direction = trade.direction.value
    units     = trade.size_units
    size_rs   = round(entry * units, 0)
    max_risk  = round(abs(entry - stop) * units, 0)
    hub_scores = _hub_scores(hub)
    hub_note   = {k.replace("hub_", ""): v for k, v in hub_scores.items()} if hub_scores else None
    note = build_expert_note(trade.symbol, direction, entry, stop, t1, t2,
                             trade.signal_confidence, hub_note, trade.ai_reason)
    row = {
        "trade_id":    trade.id,
        "opened_at":   _ist(trade.opened_at),
        "symbol":      trade.symbol.replace(".NS", ""),
        "direction":   direction,
        "entry":       round(entry, 2),
        "stop":        round(stop, 2),
        "stop_pct":    f"{_pct(stop, entry):+.1f}%",
        "target_1":    round(t1, 2),
        "t1_pct":      f"{_pct(t1, entry):+.1f}%",
        "target_2":    round(t2, 2),
        "t2_pct":      f"{_pct(t2, entry):+.1f}%",
        "rr":          f"1:{_rr(entry, stop, t1)}",
        "atr":         round(atr, 2),
        "confidence":  round(trade.signal_confidence, 1),
        "size_rs":     size_rs,
        "max_risk_rs": max_risk,
        "why_bought":  note,
        "eta_t1":      estimate_eta_to_target(entry, t1, atr, direction),
        "status":      "OPEN",
    }
    row.update(hub_scores)
    # Always add live formula templates; GoogleSheetsSink.append() substitutes
    # {ROW} with the concrete row number. LocalExcelSink strips =... strings.
    row.update(_live_formulas())
    return row


def _close_partial(trade: PaperTrade) -> dict:
    mgmt   = _trade_mgmt(trade)
    t1     = mgmt.get("target_1", trade.take_profit)
    exit_p = trade.exit_price or 0.0
    if trade.status == TradeStatus.STOPPED:
        status, hit = "STOPPED ❌", "Stopped out"
    else:
        hit_t1 = (exit_p >= t1) if trade.direction.value == "BUY" else (exit_p <= t1)
        if hit_t1:
            status, hit = "T1 HIT ✅", f"T1 ₹{t1:,.2f} reached"
        else:
            status, hit = "CLOSED", "Closed before T1"
    dur  = _fmt_duration(trade.opened_at, trade.closed_at) if trade.closed_at else ""
    pnl  = trade.pnl or 0.0
    pct  = trade.pnl_percent or 0.0
    note = build_postmortem_note(trade.symbol, trade.direction.value, trade.entry_price,
                                 exit_p, pnl, pct, status, hit, dur)
    return {
        "status":      status,
        "target_hit":  hit,
        "closed_at":   _ist(trade.closed_at),
        "duration":    dur,
        "pnl_rs":      round(pnl, 0),
        "pnl_pct":     round(pct, 2),
        "expert_note": note,
    }

# ── Formatting helpers ────────────────────────────────────────────────────────
def _cell_fmt(bg=None, fg=None, bold=False, halign=None, wrap=False):
    fmt: dict = {}
    if bg:
        fmt["backgroundColor"] = bg
    tf: dict = {}
    if fg:
        tf["foregroundColor"] = fg
    if bold:
        tf["bold"] = True
    if tf:
        fmt["textFormat"] = tf
    if halign:
        fmt["horizontalAlignment"] = halign
    if wrap:
        fmt["wrapStrategy"] = "WRAP"
    return fmt

def _repeat(ws_id, r0, r1, c0, c1, fmt, fields):
    return {"repeatCell": {
        "range":  {"sheetId": ws_id, "startRowIndex": r0, "endRowIndex": r1,
                   "startColumnIndex": c0, "endColumnIndex": c1},
        "cell":   {"userEnteredFormat": fmt},
        "fields": f"userEnteredFormat({fields})",
    }}

def _cond_contains(ws_id, c0, c1, val, bg):
    return {"addConditionalFormatRule": {"rule": {
        "ranges": [{"sheetId": ws_id, "startRowIndex": 1,
                    "startColumnIndex": c0, "endColumnIndex": c1}],
        "booleanRule": {
            "condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": val}]},
            "format": {"backgroundColor": bg},
        }
    }, "index": 0}}

def _cond_number(ws_id, c0, c1, op, val, bg):
    return {"addConditionalFormatRule": {"rule": {
        "ranges": [{"sheetId": ws_id, "startRowIndex": 1,
                    "startColumnIndex": c0, "endColumnIndex": c1}],
        "booleanRule": {
            "condition": {"type": op, "values": [{"userEnteredValue": str(val)}]},
            "format": {"backgroundColor": bg},
        }
    }, "index": 0}}


def _setup_trades_sheet(sh, ws):
    ws_id  = ws.id
    n_cols = len(KEYS)
    reqs   = []

    # Expand column count if needed
    reqs.append({"updateSheetProperties": {
        "properties": {"sheetId": ws_id,
                       "gridProperties": {"columnCount": n_cols + 4}},
        "fields": "gridProperties.columnCount",
    }})

    # Freeze header
    reqs.append({"updateSheetProperties": {
        "properties": {"sheetId": ws_id,
                       "gridProperties": {"frozenRowCount": 1}},
        "fields": "gridProperties.frozenRowCount",
    }})

    # Header row height
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": ws_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
        "properties": {"pixelSize": 28}, "fields": "pixelSize",
    }})

    # Column widths
    for ci, px in enumerate(_COL_WIDTHS[:n_cols]):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": ws_id, "dimension": "COLUMNS",
                      "startIndex": ci, "endIndex": ci + 1},
            "properties": {"pixelSize": px}, "fields": "pixelSize",
        }})

    # Header row formatting (dark navy)
    reqs.append(_repeat(ws_id, 0, 1, 0, n_cols,
        _cell_fmt(bg=C_HEADER_BG, fg=C_HEADER_FG, bold=True, halign="CENTER"),
        "backgroundColor,textFormat,horizontalAlignment"))

    # Cyan highlight on the 4 live-data header cells
    live_start = _CI["live_price"]
    live_end   = _CI["days_held"] + 1
    reqs.append(_repeat(ws_id, 0, 1, live_start, live_end,
        _cell_fmt(bg=_rgb(6,182,212), fg=C_HEADER_BG, bold=True, halign="CENTER"),
        "backgroundColor,textFormat,horizontalAlignment"))

    # Center numeric/short columns in data rows
    center_keys = [
        "trade_id","direction","stop_pct","t1_pct","t2_pct","rr","atr",
        "confidence","size_rs","max_risk_rs",
        "hub_tech","hub_news","hub_sector","hub_macro","hub_earn","hub_fund","hub_opts",
        "live_price","live_pnl_rs","live_pnl_pct","days_held",
        "eta_t1","status","duration","pnl_rs","pnl_pct",
    ]
    for k in center_keys:
        ci = _CI[k]
        reqs.append(_repeat(ws_id, 1, 2000, ci, ci+1,
            _cell_fmt(halign="CENTER"), "horizontalAlignment"))

    # Wrap AI text columns
    for k in ("why_bought", "expert_note"):
        ci = _CI[k]
        reqs.append(_repeat(ws_id, 1, 2000, ci, ci+1,
            _cell_fmt(wrap=True), "wrapStrategy"))

    # Conditional: whole row color by direction
    reqs.append(_cond_contains(ws_id, 0, n_cols, "BUY",  C_BUY_BG))
    reqs.append(_cond_contains(ws_id, 0, n_cols, "SELL", C_SELL_BG))

    # Conditional: Status column
    ci_st = _CI["status"]
    reqs.append(_cond_contains(ws_id, ci_st, ci_st+1, "OPEN",    C_STATUS_OPEN))
    reqs.append(_cond_contains(ws_id, ci_st, ci_st+1, "T1 HIT",  C_STATUS_T1))
    reqs.append(_cond_contains(ws_id, ci_st, ci_st+1, "STOPPED", C_STATUS_STOP))

    # Conditional: Live P&L and Final P&L columns (green/red on positive/negative)
    for k in ("live_pnl_rs", "pnl_rs"):
        ci = _CI[k]
        reqs.append(_cond_number(ws_id, ci, ci+1, "NUMBER_GREATER", 0, C_PNL_POS))
        reqs.append(_cond_number(ws_id, ci, ci+1, "NUMBER_LESS",    0, C_PNL_NEG))
    for k in ("live_pnl_pct", "pnl_pct"):
        ci = _CI[k]
        reqs.append(_cond_number(ws_id, ci, ci+1, "NUMBER_GREATER", 0, C_PNL_POS))
        reqs.append(_cond_number(ws_id, ci, ci+1, "NUMBER_LESS",    0, C_PNL_NEG))

    # Conditional: Hub factor cells — positive=green, negative=red
    for k in ("hub_tech","hub_news","hub_sector","hub_macro","hub_earn","hub_fund","hub_opts"):
        ci = _CI[k]
        reqs.append(_cond_number(ws_id, ci, ci+1, "NUMBER_GREATER", 0, C_HUB_POS))
        reqs.append(_cond_number(ws_id, ci, ci+1, "NUMBER_LESS",    0, C_HUB_NEG))

    # Conditional: Days Held — highlight if held > 15 days (overdue)
    ci_days = _CI["days_held"]
    reqs.append(_cond_number(ws_id, ci_days, ci_days+1, "NUMBER_GREATER", 15,
                             _rgb(254, 243, 199)))   # amber warning

    sh.batch_update({"requests": reqs})


def _add_summary_sheet(sh, trades_ws_title="Trades"):
    """Create/refresh the 📊 Summary dashboard sheet."""
    try:
        summary = sh.worksheet("📊 Summary")
    except Exception:
        summary = sh.add_worksheet(title="📊 Summary", rows=60, cols=8)

    T   = trades_ws_title
    C   = _COL

    # Column letter shorthands for summary formulas
    dir_c  = C["direction"]      # D
    st_c   = C["status"]         # AD
    pnl_c  = C["pnl_rs"]        # AH
    pct_c  = C["pnl_pct"]       # AI
    conf_c = C["confidence"]     # N
    sym_c  = C["symbol"]         # C
    lpnl_c = C["live_pnl_rs"]   # AA
    lp_c   = C["live_price"]     # Z
    days_c = C["days_held"]      # AC

    rows = [
        # Row 1: Title
        ["🤖 AutoTrade Pro — Live Trade Journal", "", "", "", "", "", "", ""],
        [f"Refreshed:", f'=TEXT(NOW(),"dd mmm yyyy HH:MM")', "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", ""],

        # Row 4: Section header
        ["📊 PORTFOLIO SNAPSHOT", "", "", "", "📈 P&L SUMMARY", "", "", ""],
        ["Total Trades",
         f"=COUNTA('{T}'!A2:A)",
         "",
         "",
         "Total Realized P&L ₹",
         f'=IFERROR(SUMIF(\'{T}\'!{st_c}2:{st_c},"<>OPEN",\'{T}\'!{pnl_c}2:{pnl_c}),0)',
         "", ""],
        ["Open Positions",
         f"=COUNTIF('{T}'!{st_c}2:{st_c},\"OPEN\")",
         "",
         "",
         "Total Unrealized P&L ₹",
         f"=IFERROR(SUMIF('{T}'!{st_c}2:{st_c},\"OPEN\",'{T}'!{lpnl_c}2:{lpnl_c}),0)",
         "", ""],
        ["Closed (Profit)",
         f"=COUNTIFS('{T}'!{st_c}2:{st_c},\"<>OPEN\",'{T}'!{pnl_c}2:{pnl_c},\">0\")",
         "",
         "",
         "Best Trade ₹",
         f"=IFERROR(MAX('{T}'!{pnl_c}2:{pnl_c}),\"—\")",
         "", ""],
        ["Closed (Loss)",
         f"=COUNTIFS('{T}'!{st_c}2:{st_c},\"<>OPEN\",'{T}'!{pnl_c}2:{pnl_c},\"<0\")",
         "",
         "",
         "Worst Trade ₹",
         f"=IFERROR(MIN('{T}'!{pnl_c}2:{pnl_c}),\"—\")",
         "", ""],
        ["Win Rate",
         f"=IFERROR(TEXT(E7/(E7+E8),\"0.0%\"),\"—\")",
         "",
         "",
         "Avg P&L per trade ₹",
         f"=IFERROR(AVERAGEIF('{T}'!{st_c}2:{st_c},\"<>OPEN\",'{T}'!{pnl_c}2:{pnl_c}),\"—\")",
         "", ""],
        ["BUY trades",
         f"=COUNTIF('{T}'!{dir_c}2:{dir_c},\"BUY\")",
         "",
         "",
         "Avg Hold Duration (days)",
         f"=IFERROR(AVERAGEIF('{T}'!{days_c}2:{days_c},\"<>—\",'{T}'!{days_c}2:{days_c}),\"—\")",
         "", ""],
        ["SELL trades",
         f"=COUNTIF('{T}'!{dir_c}2:{dir_c},\"SELL\")",
         "",
         "",
         "Avg Confidence %",
         f"=IFERROR(AVERAGE('{T}'!{conf_c}2:{conf_c}),\"—\")",
         "", ""],
        ["", "", "", "", "", "", "", ""],

        # Row 13: Live open positions
        ["⚡ OPEN POSITIONS (live prices auto-refresh)", "", "", "", "", "", "", ""],
        ["Symbol", "Dir", "Entry ₹", "Live ₹", "Live P&L ₹", "Live P&L %", "Days Held", "ETA"],
    ]

    # Last 8 open trades (most recent first) using FILTER+SORT
    eta_c = C["eta_t1"]
    entry_c = C["entry"]
    for i in range(1, 9):
        rows.append([
            f"=IFERROR(INDEX(FILTER('{T}'!{sym_c}$2:{sym_c},'{T}'!{st_c}$2:{st_c}=\"OPEN\"),COUNTA(FILTER('{T}'!{sym_c}$2:{sym_c},'{T}'!{st_c}$2:{st_c}=\"OPEN\"))+1-{i}),\"\")",
            f"=IFERROR(INDEX(FILTER('{T}'!{dir_c}$2:{dir_c},'{T}'!{st_c}$2:{st_c}=\"OPEN\"),COUNTA(FILTER('{T}'!{dir_c}$2:{dir_c},'{T}'!{st_c}$2:{st_c}=\"OPEN\"))+1-{i}),\"\")",
            f"=IFERROR(INDEX(FILTER('{T}'!{entry_c}$2:{entry_c},'{T}'!{st_c}$2:{st_c}=\"OPEN\"),COUNTA(FILTER('{T}'!{entry_c}$2:{entry_c},'{T}'!{st_c}$2:{st_c}=\"OPEN\"))+1-{i}),\"\")",
            f"=IFERROR(INDEX(FILTER('{T}'!{lp_c}$2:{lp_c},'{T}'!{st_c}$2:{st_c}=\"OPEN\"),COUNTA(FILTER('{T}'!{lp_c}$2:{lp_c},'{T}'!{st_c}$2:{st_c}=\"OPEN\"))+1-{i}),\"\")",
            f"=IFERROR(INDEX(FILTER('{T}'!{lpnl_c}$2:{lpnl_c},'{T}'!{st_c}$2:{st_c}=\"OPEN\"),COUNTA(FILTER('{T}'!{lpnl_c}$2:{lpnl_c},'{T}'!{st_c}$2:{st_c}=\"OPEN\"))+1-{i}),\"\")",
            f"=IFERROR(INDEX(FILTER('{T}'!{C['live_pnl_pct']}$2:{C['live_pnl_pct']},'{T}'!{st_c}$2:{st_c}=\"OPEN\"),COUNTA(FILTER('{T}'!{C['live_pnl_pct']}$2:{C['live_pnl_pct']},'{T}'!{st_c}$2:{st_c}=\"OPEN\"))+1-{i}),\"\")",
            f"=IFERROR(INDEX(FILTER('{T}'!{days_c}$2:{days_c},'{T}'!{st_c}$2:{st_c}=\"OPEN\"),COUNTA(FILTER('{T}'!{days_c}$2:{days_c},'{T}'!{st_c}$2:{st_c}=\"OPEN\"))+1-{i}),\"\")",
            f"=IFERROR(INDEX(FILTER('{T}'!{eta_c}$2:{eta_c},'{T}'!{st_c}$2:{st_c}=\"OPEN\"),COUNTA(FILTER('{T}'!{eta_c}$2:{eta_c},'{T}'!{st_c}$2:{st_c}=\"OPEN\"))+1-{i}),\"\")",
        ])

    rows.append(["", "", "", "", "", "", "", ""])

    # P&L chart section (SPARKLINE)
    rows.append(["📉 P&L CHART (closed trades)", "", "", "", "", "", "", ""])
    rows.append([
        f'=IFERROR(SPARKLINE(FILTER(\'{T}\'!{pnl_c}2:{pnl_c},\'{T}\'!{pnl_c}2:{pnl_c}<>""),'
        f'{{"charttype","column";"negcolor","#F43F5E";"color","#10B981";"ymin",'
        f'MIN(\'{T}\'!{pnl_c}2:{pnl_c});"ymax",MAX(\'{T}\'!{pnl_c}2:{pnl_c})}})'
        f',"No closed trades yet")',
        "", "", "", "", "", "", "",
    ])
    rows.append(["← Each bar = one closed trade | Green = profit, Red = loss", "", "", "", "", "", "", ""])

    summary.clear()
    summary.update(rows, "A1", value_input_option="USER_ENTERED")

    # Format the summary sheet
    ws_id = summary.id
    n = 8  # columns in summary
    sh.batch_update({"requests": [
        # Expand columns
        {"updateSheetProperties": {
            "properties": {"sheetId": ws_id, "gridProperties": {"columnCount": 10}},
            "fields": "gridProperties.columnCount"}},
        # Title row
        _repeat(ws_id, 0, 1, 0, n,
            _cell_fmt(bg=C_HEADER_BG, fg=C_HEADER_FG, bold=True),
            "backgroundColor,textFormat"),
        # Section header rows bold
        _repeat(ws_id, 3, 4, 0, n,
            _cell_fmt(bg=_rgb(30,41,59), fg=C_HEADER_FG, bold=True),
            "backgroundColor,textFormat"),
        _repeat(ws_id, 12, 13, 0, n,
            _cell_fmt(bg=_rgb(6,78,59), fg=_rgb(209,250,229), bold=True),
            "backgroundColor,textFormat"),
        _repeat(ws_id, 24, 25, 0, n,
            _cell_fmt(bg=_rgb(30,41,59), fg=C_HEADER_FG, bold=True),
            "backgroundColor,textFormat"),
        # Column header row (row 14) for open positions
        _repeat(ws_id, 13, 14, 0, n,
            _cell_fmt(bg=_rgb(51,65,85), fg=C_HEADER_FG, bold=True, halign="CENTER"),
            "backgroundColor,textFormat,horizontalAlignment"),
        # Open positions rows alternate shading
        _repeat(ws_id, 14, 22, 0, n,
            _cell_fmt(halign="CENTER"), "horizontalAlignment"),
        # Sparkline row height
        {"updateDimensionProperties": {
            "range": {"sheetId": ws_id, "dimension": "ROWS",
                      "startIndex": 25, "endIndex": 26},
            "properties": {"pixelSize": 140}, "fields": "pixelSize"}},
        # Column widths for summary
        {"updateDimensionProperties": {
            "range": {"sheetId": ws_id, "dimension": "COLUMNS",
                      "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 200}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": ws_id, "dimension": "COLUMNS",
                      "startIndex": 1, "endIndex": 8},
            "properties": {"pixelSize": 130}, "fields": "pixelSize"}},
    ]})
    return summary


# ── Local Excel sink ──────────────────────────────────────────────────────────
class LocalExcelSink:
    def __init__(self, path: str):
        self.path = path

    def _load(self):
        import openpyxl
        from openpyxl.styles import PatternFill, Font, Alignment
        from openpyxl.utils import get_column_letter

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
            hdr_fill  = PatternFill("solid", fgColor="0F172A")
            hdr_font  = Font(bold=True, color="F8FAFC", size=10)
            hdr_align = Alignment(horizontal="center", vertical="center")
            for cell in ws[1]:
                cell.fill, cell.font, cell.alignment = hdr_fill, hdr_font, hdr_align
            ws.row_dimensions[1].height = 22
            excel_widths = [
                6, 17, 13, 6, 10, 10, 7, 10, 7, 10, 7, 6, 7,
                7, 11, 12, 6, 6, 7, 7, 6, 6, 6,
                50, 17, 10, 13, 9, 9,
                10, 22, 17, 10, 11, 8, 50,
            ]
            for ci, w in enumerate(excel_widths[:len(KEYS)], 1):
                ws.column_dimensions[get_column_letter(ci)].width = w
        return wb, ws

    def existing_ids(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        import openpyxl
        wb = openpyxl.load_workbook(self.path, read_only=True)
        ws = wb.active
        ci_st = _CI["status"]
        out = {}
        for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not row or row[0] in (None, ""):
                continue
            try:
                tid = int(row[0])
            except (ValueError, TypeError):
                continue
            status = row[ci_st] if len(row) > ci_st else ""
            out[tid] = (i, status or "")
        wb.close()
        return out

    def append(self, row: dict):
        import openpyxl
        from openpyxl.styles import PatternFill, Alignment
        direction = row.get("direction", "")
        with _FILE_LOCK:
            wb, ws = self._load()
            # Skip formula strings in Excel
            values = []
            for k in KEYS:
                v = row.get(k, "")
                if isinstance(v, str) and v.startswith("="):
                    v = ""
                values.append(v)
            ws.append(values)
            ri = ws.max_row
            if direction == "BUY":
                fill = PatternFill("solid", fgColor="F0FDF4")
            elif direction == "SELL":
                fill = PatternFill("solid", fgColor="FFF1F2")
            else:
                fill = None
            if fill:
                for cell in ws[ri]:
                    cell.fill = fill
            for k in ("why_bought", "expert_note"):
                ws.cell(ri, _CI[k]+1).alignment = Alignment(wrap_text=True, vertical="top")
            wb.save(self.path)

    def update(self, row_number: int, partial: dict):
        import openpyxl
        from openpyxl.styles import PatternFill
        with _FILE_LOCK:
            wb, ws = self._load()
            for k, v in partial.items():
                if k in KEYS:
                    ws.cell(row=row_number, column=_CI[k]+1, value=v)
            pnl = partial.get("pnl_rs")
            if pnl is not None:
                fill = PatternFill("solid", fgColor="D1FAE5" if float(pnl) >= 0 else "FEE2E2")
                ws.cell(row_number, _CI["pnl_rs"]+1).fill = fill
            status = partial.get("status", "")
            if status:
                fill = PatternFill("solid",
                    fgColor="D1FAE5" if "T1 HIT" in status else
                            "FEE2E2" if "STOPPED" in status else "F8FAFC")
                ws.cell(row_number, _CI["status"]+1).fill = fill
            wb.save(self.path)


# ── Google Sheets sink ────────────────────────────────────────────────────────
class GoogleSheetsSink:
    def __init__(self):
        self._ws = self._sh = None
        self._formatted = False

    def _get_credentials(self):
        import pickle
        from google.auth.transport.requests import Request
        token_path = settings.GOOGLE_OAUTH_TOKEN_PATH
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = None
        if os.path.exists(token_path):
            with open(token_path, "rb") as f:
                creds = pickle.load(f)
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(token_path, "wb") as f:
                    pickle.dump(creds, f)
                return creds
            except Exception:
                pass
        secret = settings.GOOGLE_OAUTH_CLIENT_SECRET_JSON
        if secret and os.path.exists(secret):
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(secret, scopes)
            creds = flow.run_local_server(port=0)
            os.makedirs(os.path.dirname(token_path) or ".", exist_ok=True)
            with open(token_path, "wb") as f:
                pickle.dump(creds, f)
            return creds
        if settings.GOOGLE_SERVICE_ACCOUNT_JSON:
            from google.oauth2.service_account import Credentials
            return Credentials.from_service_account_file(
                settings.GOOGLE_SERVICE_ACCOUNT_JSON, scopes=scopes)
        raise RuntimeError("No Google credentials configured")

    def _worksheet(self):
        if self._ws is not None:
            return self._ws, self._sh
        import gspread
        gc = gspread.authorize(self._get_credentials())
        sh = gc.open_by_key(settings.GOOGLE_SHEETS_ID)
        title = settings.GOOGLE_SHEETS_WORKSHEET
        try:
            ws = sh.worksheet(title)
            existing = ws.row_values(1) if ws.row_count > 0 else []
            if existing and existing != HEADERS:
                ws.clear()
                ws.update([HEADERS], "A1")
                self._formatted = False
        except Exception:
            ws = sh.add_worksheet(title=title, rows=2000, cols=len(KEYS)+4)
            ws.update([HEADERS], "A1")
            self._formatted = False
        self._ws, self._sh = ws, sh
        return ws, sh

    def _ensure_formatted(self, ws, sh):
        if self._formatted:
            return
        try:
            _setup_trades_sheet(sh, ws)
            _add_summary_sheet(sh, ws.title)
            self._formatted = True
        except Exception as exc:
            logger.warning(f"[sheet_logger] formatting failed (non-fatal): {exc}")

    def existing_ids(self) -> dict:
        ws, sh = self._worksheet()
        self._ensure_formatted(ws, sh)
        records = ws.get_all_values()
        ci_st = _CI["status"]
        out = {}
        for i, row in enumerate(records[1:], start=2):
            if not row or not row[0]:
                continue
            try:
                tid = int(row[0])
            except (ValueError, TypeError):
                continue
            status = row[ci_st] if len(row) > ci_st else ""
            out[tid] = (i, status or "")
        return out

    def append(self, row: dict):
        ws, _ = self._worksheet()
        # Know the next row number BEFORE appending for formula substitution
        nrows = len(ws.get_all_values())
        r = nrows + 1
        values = []
        for k in KEYS:
            v = row.get(k, "")
            if isinstance(v, str) and "{ROW}" in v:
                v = v.replace("{ROW}", str(r))
            values.append(v)
        ws.append_row(values, value_input_option="USER_ENTERED")

    def update(self, row_number: int, partial: dict):
        ws, _ = self._worksheet()
        for k, v in partial.items():
            if k in KEYS:
                ws.update_cell(row_number, _CI[k]+1, v)


# ── Factory + sync engine ─────────────────────────────────────────────────────
def _make_sink():
    backend = (getattr(settings, "SHEET_LOG_BACKEND", "local") or "local").lower()
    if backend == "google":
        if not settings.google_sheets_available:
            logger.warning("[sheet_logger] google backend: no credentials — skipping")
            return None
        return GoogleSheetsSink()
    return LocalExcelSink(settings.SHEET_LOG_LOCAL_PATH)


def _sync_blocking(open_rows, close_updates):
    sink = _make_sink()
    if sink is None:
        return 0, 0
    appended = updated = 0
    for row in open_rows:
        try:
            sink.append(row)
            appended += 1
        except Exception as exc:
            logger.warning(f"[sheet_logger] append {row.get('trade_id')}: {exc}")
    for row_number, partial in close_updates:
        try:
            sink.update(row_number, partial)
            updated += 1
        except Exception as exc:
            logger.warning(f"[sheet_logger] update row {row_number}: {exc}")
    return appended, updated


async def sync_journal(session: AsyncSession, *, limit: int = 500) -> dict:
    """Idempotently reconcile the spreadsheet with the trades table."""
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

        need_hub = [t.symbol for t in rows if t.id not in existing]
        hub_map: dict = {}
        if need_hub:
            ns = {f"{s.split('.')[0]}.NS" for s in need_hub}
            for m in (await session.execute(
                select(MasterIntelligenceScore)
                .where(MasterIntelligenceScore.symbol.in_(ns))
                .order_by(MasterIntelligenceScore.scored_at.desc())
            )).scalars().all():
                hub_map.setdefault(m.symbol, m)

        open_rows, close_updates = [], []
        for t in rows:
            if t.id not in existing:
                hub = hub_map.get(f"{t.symbol.split('.')[0]}.NS")
                open_rows.append(_open_row(t, hub))  # row_num set by sink.append
            else:
                row_number, sheet_status = existing[t.id]
                if "OPEN" in str(sheet_status) and t.status != TradeStatus.OPEN:
                    close_updates.append((row_number, _close_partial(t)))

        if not open_rows and not close_updates:
            return {"enabled": True, "appended": 0, "updated": 0,
                    "in_sheet": len(existing)}

        appended, updated = await asyncio.to_thread(_sync_blocking, open_rows, close_updates)
        logger.info(
            f"[sheet_logger] sync: +{appended} new, ~{updated} closed "
            f"(backend={settings.SHEET_LOG_BACKEND})"
        )
        return {"enabled": True, "appended": appended, "updated": updated}
    except Exception as exc:
        logger.warning(f"[sheet_logger] sync_journal failed (non-fatal): {exc}")
        return {"enabled": True, "error": str(exc)}
