"""PDF timesheet (urenstaat) of a project, made with ReportLab.

Layout (A4 portrait): logo with our name and address below it, the customer's
full address on the right, the title with the
project and period, a table with the hours per day per employee (date,
employee, hours, description, field service km), the totals per employee
and the grand total. Page numbers at the bottom.
"""

from __future__ import annotations

from datetime import date
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .pdf_layout import LIGHT, MARGIN, WIDTH, footer, header_row_style, letter_header, styles
from .pdf_layout import num as _num
from .timesheet import Timesheet

DAYS = ["ma", "di", "wo", "do", "vr", "za", "zo"]


def build_pdf(project, start: date, end: date, sheet: Timesheet, company: str, office: str,
              logo: str | None = None, company_lines: list[str] | None = None) -> bytes:
    buffer = BytesIO()
    st = styles()
    normal, cell, right, title = st["normal"], st["cell"], st["right"], st["title"]
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"Urenstaat {project.code}", author=company)
    customer = project.customer
    header = letter_header(company, office, customer, logo, company_lines)
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
    widths[3] = WIDTH - sum(w for w in widths if w)
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle(header_row_style() + [
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
        per_user.setStyle(TableStyle(header_row_style() + [
            ("ALIGN", (1, 0), (-1, 0), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ]))
        story.append(per_user)

    page_footer = footer(f"Urenstaat {project.code} · {start:%d/%m/%Y} - {end:%d/%m/%Y}")
    doc.build(story, onFirstPage=page_footer, onLaterPages=page_footer)
    return buffer.getvalue()
