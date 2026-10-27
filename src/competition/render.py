"""Render an Analysis as a one-page TEN Capital memo PDF.

The page has a fixed budget, and users edit the prose freely, so rendering tries
a descending series of type scales and keeps the largest that fits on one page.
If even the smallest scale overflows, the content is shrunk into the frame as a
last resort and the result says so, so the UI can tell the user to trim.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date
from typing import Callable
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepInFrame, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from . import config
from . import template as T
from .schema import Analysis

# TEN Capital palette (ten-capital-brand).
INK = colors.HexColor("#000000")
BODY = colors.HexColor("#4B4F58")
MUTED = colors.HexColor("#7A7A7A")
CORAL = colors.HexColor("#ED5644")
TEAL = colors.HexColor("#4FC4D6")
AMBER = colors.HexColor("#F1A31F")
CREAM = colors.HexColor("#FFFDF6")
GRAY_BG = colors.HexColor("#F7F7F7")
RULE = colors.HexColor("#CBD6E2")

SUPPORT_COLOR = {"supported": TEAL, "partially supported": AMBER, "not supported": CORAL}

DISCLAIMER = (
    'Disclaimer: TEN is a "Funding as a Service" program. It is not a registered broker-dealer and does '
    "not offer investment advice or advise on the raising of capital through securities offerings. TEN does "
    "not recommend or otherwise suggest that any investor make an investment in a specific company, or that "
    "any company offer securities to a particular investor. TEN takes no part in the negotiation or execution "
    "of transactions for the purchase or sale of securities, and at no time has possession of funds or "
    "securities. No securities transactions are executed or negotiated on or through the TEN program. TEN "
    "receives no compensation in connection with the purchase or sale of securities."
)

PAGE_W, PAGE_H = letter
MARGIN_X = 0.55 * inch
MARGIN_TOP = 0.45 * inch
MARGIN_BOTTOM = 0.6 * inch
SCALES = (1.16, 1.12, 1.08, 1.04, 1.0, 0.96, 0.92, 0.88, 0.84, 0.80, 0.76)

RefFormatter = Callable[[list[str]], str]


@dataclass
class RenderResult:
    pdf: bytes
    scale: float
    forced_shrink: bool


_FONTS_READY = False
F_REG, F_SEMI, F_BOLD, F_ITAL = "Helvetica", "Helvetica-Bold", "Helvetica-Bold", "Helvetica-Oblique"


def _register_fonts() -> None:
    global _FONTS_READY, F_REG, F_SEMI, F_BOLD, F_ITAL
    if _FONTS_READY:
        return
    names = {"OpenSans": "Regular", "OpenSans-SemiBold": "SemiBold",
             "OpenSans-Bold": "Bold", "OpenSans-Italic": "Italic"}
    paths = {n: config.FONTS_DIR / f"OpenSans-{w}.ttf" for n, w in names.items()}
    if all(p.exists() for p in paths.values()):
        for name, path in paths.items():
            pdfmetrics.registerFont(TTFont(name, str(path)))
        F_REG, F_SEMI, F_BOLD, F_ITAL = "OpenSans", "OpenSans-SemiBold", "OpenSans-Bold", "OpenSans-Italic"
    _FONTS_READY = True


def _default_formatter(refs: list[str]) -> str:
    return ", ".join(refs)


def _styles(s: float) -> dict[str, ParagraphStyle]:
    body = 8.4 * s
    return {
        "eyebrow": ParagraphStyle("eyebrow", fontName=F_SEMI, fontSize=7.2 * s, leading=9 * s,
                                  textColor=CORAL),
        "title": ParagraphStyle("title", fontName=F_BOLD, fontSize=17 * s, leading=20 * s, textColor=INK),
        "oneline": ParagraphStyle("oneline", fontName=F_REG, fontSize=8.6 * s, leading=11 * s,
                                  textColor=MUTED),
        "meta": ParagraphStyle("meta", fontName=F_REG, fontSize=7 * s, leading=9 * s, textColor=MUTED,
                               alignment=2),
        "headline": ParagraphStyle("headline", fontName=F_BOLD, fontSize=11 * s, leading=14 * s,
                                   textColor=INK),
        "h3": ParagraphStyle("h3", fontName=F_BOLD, fontSize=9.2 * s, leading=11.5 * s, textColor=INK,
                             spaceBefore=6 * s, spaceAfter=1.5 * s),
        "body": ParagraphStyle("body", fontName=F_REG, fontSize=body, leading=body * 1.34, textColor=BODY,
                               alignment=TA_LEFT),
        "src": ParagraphStyle("src", fontName=F_ITAL, fontSize=6.6 * s, leading=8.4 * s, textColor=MUTED,
                              spaceBefore=1 * s),
        "cell": ParagraphStyle("cell", fontName=F_REG, fontSize=7.6 * s, leading=9.6 * s, textColor=BODY),
        "cellb": ParagraphStyle("cellb", fontName=F_SEMI, fontSize=7.6 * s, leading=9.6 * s, textColor=INK),
        "th": ParagraphStyle("th", fontName=F_BOLD, fontSize=7.2 * s, leading=9 * s, textColor=INK),
        "bullet": ParagraphStyle("bullet", fontName=F_REG, fontSize=7.8 * s, leading=10 * s, textColor=BODY,
                                 leftIndent=8 * s, bulletIndent=0, spaceAfter=1.2 * s),
        "fine": ParagraphStyle("fine", fontName=F_REG, fontSize=5.4 * s, leading=6.6 * s, textColor=MUTED),
    }


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text.strip()), style)


def _sources(refs: list[str], fmt: RefFormatter, st) -> list:
    spec = T.load()["format"]["source_line"]
    label = fmt(refs) or spec["empty_text"]
    return [Paragraph(escape(spec["prefix"] + label), st["src"])]


def _story(a: Analysis, fmt: RefFormatter, s: float, compiled_on: str, grounding_note: str,
           support_label: str | None = None) -> list:
    """Lay out the memo by walking the template's blocks in order."""
    st = _styles(s)
    width = PAGE_W - 2 * MARGIN_X - 12  # SimpleDocTemplate frames pad 6pt per side
    story: list = []

    for blk in T.load()["layout"]:
        kind = blk["block"]

        if kind == "header":
            eyebrow = next(f["text"] for f in blk["fields"] if f["key"] == "eyebrow")
            compiled = next(f["label"] for f in blk["fields"] if f["key"] == "compiled_date")
            header = Table(
                [[[_p(eyebrow, st["eyebrow"]), _p(a.company_name, st["title"]), _p(a.one_line, st["oneline"])],
                  _p(f"{compiled} {compiled_on}", st["meta"])]],
                colWidths=[width * 0.78, width * 0.22])
            header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                                        ("TOPPADDING", (0, 0), (-1, -1), 0)]))
            story += [header, Spacer(1, 6 * s)]

        elif kind == "headline":
            bar = Table([[""]], colWidths=[40], rowHeights=[3 * s])
            bar.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CORAL),
                                     ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
            bar.hAlign = "LEFT"
            story += [bar, Spacer(1, 3 * s), _p(a.headline, st["headline"]), Spacer(1, 5 * s)]

        elif kind == "evidence_callout":
            accent = RULE if support_label else SUPPORT_COLOR.get(a.thesis_support, AMBER)
            callout = Table([["", Paragraph(
                f"<font name='{F_BOLD}' color='#000000'>{escape(blk['label'])}: "
                f"{escape(support_label or a.thesis_support.capitalize())}.</font> " + escape(a.thesis_support_rationale.strip()),
                st["cell"])]], colWidths=[3, width - 3])
            callout.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), accent), ("BACKGROUND", (1, 0), (1, 0), CREAM),
                ("LEFTPADDING", (0, 0), (0, 0), 0), ("RIGHTPADDING", (0, 0), (0, 0), 0),
                ("LEFTPADDING", (1, 0), (1, 0), 7 * s), ("TOPPADDING", (0, 0), (-1, -1), 4 * s),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4 * s), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
            story.append(callout)

        elif kind == "section":
            sec = getattr(a, blk["key"])
            story += [_p(blk["title"], st["h3"]), _p(sec.body, st["body"]), *_sources(sec.refs, fmt, st)]

        elif kind == "table" and a.alternatives:
            cols = {c["key"]: c for c in blk["columns"]}
            story.append(_p(blk["title"], st["h3"]))
            rows = [[_p(cols[k]["header"], st["th"]) for k in ("name", "how_used", "where_it_breaks", "refs")]]
            for alt in a.alternatives:
                rows.append([
                    [_p(alt.name, st["cellb"]),
                     Paragraph(f"<font color='#7A7A7A'>{escape(alt.kind)}</font>", st["cell"])],
                    _p(alt.how_used, st["cell"]), _p(alt.where_it_breaks, st["cell"]),
                    _p(fmt(alt.refs) or cols["refs"]["empty_text"], st["cell"])])
            table = Table(rows, colWidths=[width * 0.22, width * 0.31, width * 0.31, width * 0.16], repeatRows=1)
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), GRAY_BG), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE), ("LEFTPADDING", (0, 0), (-1, -1), 4 * s),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4 * s), ("TOPPADDING", (0, 0), (-1, -1), 2.5 * s),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5 * s)]))
            story.append(table)

        elif kind == "two_column_lists":
            def bullets(spec: dict) -> list:
                items = [i for i in getattr(a, spec["key"]) if i.strip()]
                return [_p(spec["title"], st["h3"])] + (
                    [Paragraph(escape(i.strip()), st["bullet"], bulletText="•") for i in items]
                    or [_p(spec["empty_text"], st["bullet"])])

            left, right = blk["columns"]
            col = (width - 12) / 2
            bottom = Table([[bullets(left), bullets(right)]], colWidths=[col + 6, col + 6])
            bottom.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                        ("RIGHTPADDING", (0, 0), (0, 0), 12), ("RIGHTPADDING", (1, 0), (1, 0), 0),
                                        ("TOPPADDING", (0, 0), (-1, -1), 0)]))
            story += [Spacer(1, 4 * s), bottom, Spacer(1, 5 * s)]

        elif kind == "fine_print":
            parts = [part.replace("{grounding_note}", grounding_note).replace("{disclaimer}", DISCLAIMER)
                     for part in blk["parts"]]
            story.append(_p(" ".join(x for x in parts if x.strip()), st["fine"]))

    return story


