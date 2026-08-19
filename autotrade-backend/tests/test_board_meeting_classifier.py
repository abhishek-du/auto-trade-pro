"""Tests for the NSE board-meeting sub-type classifier (2026-08-19).

Every `bm_desc` string here is copied verbatim from a real filing in
market_events. That matters: each of the four parser bugs fixed while building
this was invisible to invented examples and only appeared when the classifier
was run over all 1,783 live filings.

  * re-scheduled filings quote the OLD meeting date in prose, so a bare date
    regex reads it as the reporting period
  * Exide writes "period endING 30th June 2026", not "ended"
  * GAIL and National Aluminium write "quarter ended 30.06.2026"
  * Nahar Poly writes "period ended June 2026" with no day at all, and
    Starteck writes "30th June,. 2026" — comma then dot
"""
from __future__ import annotations

from datetime import date

import pytest

from engine.board_meeting_classifier import (
    FRESH_RESULT, RESTATEMENT, STALE_QUARTER,
    classify_board_meeting,
)

AUG19 = date(2026, 8, 19)


class TestFreshResults:
    @pytest.mark.parametrize("desc,expected_period", [
        ("To consider and approve the unaudited standalone and consolidated financial "
         "results of the Company for the quarter ended June 30, 2026",
         date(2026, 6, 30)),
        ("To consider and approve the financial results for the period ended Jun 30, 2026",
         date(2026, 6, 30)),
        ("To consider and approve the Quarterly Unaudited Financial results of the "
         "Company for the period ended June 2026 and Other business.",
         date(2026, 6, 30)),
        ("To consider the Un-Audited Financial Results (Standalone &Consolidated) for "
         "the Quarter ended 30.06.2026",
         date(2026, 6, 30)),
        ("Notice of Board Meeting to consider Unaudited Financial Results for the "
         "quarter ended on 30.06.2026.",
         date(2026, 6, 30)),
        ("To consider and approve unaudited financial results of the Company for the "
         "period ending 30th June 2026",
         date(2026, 6, 30)),
        ("Intimation of Board Meeting for Results & Closure of Trading Window for the "
         "quarter ended 30th June,. 2026",
         date(2026, 6, 30)),
    ])
    def test_current_quarter_is_a_fresh_result(self, desc, expected_period):
        t = classify_board_meeting(desc, "Financial Results", AUG19)
        assert t.subtype == FRESH_RESULT
        assert t.period_end == expected_period
        assert t.period_confirmed is True
        assert t.is_tradable_catalyst is True


class TestOldMeetingDatesAreNotPeriods:
    """The trap that forced the "ended" anchor."""

    def test_rescheduled_meeting_date_is_not_read_as_the_period(self):
        desc = ("IFBIND : 06-Aug-2026 :  The Company has informed the Exchange that a "
                "Board meeting to be held on July 28, 2026 has been re-scheduled. "
                "Further, the Company has informed that the Board will consider the "
                "unaudited financial results for the quarter ended June 30, 2026")
        t = classify_board_meeting(desc, "Financial Results", AUG19)
        assert t.period_end == date(2026, 6, 30), "picked up the meeting date, not the period"
        assert t.is_rescheduled is True
        assert t.subtype == FRESH_RESULT

    def test_reschedule_notice_with_no_period_still_flags_the_delay(self):
        desc = ("We wish to inform you that due to unavoidable circumstances, Board "
                "Meeting has been re-scheduled and will now be held on Wednesday, "
                "5th August 2026")
        t = classify_board_meeting(desc, "Financial Results", AUG19)
        assert t.is_rescheduled is True
        assert t.period_confirmed is False


class TestStaleQuarter:
    @pytest.mark.parametrize("desc", [
        "To consider and approve the financial results for the period ended March 31, 2026",
        "To consider and approve the financial results for the period ended March 31, 2026 "
        "and Fund Raising",
    ])
    def test_old_quarter_filed_late(self, desc):
        t = classify_board_meeting(desc, "Financial Results", AUG19)
        assert t.subtype == STALE_QUARTER
        assert t.period_end == date(2026, 3, 31)
        assert t.is_tradable_catalyst is False, "a months-late quarter is not news about now"


