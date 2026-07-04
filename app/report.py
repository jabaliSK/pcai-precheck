"""PDF report generation using ReportLab (pure-Python, no system deps)."""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak,
)

from .checks.base import STATUS_PASS, STATUS_WARN, STATUS_FAIL, STATUS_SKIP

STATUS_COLORS = {
    STATUS_PASS: colors.HexColor("#1a7f37"),
    STATUS_WARN: colors.HexColor("#bf8700"),
    STATUS_FAIL: colors.HexColor("#cf222e"),
    STATUS_SKIP: colors.HexColor("#57606a"),
}
STATUS_BG = {
    STATUS_PASS: colors.HexColor("#dafbe1"),
    STATUS_WARN: colors.HexColor("#fff8c5"),
    STATUS_FAIL: colors.HexColor("#ffebe9"),
    STATUS_SKIP: colors.HexColor("#eaeef2"),
}


def _fmt_time(ts: float | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _status_para(status: str, styles) -> Paragraph:
    color = STATUS_COLORS.get(status, colors.black)
    return Paragraph(
        f'<b><font color="{color.hexval()}">{status.upper()}</font></b>',
        styles["Normal"],
    )


def build_pdf(state: dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        title="pcai-precheck report",
        author="pcai-precheck",
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Small", parent=styles["Normal"], fontSize=8, leading=10))
    styles.add(ParagraphStyle(name="Mono",  parent=styles["Code"],   fontSize=7, leading=9))

    story: list = []

    # Header
    story.append(Paragraph("<b>PCAI Firewall / Whitelist Pre-check Report</b>", styles["Title"]))
    summary = state.get("summary", {})
    meta = [
        ["Host",         state.get("hostname", "?")],
        ["Version",      state.get("version", "?")],
        ["Started",      _fmt_time(state.get("started_at"))],
        ["Finished",     _fmt_time(state.get("finished_at"))],
        ["Status",       "running" if state.get("running") else "complete"],
        ["Pass / Warn / Fail",
         f"{summary.get(STATUS_PASS,0)} / {summary.get(STATUS_WARN,0)} / {summary.get(STATUS_FAIL,0)}"],
    ]
    t = Table(meta, colWidths=[1.4 * inch, 5.4 * inch])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f6f8fa")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d7de")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d0d7de")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))

    cfg = state.get("config", {})
    if cfg:
        story.append(Paragraph("<b>Configuration</b>", styles["Heading3"]))
        cfg_rows = [[k, str(v)] for k, v in cfg.items()]
        ct = Table(cfg_rows, colWidths=[1.6 * inch, 5.2 * inch])
        ct.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f6f8fa")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d7de")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d0d7de")),
        ]))
        story.append(ct)
        story.append(Spacer(1, 12))

    # Results grouped by category
    results = state.get("results", [])
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r)

    for cat, items in by_cat.items():
        story.append(Paragraph(f"<b>{cat}</b>", styles["Heading2"]))
        rows = [["#", "Name", "Tool", "Target", "Attempts", "Duration", "Status", "Detail"]]
        bg_rows: list[tuple[int, str]] = []
        for i, r in enumerate(items, 1):
            rows.append([
                str(i),
                Paragraph(r["name"], styles["Small"]),
                r["tool"],
                Paragraph(r["target"], styles["Small"]),
                str(r.get("attempts", 1)),
                f'{r.get("duration_ms", 0)} ms',
                _status_para(r["status"], styles),
                Paragraph(r.get("detail", "") or "", styles["Small"]),
            ])
            bg_rows.append((i, r["status"]))
        tbl = Table(
            rows,
            colWidths=[0.3 * inch, 1.6 * inch, 0.6 * inch, 1.6 * inch,
                       0.55 * inch, 0.6 * inch, 0.6 * inch, 1.8 * inch],
            repeatRows=1,
        )
        style = [
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaeef2")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d7de")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d0d7de")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        for row_idx, status in bg_rows:
            bg = STATUS_BG.get(status)
            if bg is not None:
                style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), bg))
        tbl.setStyle(TableStyle(style))
        story.append(tbl)
        story.append(Spacer(1, 12))

    # Failure appendix (with captured output)
    failures = [r for r in results if r["status"] in (STATUS_FAIL, STATUS_WARN)]
    if failures:
        story.append(PageBreak())
        story.append(Paragraph("<b>Failure & warning details</b>", styles["Heading2"]))
        for r in failures:
            story.append(Paragraph(
                f'<b>[{r["status"].upper()}]</b> {r["category"]} / {r["name"]}',
                styles["Heading4"],
            ))
            story.append(Paragraph(f'Target: {r["target"]}', styles["Small"]))
            story.append(Paragraph(f'Detail: {r.get("detail","")}', styles["Small"]))
            out = (r.get("output") or "").strip()
            if out:
                # Preserve line breaks for the pre-formatted output.
                escaped = (out.replace("&", "&amp;")
                              .replace("<", "&lt;").replace(">", "&gt;")
                              .replace("\n", "<br/>"))
                story.append(Paragraph(f"<font face='Courier'>{escaped}</font>", styles["Mono"]))
            story.append(Spacer(1, 8))

    doc.build(story)
    return buf.getvalue()