def _footer(title: str, compiled_on: str) -> Callable:
    def draw(canvas, doc):
        canvas.saveState()
        size = 7
        left = f" {title}"
        page = str(canvas.getPageNumber())
        right = f"Compiled on {compiled_on} by TEN Capital Network"
        gap = 22
        logo_w, logo_h = 0.67 * inch, 0.25 * inch
        has_logo = config.LOGO_PATH.exists()
        widths = [pdfmetrics.stringWidth(t, F_REG, size) for t in (left, page, right)]
        total = sum(widths) + gap * 2 + (8 + logo_w if has_logo else 0)
        x = (PAGE_W - total) / 2
        y = 0.32 * inch
        canvas.setFont(F_REG, size)
        canvas.setFillColor(BODY)
        for text, w in zip((left, page, right), widths):
            canvas.drawString(x, y, text)
            x += w + gap
        if has_logo:
            canvas.drawImage(str(config.LOGO_PATH), x - gap + 8, y - 0.085 * inch, logo_w, logo_h,
                             mask="auto", preserveAspectRatio=True)
        canvas.restoreState()
    return draw


def _build(story: list, title: str, compiled_on: str) -> tuple[bytes, int]:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=MARGIN_X, rightMargin=MARGIN_X,
                            topMargin=MARGIN_TOP, bottomMargin=MARGIN_BOTTOM, title=title,
                            author="TEN Capital Network")
    footer = _footer(title, compiled_on)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue(), doc.page


