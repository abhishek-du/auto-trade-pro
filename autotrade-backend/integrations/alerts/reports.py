"""ReportLab PDF builder for the weekly report (Phase 5).

reportlab is pure-Python (no system libraries required, unlike
weasyprint's Pango/Cairo dependency) -- see requirements.txt.
"""
from __future__ import annotations

import io
import logging

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

_HEADER_COLOR = colors.HexColor("#0a1120")
_ACCENT = colors.HexColor("#2979FF")
_GRID_COLOR = colors.HexColor("#cccccc")


def build_weekly_pdf(
    *,
    metrics: dict,
    rebalance_trades: list,
    sector_weights: dict,
    position_weights: dict,
    ai_commentary: str = "",
    equity_curve_png: bytes | None = None,
    report_date: str = "",
) -> bytes:
    """Builds the weekly portfolio report PDF and returns its bytes.
    Never raises for empty/missing sections -- an early week with no
    rebalance signals or thin metrics history still produces a valid,
    if sparse, PDF rather than failing the whole weekly task."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ReportTitle", parent=styles["Title"], textColor=_HEADER_COLOR, fontSize=20)
    h2_style = ParagraphStyle("ReportH2", parent=styles["Heading2"], textColor=_HEADER_COLOR, spaceBefore=14)
    body_style = styles["BodyText"]

    story = [
        Paragraph(f"AutoTrade Pro — Weekly Portfolio Report", title_style),
        Paragraph(report_date, body_style),
        Spacer(1, 0.5 * cm),
    ]

    if equity_curve_png:
        story.append(Image(io.BytesIO(equity_curve_png), width=17 * cm, height=7.4 * cm))
        story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("Risk Metrics (30d)", h2_style))
    if metrics.get("sharpe_ratio") is not None:
        metric_rows = [
            ["Metric", "Value"],
            ["Portfolio Return", f"{metrics.get('portfolio_return', 0):+.2f}%"],
            ["NIFTY Benchmark", f"{metrics.get('benchmark_return', 0):+.2f}%"],
            ["Portfolio Beta", f"{metrics.get('portfolio_beta', 0):.2f}"],
            ["Std Deviation", f"{metrics.get('portfolio_stddev', 0):.2f}%"],
            ["Sharpe Ratio", f"{metrics.get('sharpe_ratio', 0):.2f}"],
            ["Treynor Ratio", f"{metrics.get('treynor_ratio', 0):.2f}"],
            ["Jensen's Alpha", f"{metrics.get('jensens_alpha', 0):+.2f}%"],
        ]
        table = Table(metric_rows, colWidths=[8 * cm, 8 * cm])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _HEADER_COLOR),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, _GRID_COLOR),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(table)
    else:
        story.append(Paragraph("Insufficient trade history yet for risk metrics.", body_style))

    story.append(Paragraph("Rebalance Signals", h2_style))
    if rebalance_trades:
        rebal_rows = [["Action", "Symbol", "Current %", "Target %", "Drift %"]]
        for t in rebalance_trades[:15]:
            rebal_rows.append([
                t["action"], t["symbol"].replace(".NS", ""),
                f"{t['current_weight']:.1f}", f"{t['target_weight']:.1f}", f"{t['drift']:.1f}",
            ])
        table = Table(rebal_rows, colWidths=[2.5 * cm, 4 * cm, 3 * cm, 3 * cm, 3 * cm])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _HEADER_COLOR),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, _GRID_COLOR),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(table)
    else:
        story.append(Paragraph("Portfolio is within tolerance — no rebalancing needed this week.", body_style))

    if sector_weights:
        story.append(Paragraph("Top Sector Exposure", h2_style))
        top_sectors = sorted(sector_weights.items(), key=lambda x: x[1], reverse=True)[:8]
        story.append(Paragraph(
            "  ·  ".join(f"{s}: {w:.1f}%" for s, w in top_sectors), body_style,
        ))

    if ai_commentary:
        story.append(Paragraph("AI Commentary", h2_style))
        story.append(Paragraph(ai_commentary, body_style))

    doc.build(story)
    return buf.getvalue()
