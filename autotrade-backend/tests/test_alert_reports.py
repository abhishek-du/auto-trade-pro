"""Tests for the weekly report (Phase 5): ReportPayload rendering, the
reportlab PDF builder, and router integration.

Run:
    cd autotrade-backend
    .venv/bin/python -m pytest tests/test_alert_reports.py -v --tb=short
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from integrations.alerts import (
    AlertAction,
    AlertCategory,
    AlertEvent,
    ReportPayload,
    Severity,
    publish,
)
from integrations.alerts.reports import build_weekly_pdf
from integrations.alerts.templates import render_async
from tests.test_alert_router import assert_balanced_html, assert_no_markdown_leakage

_METRICS = {
    "portfolio_return": 12.5, "benchmark_return": 9.1, "portfolio_beta": 0.85,
    "portfolio_stddev": 14.2, "sharpe_ratio": 1.35, "treynor_ratio": 0.11,
    "jensens_alpha": 3.2,
}
_REBALANCE = [
    {"action": "BUY", "symbol": "TCS.NS", "current_weight": 2.0, "target_weight": 8.0, "drift": 6.0, "reason": "underweight"},
    {"action": "SELL", "symbol": "INFY.NS", "current_weight": 15.0, "target_weight": 8.0, "drift": -7.0, "reason": "overweight"},
]
_SECTORS = {"IT": 25.0, "Banking": 20.0, "Pharma": 10.0}
_POSITIONS = {"TCS.NS": 8.0, "INFY.NS": 8.0}


def test_render_report_with_metrics_is_decision_first():
    payload = ReportPayload(
        metrics=_METRICS, rebalance_trades=_REBALANCE,
        sector_weights=_SECTORS, position_weights=_POSITIONS,
        ai_commentary="Solid week overall.",
    )
    event = AlertEvent(category=AlertCategory.WEEKLY_REPORT, action=AlertAction.REPORT,
                        severity=Severity.INFO, payload=payload)
    text = render(event)
    assert_balanced_html(text)
    assert_no_markdown_leakage(text)
    assert "rebalance signal" in text
    # Decision (rebalance count) appears before the detailed risk-metrics table.
    assert text.index("rebalance signal") < text.index("Risk Metrics")


def test_render_report_with_no_metrics_or_rebalance():
    """Early-week / thin-history case: no crash, sensible fallback copy."""
    payload = ReportPayload(
        metrics={"sharpe_ratio": None}, rebalance_trades=[],
        sector_weights={}, position_weights={},
    )
    event = AlertEvent(category=AlertCategory.WEEKLY_REPORT, action=AlertAction.REPORT,
                        severity=Severity.INFO, payload=payload)
    text = render(event)
    assert_balanced_html(text)
    assert "within tolerance" in text
    assert "Insufficient history" in text


def test_build_weekly_pdf_produces_valid_pdf():
    pdf_bytes = build_weekly_pdf(
        metrics=_METRICS, rebalance_trades=_REBALANCE,
        sector_weights=_SECTORS, position_weights=_POSITIONS,
        ai_commentary="Test commentary.", equity_curve_png=None,
        report_date="2026-08-05",
    )
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 500


def test_build_weekly_pdf_handles_empty_sections():
    """No rebalance trades, no metrics history yet, no equity curve -- must
    still produce a valid PDF, not raise."""
    pdf_bytes = build_weekly_pdf(
        metrics={"sharpe_ratio": None}, rebalance_trades=[],
        sector_weights={}, position_weights={}, report_date="2026-08-05",
    )
    assert pdf_bytes.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_weekly_report_sends_one_message_and_one_pdf():
    """publish() for a WEEKLY_REPORT with pdf_bytes set must send exactly
    one text message and one companion PDF document -- not the two
    separate messages the old pre-merge tasks used to send."""
    payload = ReportPayload(
        metrics=_METRICS, rebalance_trades=_REBALANCE,
        sector_weights=_SECTORS, position_weights=_POSITIONS,
        pdf_bytes=b"%PDF-fake",
    )
    with patch("integrations.alerts.router.settings") as mock_settings, \
         patch("integrations.telegram_service._post", new_callable=AsyncMock) as mock_post, \
         patch("integrations.telegram_service._post_document", new_callable=AsyncMock) as mock_doc:
        mock_settings.telegram_available = True
        mock_settings.TELEGRAM_MIN_SEVERITY = "INFO"
        mock_post.return_value = 123456
        await publish(AlertEvent(
            category=AlertCategory.WEEKLY_REPORT, action=AlertAction.REPORT,
            severity=Severity.INFO, payload=payload,
        ))
    mock_post.assert_awaited_once()
    mock_doc.assert_awaited_once()
    assert mock_doc.call_args.kwargs.get("reply_to_message_id") == 123456
    assert mock_doc.call_args[0][0] == b"%PDF-fake"


@pytest.mark.asyncio
async def test_weekly_report_without_pdf_sends_no_document():
    payload = ReportPayload(
        metrics=_METRICS, rebalance_trades=[], sector_weights={}, position_weights={},
        pdf_bytes=None,
    )
    with patch("integrations.alerts.router.settings") as mock_settings, \
         patch("integrations.telegram_service._post", new_callable=AsyncMock) as mock_post, \
         patch("integrations.telegram_service._post_document", new_callable=AsyncMock) as mock_doc:
        mock_settings.telegram_available = True
        mock_settings.TELEGRAM_MIN_SEVERITY = "INFO"
        await publish(AlertEvent(
            category=AlertCategory.WEEKLY_REPORT, action=AlertAction.REPORT,
            severity=Severity.INFO, payload=payload,
        ))
    mock_post.assert_awaited_once()
    mock_doc.assert_not_awaited()