class TestRestatement:
    def test_revision_of_old_numbers(self):
        desc = ("To consider, approve and take on record, the revision of standalone and "
                "consolidated financial statements of the Company for the quarter and "
                "financial year ended 31st March, 2026")
        t = classify_board_meeting(desc, "Financial Results", AUG19)
        assert t.subtype == RESTATEMENT
        assert t.is_restatement is True
        assert t.is_tradable_catalyst is False

    def test_restated_wording(self):
        desc = ("To consider and approve the Restated Consolidated Financial Statements "
                "of the Company for the Financial Year ended March 31, 2026")
        t = classify_board_meeting(desc, "Financial Results", AUG19)
        assert t.subtype == RESTATEMENT

    def test_fresh_result_that_also_restates_keeps_both_facts(self):
        """ORCHPHARMA, 14-Aug. Forcing a single label would discard half of it,
        so the primary sub-type stays FRESH and the restatement is a flag."""
        desc = ("To consider and approve the unaudited limited reviewed financial results "
                "(standalone and consolidated) for the period ended June 30, 2026 and "
                "restated financial statements")
        t = classify_board_meeting(desc, "Financial Results", AUG19)
        assert t.subtype == FRESH_RESULT
        assert t.is_restatement is True
        # A filing that revises prior numbers is not a clean earnings catalyst.
        assert t.is_tradable_catalyst is False


class TestBoilerplateNotices:
    """~1% of filings name no period, and they include large caps (TRENT, PFC,
    ALKEM). Dropping them would suppress real catalysts over a terse notice."""

    @pytest.mark.parametrize("desc", [
        "Notice of Board Meeting",
        "Intimation of Board Meeting",
        "Notice of meeting of the Board of Directors of Alkem Laboratories Limited ( the Company )",
    ])
    def test_inferred_as_fresh_but_marked_unconfirmed(self, desc):
        t = classify_board_meeting(desc, "Financial Results", AUG19)
        assert t.subtype == FRESH_RESULT
        assert t.period_confirmed is False
        assert t.period_end is None

    def test_empty_input_does_not_raise(self):
        for bad in (None, "", "   "):
            t = classify_board_meeting(bad, None, AUG19)
            assert t.period_confirmed is False


class TestActions:
    @pytest.mark.parametrize("purpose,expected", [
        ("Financial Results", []),
        ("Financial Results/Other business matters", []),
        ("Financial Results/Dividend", ["DIVIDEND"]),
        ("Financial Results/Fund Raising", ["FUND_RAISING"]),
        ("Financial Results/Stock Split", ["STOCK_SPLIT"]),
        ("Financial Results/Dividend/Fund Raising/Other business matters",
         ["DIVIDEND", "FUND_RAISING"]),
        ("Financial Results/Voluntary Delisting/Other business matters",
         ["VOLUNTARY_DELISTING"]),
    ])
    def test_actions_parsed_from_purpose(self, purpose, expected):
        t = classify_board_meeting("results for the quarter ended June 30, 2026",
                                   purpose, AUG19)
        assert t.actions == expected

    def test_missing_purpose_is_harmless(self):
        assert classify_board_meeting("x", None, AUG19).actions == []


class TestQuarterReference:
    """The reference quarter is derived from the event date, so this does not
    rot into calling everything stale once the calendar moves on."""

    def test_same_period_is_fresh_in_august_but_stale_in_november(self):
        desc = "results for the quarter ended June 30, 2026"
        assert classify_board_meeting(desc, None, date(2026, 8, 19)).subtype == FRESH_RESULT
        assert classify_board_meeting(desc, None, date(2026, 11, 20)).subtype == STALE_QUARTER

    def test_september_quarter_is_fresh_in_november(self):
        desc = "results for the quarter ended September 30, 2026"
        t = classify_board_meeting(desc, None, date(2026, 11, 20))
        assert t.subtype == FRESH_RESULT

    def test_january_filing_uses_previous_december(self):
        desc = "results for the quarter ended December 31, 2026"
        t = classify_board_meeting(desc, None, date(2027, 1, 20))
        assert t.subtype == FRESH_RESULT


class TestPurity:
    def test_is_deterministic(self):
        desc = "results for the quarter ended June 30, 2026"
        a = classify_board_meeting(desc, "Financial Results/Dividend", AUG19)
        b = classify_board_meeting(desc, "Financial Results/Dividend", AUG19)
        assert a.to_dict() == b.to_dict()
