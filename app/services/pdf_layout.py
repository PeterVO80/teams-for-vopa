"""Shared layout of the PDF documents (timesheet, quote), made with ReportLab.

Every document starts with the same letter header: our logo with our name and
address (and VAT number, phone, e-mail when set) below it on the left, the
customer's full address on the right.
"""

from __future__ import annotations

from decimal import Decimal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, Spacer, Table, TableStyle

HEADER_BG = colors.HexColor("#1F4E79")
LIGHT = colors.HexColor("#EEF2F7")
MARGIN = 18 * mm
#: Usable width: SimpleDocTemplate's frame keeps 6 pt padding on both sides.
WIDTH = A4[0] - 2 * MARGIN - 12


def num(value: Decimal, places: int = 2) -> str:
    """Belgian notation without trailing zeros: 7,5 / 8 / 1.234,5."""
    text = f"{value:,.{places}f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    if "," in text:
        text = text.rstrip("0").rstrip(",")
    return text


def money(value: Decimal | None) -> str:
    """€ 1.234,50"""
    if value is None:
        return ""
    return "€ " + f"{value:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]
    normal = ParagraphStyle("n", parent=base, fontName="Helvetica", fontSize=9, leading=11)
    return {
        "normal": normal,
        "small": ParagraphStyle("s", parent=normal, fontSize=8, leading=10, textColor=colors.HexColor("#555555")),
        "cell": ParagraphStyle("c", parent=normal, fontSize=8.5, leading=10.5),
        "right": ParagraphStyle("r", parent=normal, fontSize=8.5, leading=10.5, alignment=TA_RIGHT),
        "note": ParagraphStyle("no", parent=normal, fontName="Helvetica-Oblique", fontSize=8, leading=10,
                               textColor=colors.HexColor("#555555")),
        "title": ParagraphStyle("t", parent=normal, fontName="Helvetica-Bold", fontSize=15, leading=19, spaceAfter=2),
    }


def logo(path: str | None, width: float = 46 * mm):
    """The logo as a flowable with its own aspect ratio (None when there is no file)."""
    if not path:
        return None
    try:
        image = Image(path)
    except OSError:
        return None
    image.drawHeight = width * image.imageHeight / image.imageWidth
    image.drawWidth = width
    image.hAlign = "LEFT"
    return image


def text(value: str | None) -> str:
    """Escaped text with line breaks for a Paragraph."""
    return escape(value or "").replace("\n", "<br/>")


def letter_header(company: str, office: str, customer=None, logo_path: str | None = None,
                  company_lines: list[str] | None = None) -> Table:
    """Logo + our name and address (left), the customer's full address (right)."""
    st = styles()
    ours = []
    image = logo(logo_path)
    if image:
        ours += [image, Spacer(1, 3 * mm)]
    ours.append(Paragraph(f"<b>{escape(company)}</b>", st["normal"]))
    if office:
        ours.append(Paragraph(escape(office), st["small"]))
    for line in company_lines or []:
        ours.append(Paragraph(escape(line), st["small"]))
    theirs = []
    if customer is not None:
        theirs.append(Paragraph(f"<b>{escape(customer.name)}</b>", st["normal"]))
        if customer.contact_person:
            theirs.append(Paragraph(f"t.a.v. {escape(customer.contact_person)}", st["normal"]))
        theirs += [Paragraph(escape(line), st["normal"]) for line in customer.address_lines]
        if customer.vat_number:
            theirs.append(Paragraph(f"Btw {escape(customer.vat_number)}", st["small"]))
    header = Table([[ours, theirs]], colWidths=[WIDTH - 75 * mm, 75 * mm])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                ("TOPPADDING", (1, 0), (1, 0), 6 * mm)]))   # customer block a bit lower
    return header


def footer(left_text: str):
    """onPage callback: ``left_text`` and the page number at the bottom."""
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(MARGIN, 10 * mm, left_text)
        canvas.drawRightString(A4[0] - MARGIN, 10 * mm, f"Pagina {doc.page}")
        canvas.restoreState()
    return draw


def header_row_style() -> list:
    """Table style commands for a dark header row."""
    return [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
    ]
