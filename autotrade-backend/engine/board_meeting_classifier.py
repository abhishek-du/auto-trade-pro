"""Classify NSE board-meeting filings into actionable sub-types (2026-08-19).

The calendar labels every "Financial Results" board meeting as
EARNINGS / importance=HIGH. Measured across all 1,783 live filings, that one
label covers at least four events with opposite trading meanings:

    94.2%  fresh quarterly result   -> a genuine catalyst
     1.5%  re-scheduled meeting     -> the DELAY is itself the signal
     0.7%  old quarter being filed  -> months-late, not news about now
     0.3%  restatement / revision   -> prior numbers were wrong; a red flag,
                                       not an earnings surprise

NSE already ships the distinction in the filing's own `bm_desc` text and its
`purpose` field. Nothing here needs a model — it is the same deterministic
extraction as crawler/news_router.py, for the same reason: this runs over
every filing, and the signal is already in the string.

Two traps, both found by reading the real filings rather than imagining them:

1. Re-scheduled filings quote the OLD meeting date inside the narrative:
   "a Board meeting to be held on Jun 29, 2026 has been re-scheduled".
   A bare date regex reads that as the reporting period. So dates are only
   ever parsed after an explicit "...ended" anchor.

2. A filing can be BOTH fresh and a restatement — ORCHPHARMA, 14-Aug:
   "the unaudited ... results for the period ended June 30, 2026 and
   restated ...". Forcing one label would discard half the meaning, so
   restatement and rescheduling are exposed as flags alongside the primary
   sub-type rather than competing with it.
"""
from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date

# ── Primary sub-types ────────────────────────────────────────────────────────
FRESH_RESULT  = "FRESH_RESULT"   # results for the most recent completed quarter
STALE_QUARTER = "STALE_QUARTER"  # an older quarter, filed late
RESTATEMENT   = "RESTATEMENT"    # revising numbers already published
UNKNOWN       = "UNKNOWN"        # no period could be read from the filing

# Rescheduling / restatement wording, taken from the live corpus.
_RESCHEDULED_RE = re.compile(r"re-?schedul|postpon|deferr?ed", re.I)
_RESTATEMENT_RE = re.compile(r"\brevis(?:ed|ion)\b|\brestat(?:ed|ement)", re.I)

# Period end. The "ended" anchor is mandatory — see trap 1 in the module
# docstring. Both orderings occur live: "March 31, 2026" and "31st March 2026".
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_ALT = "|".join(_MONTHS)
# "ended" AND "ending" — Exide files "for the period ending 30th June 2026".
# Three date shapes all occur live:
#   "March 31, 2026"  |  "31st March 2026"  |  "30.06.2026"
# The numeric form is read as DD.MM.YYYY, which is the Indian filing
# convention; a first component above 12 confirms it, and the validation in
# _parse_period_end rejects anything that is not a real date.
#
# Punctuation between month and year is `[.,\s]*` rather than a fixed
# sequence: Starteck files "quarter ended 30th June,. 2026" — comma then dot.
# The month-and-year-only branch is LAST so it cannot steal a match from the
# fuller forms; it exists because Nahar Poly and Ace Integrated file
# "period ended June 2026" with no day at all, which resolves to that month's
# final day.
_PERIOD_RE = re.compile(
    r"end(?:ed|ing)\s+(?:on\s+)?"
    r"(?:"
    r"(?P<m1>" + _MONTH_ALT + r")[a-z]*[.,\s]*(?P<d1>\d{1,2})(?:st|nd|rd|th)?[.,\s]*(?P<y1>\d{4})"
    r"|"
    r"(?P<d2>\d{1,2})(?:st|nd|rd|th)?\s*(?P<m2>" + _MONTH_ALT + r")[a-z]*[.,\s]*(?P<y2>\d{4})"
    r"|"
    r"(?P<d3>\d{1,2})[./-](?P<m3>\d{1,2})[./-](?P<y3>\d{4})"
    r"|"
    r"(?P<m4>" + _MONTH_ALT + r")[a-z]*[.,\s]*(?P<y4>\d{4})"
    r")",
    re.I,
)


@dataclass
class BoardMeetingType:
    """What a "Financial Results" board meeting is actually about."""

    subtype:        str = UNKNOWN
    period_end:     date | None = None
    # False when the filing never named a period and the sub-type was inferred
    # from the base rate. Callers that need certainty must check this.
    period_confirmed: bool = False
    is_rescheduled: bool = False
    is_restatement: bool = False
    # Corporate actions ride along on the same filing. Parsed from `purpose`,
    # which NSE already delivers as a clean "/"-joined list — no need to hunt
    # for them in prose.
    actions:        list[str] = field(default_factory=list)

    @property
    def is_tradable_catalyst(self) -> bool:
        """True only for a fresh result that is not revising old numbers.

        A restatement moves the price but in the opposite direction from an
        earnings beat, and a months-late quarter is not news about now.
        Callers wanting those should read the flags directly.
        """
        return self.subtype == FRESH_RESULT and not self.is_restatement

    def to_dict(self) -> dict:
        return {
            "subtype": self.subtype,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "period_confirmed": self.period_confirmed,
            "is_rescheduled": self.is_rescheduled,
            "is_restatement": self.is_restatement,
            "actions": self.actions,
            "is_tradable_catalyst": self.is_tradable_catalyst,
        }


