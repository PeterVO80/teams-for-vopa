"""PDF timesheet (urenstaat) of a project, made with ReportLab.

Layout (A4 portrait): logo with our name and address below it, the customer's
full address on the right, the title with the
project and period, a table with the hours per day per employee (date,
employee, hours, description, field service km), the totals per employee
and the grand total. Page numbers at the bottom.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .timesheet import Timesheet

HEADER_BG = colors.HexColor("#1F4E79")
LIGHT = colors.HexColor("#EEF2F7")
DAYS = ["ma", "di", "wo", "do", "vr", "za", "zo"]


def _num(value: Decimal, places: int = 2) -> str:
    """Belgian notation without trailing zeros: 7,5 / 8 / 123,4."""
    text = f"{value:,.{places}f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    if "," in text:
        text = text.rstrip("0").rstrip(",")
    return text


def _logo(path: str | None, width: float = 46 * mm):
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


def build_pdf(project, start: date, end: date, sheet: Timesheet, company: str, office: str,
              logo: str | None = None) -> bytes:
    buffer = BytesIO()
    styles = getSampleStyleSheet()
    normal = ParagraphStyle("n", parent=styles["Normal"], fontName="Helvetica", fontSize=9, leading=11)
    small = ParagraphStyle("s", parent=normal, fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
    cell = ParagraphStyle("c", parent=normal, fontSize=8.5, leading=10.5)
    right = ParagraphStyle("r", parent=cell, alignment=TA_RIGHT)
    title = ParagraphStyle("t", parent=normal, fontName="Helvetica-Bold", fontSize=15, leading=19, spaceAfter=2)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(18 * mm, 10 * mm, f"Urenstaat {project.code} · {start:%d/%m/%Y} - {end:%d/%m/%Y}")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Pagina {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"Urenstaat {project.code}", author=company)
    # Header: logo with our name and address below it (left); the customer's
    # full address on the right, like a letter.
    ours = []
    image = _logo(logo)
    if image:
        ours += [image, Spacer(1, 3 * mm)]
    ours.append(Paragraph(f"<b>{escape(company)}</b>", normal))
    if office:
        ours.append(Paragraph(escape(office), small))
    customer = project.customer
    theirs = []
    if customer:
        theirs.append(Paragraph(f"<b>{escape(customer.name)}</b>", normal))
        theirs += [Paragraph(escape(line), normal) for line in customer.address_lines]
        if customer.vat_number:
            theirs.append(Paragraph(f"Btw {escape(customer.vat_number)}", small))
    width = A4[0] - 36 * mm
    header = Table([[ours, theirs]], colWidths=[width - 75 * mm, 75 * mm])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                ("TOPPADDING", (1, 0), (1, 0), 6 * mm)]))   # customer block a bit lower
    story = [
        header,
        Spacer(1, 10 * mm),
        Paragraph("Urenstaat", title),
        Paragraph(f"<b>Project:</b> {escape(project.code)} - {escape(project.name)}", normal),
    ]
    if customer:
        story.append(Paragraph(f"<b>Klant:</b> {escape(customer.name)}", normal))
    story += [Paragraph(f"<b>Periode:</b> {start:%d/%m/%Y} - {end:%d/%m/%Y}", normal), Spacer(1, 6 * mm)]

    data = [["Datum", "Medewerker", "Uren", "Omschrijving", "Km"]]
    for row in sheet.rows:
        description = escape(row.description).replace("\n", "<br/>")
        if row.field_service:
            description = ("<i>Buitendienst</i>" + ("<br/>" if description else "")) + description
        data.append([
            Paragraph(f"{DAYS[row.work_date.weekday()]} {row.work_date:%d/%m/%Y}", cell),
            Paragraph(escape(row.user.full_name), cell),
            Paragraph(_num(row.hours), right),
            Paragraph(description, cell),
            Paragraph(_num(row.km, 1) if row.km else "", right),
        ])
    if len(data) == 1:
        data.append(["", "", "", Paragraph("<i>Geen uren geregistreerd in deze periode.</i>", cell), ""])
    data.append(["", Paragraph("<b>Totaal</b>", cell), Paragraph(f"<b>{_num(sheet.total_hours)}</b>", right), "",
                 Paragraph(f"<b>{_num(sheet.total_km, 1)}</b>" if sheet.total_km else "", right)])
    widths = [27 * mm, 34 * mm, 14 * mm, None, 15 * mm]
    widths[3] = A4[0] - 36 * mm - sum(w for w in widths if w)
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("ALIGN", (2, 0), (2, 0), "RIGHT"),
        ("ALIGN", (4, 0), (4, 0), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, LIGHT]),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(table)

    if sheet.per_user:
        story += [Spacer(1, 7 * mm), Paragraph("<b>Totaal per medewerker</b>", normal), Spacer(1, 2 * mm)]
        totals = [["Medewerker", "Uren", "Km"]] + [
            [Paragraph(escape(user.full_name), cell), Paragraph(_num(hours), right),
             Paragraph(_num(km, 1) if km else "", right)] for user, hours, km in sheet.per_user]
        per_user = Table(totals, colWidths=[60 * mm, 20 * mm, 20 * mm], hAlign="LEFT")
        per_user.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8.5),
            ("ALIGN", (1, 0), (-1, 0), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ]))
        story.append(per_user)

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
