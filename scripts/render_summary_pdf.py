#!/usr/bin/env python3
"""Render SYSTEM_SUMMARY.md to a shareable PDF. Menlo for the ASCII diagram."""

import re
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (PageBreak, Paragraph, Preformatted,
                                SimpleDocTemplate, Spacer, Table,
                                TableStyle)

SRC = Path("/Users/samkim/Harnessv1/SYSTEM_SUMMARY.md")
OUT = Path("/Users/samkim/Desktop/SYSTEM_SUMMARY.pdf")

pdfmetrics.registerFont(TTFont("Menlo", "/System/Library/Fonts/Menlo.ttc", subfontIndex=0))

styles = {
    "title": ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=17, leading=21,
                            spaceAfter=4, textColor="#111111"),
    "subtitle": ParagraphStyle("st", fontName="Helvetica-Oblique", fontSize=9.5, leading=12,
                               spaceAfter=10, textColor="#555555"),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12.5, leading=15,
                         spaceBefore=10, spaceAfter=4, textColor="#0a2540"),
    "body": ParagraphStyle("b", fontName="Helvetica", fontSize=9.5, leading=13, spaceAfter=5),
    "bullet": ParagraphStyle("bl", fontName="Helvetica", fontSize=9.5, leading=13,
                             leftIndent=14, bulletIndent=4, spaceAfter=3),
    "mono": ParagraphStyle("m", fontName="Menlo", fontSize=6.9, leading=8.3),
    "mono_page": ParagraphStyle("mp", fontName="Menlo", fontSize=7.3, leading=9.1),
}


def md_inline(s):
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"\*(.+?)\*", r"<i>\1</i>", s)
    s = s.replace("`", "")
    s = s.replace("→", "-&gt;").replace("↓", "").replace("▼", "v")
    return s


def flush_table(rows, story):
    if not rows:
        return
    data = [[md_inline(c.strip()) for c in r.strip().strip("|").split("|")] for r in rows]
    data = [r for i, r in enumerate(data) if not (i == 1 and set("".join(rows[1])) <= set("|-: "))]
    t = Table(data, colWidths=[None] * len(data[0]), hAlign="LEFT")
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), "#eef2f7"),
        ("GRID", (0, 0), (-1, -1), 0.4, "#c9d4e0"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))


def main():
    lines = SRC.read_text().split("\n")
    story = []
    table_buf = []
    in_code = False
    code_buf = []
    for line in lines:
        if line.strip().startswith("```"):
            if in_code:
                text = "\n".join(code_buf)
                style = styles["mono"] if len(text) > 1200 else styles["mono_page"]
                story.append(Preformatted(text, style))
                story.append(Spacer(1, 6))
                code_buf = []
            in_code = not in_code
            continue
        if in_code:
            code_buf.append(line.replace("→", "->").replace("↓", "|").replace("▼", "v"))
            continue
        if line.strip().startswith("|"):
            table_buf.append(line)
            continue
        flush_table(table_buf, story)
        table_buf = []
        s = line.strip()
        if not s or s == "---":
            if s == "---":
                story.append(Spacer(1, 2))
            continue
        if s.startswith("# "):
            story.append(Paragraph(md_inline(s[2:]), styles["title"]))
        elif s.startswith("## "):
            if "Page 2" in s:
                story.append(PageBreak())
            story.append(Paragraph(md_inline(s[3:]), styles["h2"]))
        elif s.startswith("- "):
            story.append(Paragraph(md_inline(s[2:]), styles["bullet"], bulletText="•"))
        elif s.startswith("*") and s.endswith("*") and not s.startswith("**"):
            story.append(Paragraph("<i>%s</i>" % md_inline(s.strip("*")), styles["body"]))
        else:
            story.append(Paragraph(md_inline(s), styles["body"]))
    flush_table(table_buf, story)

    doc = SimpleDocTemplate(str(OUT), pagesize=letter,
                            leftMargin=0.65 * inch, rightMargin=0.65 * inch,
                            topMargin=0.6 * inch, bottomMargin=0.6 * inch,
                            title="Datasheet Intelligence System — Summary & Architecture")
    doc.build(story)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