def _latest_quarter_end(as_of: date) -> date:
    """Most recent Indian fiscal quarter-end strictly before `as_of`.

    Derived rather than hardcoded so this does not silently rot into treating
    every filing as stale once the calendar rolls past Q1 FY27.
    """
    for m, d in ((12, 31), (9, 30), (6, 30), (3, 31)):
        qe = date(as_of.year, m, d)
        if qe < as_of:
            return qe
    return date(as_of.year - 1, 12, 31)


def _parse_period_end(text: str) -> date | None:
    """Last "...ended <date>" in the filing, or None.

    LAST rather than first: filings that mention several periods put the one
    being reported at the end ("...for the quarter and financial year ended
    31st March 2026"), and the earlier mentions are context.
    """
    last = None
    for m in _PERIOD_RE.finditer(text or ""):
        try:
            if m.group("m1"):
                mon = _MONTHS[m.group("m1")[:3].lower()]
                last = date(int(m.group("y1")), mon, int(m.group("d1")))
            elif m.group("m2"):
                mon = _MONTHS[m.group("m2")[:3].lower()]
                last = date(int(m.group("y2")), mon, int(m.group("d2")))
            elif m.group("d3"):
                last = date(int(m.group("y3")), int(m.group("m3")), int(m.group("d3")))
            else:
                # Month and year only ("period ended June 2026") — a reporting
                # period always runs to the month's end.
                mon = _MONTHS[m.group("m4")[:3].lower()]
                last = date(int(m.group("y4")), mon,
                            monthrange(int(m.group("y4")), mon)[1])
        except (ValueError, KeyError):
            continue
    return last


def _parse_actions(purpose: str | None) -> list[str]:
    """Corporate actions from NSE's `purpose` field.

    Live values are "/"-joined, e.g. "Financial Results/Dividend/Fund Raising".
    "Financial Results" and "Other business matters" are dropped — the former
    is true of every row here, the latter carries no information.
    """
    if not purpose:
        return []
    out = []
    for part in str(purpose).split("/"):
        p = part.strip()
        if not p or p.lower() in ("financial results", "other business matters"):
            continue
        out.append(p.upper().replace(" ", "_"))
    return out


def classify_board_meeting(
    bm_desc: str | None,
    purpose: str | None = None,
    event_date: date | None = None,
) -> BoardMeetingType:
    """Classify one NSE board-meeting filing. Pure and side-effect free."""
    text = bm_desc or ""
    period_end = _parse_period_end(text)
    is_resched = bool(_RESCHEDULED_RE.search(text))
    is_restate = bool(_RESTATEMENT_RE.search(text))
    actions = _parse_actions(purpose)

    if period_end is None:
        # No period named. Roughly 1% of filings are pure boilerplate — "Notice
        # of Board Meeting", "Intimation of Board Meeting" — and they include
        # large caps (TRENT, PFC, ALKEM, VIP Industries). Calling those UNKNOWN
        # and dropping them would suppress genuine earnings catalysts for major
        # names purely because their company secretary wrote a terse notice.
        #
        # The filing is still a confirmed "Financial Results" board meeting, and
        # 97.9% of all filings measured are for the current quarter, so the base
        # rate strongly favours FRESH. period_confirmed=False records that this
        # was inferred rather than read, so a caller needing certainty can tell
        # the difference. Restatement wording still overrides — it identifies
        # itself without any period.
        if is_restate:
            subtype = RESTATEMENT
        else:
            subtype = FRESH_RESULT
    else:
        ref = _latest_quarter_end(event_date or date.today())
        if period_end < ref:
            # An older quarter. Restating old numbers is the more specific
            # description, so it wins the label; plain late filing is stale.
            subtype = RESTATEMENT if is_restate else STALE_QUARTER
        else:
            # Current quarter. Stays FRESH_RESULT even when it also restates
            # (ORCHPHARMA) — the restatement is surfaced via the flag so
            # neither fact is lost.
            subtype = FRESH_RESULT

    return BoardMeetingType(
        subtype=subtype,
        period_end=period_end,
        period_confirmed=period_end is not None,
        is_rescheduled=is_resched,
        is_restatement=is_restate,
        actions=actions,
    )