def render_pdf(analysis: Analysis, format_refs: RefFormatter | None = None,
               compiled_on: date | None = None, grounding_note: str = "",
               date_text: str | None = None, support_label: str | None = None) -> RenderResult:
    """Render one page. ``date_text`` and ``support_label`` override the printed
    date and rating, which the blank template uses for placeholders."""
    _register_fonts()
    fmt = format_refs or _default_formatter
    day = compiled_on or date.today()
    when = date_text or f"{day:%B} {day.day}, {day.year}"
    title = f"{analysis.company_name} {T.load()['title']}"

    for s in SCALES:
        pdf, pages = _build(_story(analysis, fmt, s, when, grounding_note, support_label), title, when)
        if pages == 1:
            return RenderResult(pdf, s, False)

    frame_w = PAGE_W - 2 * MARGIN_X
    frame_h = PAGE_H - MARGIN_TOP - MARGIN_BOTTOM - 4  # small slack for frame padding
    squeezed = KeepInFrame(frame_w, frame_h, _story(analysis, fmt, SCALES[-1], when, grounding_note, support_label),
                           mode="shrink")
    pdf, _ = _build([squeezed], title, when)
    return RenderResult(pdf, SCALES[-1], True)


def render_preview_png(pdf: bytes, zoom: float = 1.6) -> bytes:
    import pymupdf as fitz
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return doc[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes("png")
