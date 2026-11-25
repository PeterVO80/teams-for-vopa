"""PDF of a quote (offerte) for the customer, made with ReportLab.

Layout (A4 portrait): the letter header (pdf_layout.py), title "Offerte" with
number, date, validity and subject, the introduction, the lines (section
titles with their subtotal on the right, material and labour with their notes -
per section: all details, only quantities or only the subtotal), the
totals (material, labour, excl. VAT, VAT, incl. VAT) and a "voor akkoord"
block for the customer's signature.

Purchase prices and margins are internal: they never appear on the PDF.
"""

from __future__ import annotations

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..models import PdfDetail, Quote
from .pdf_layout import LIGHT, MARGIN, WIDTH, footer, header_row_style, letter_header, money, num, styles, text

PRICE_PLACES = 4


def _price(value) -> str:
    """Unit price: 2 decimals, up to 4 when needed (cable per metre: € 0,3422)."""
    if value is None:
        return ""
    amount = money(value)
    exact = f"{value:.{PRICE_PLACES}f}".rstrip("0")
    decimals = len(exact.split(".")[1]) if "." in exact else 0
    if decimals > 2:
        amount = "€ " + num(value, decimals)
    return amount


def build_quote_pdf(quote: Quote, company: str, office: str, logo: str | None = None,
                    company_lines: list[str] | None = None) -> bytes:
    buffer = BytesIO()
    st = styles()
    normal, small, cell, right, note, title = (st[k] for k in ("normal", "small", "cell", "right", "note", "title"))
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"Offerte {quote.number}", author=company)

    story = [letter_header(company, office, quote.customer, logo, company_lines), Spacer(1, 10 * mm)]

    # Title and reference data.
    facts = [("Offertenummer", quote.number)]
    if quote.quote_date:
        facts.append(("Datum", f"{quote.quote_date:%d/%m/%Y}"))
    if quote.valid_until:
        facts.append(("Geldig tot", f"{quote.valid_until:%d/%m/%Y}"))
    if quote.project:
        facts.append(("Project", quote.project.code))
    fact_table = Table([[Paragraph(f"<b>{label}</b>", small), Paragraph(escape(value), normal)] for label, value in facts],
                       colWidths=[28 * mm, 45 * mm], hAlign="RIGHT")
    fact_table.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1),
                                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    top = Table([[[Paragraph("Offerte", title), Paragraph(f"<b>Betreft:</b> {escape(quote.title)}", normal)], fact_table]],
                colWidths=[WIDTH - 75 * mm, 75 * mm])
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [top, Spacer(1, 5 * mm)]
    if quote.introduction:
        story += [Paragraph(text(quote.introduction), normal), Spacer(1, 5 * mm)]

    # Lines: one table; section titles span the row, notes go under the description.
    data = [["Omschrijving", "Aantal", "Eenh.", "Eenheidsprijs", "Totaal"]]
    commands = header_row_style() + [
        ("ALIGN", (1, 0), (1, 0), "RIGHT"), ("ALIGN", (3, 0), (4, 0), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for section in quote.sections():
        if section.title is not None:
            row = len(data)
            content = [Paragraph(f"<b>{escape(section.title.description)}</b>", st["normal"])]
            if section.title.note:
                content.append(Paragraph(text(section.title.note), note))
            # The section's subtotal on the right of its title.
            subtotal = Paragraph(f"<b>{money(section.total)}</b>", right) if section.lines else ""
            data.append([content, "", "", "", subtotal])
            commands += [("SPAN", (0, row), (3, row)), ("BACKGROUND", (0, row), (-1, row), LIGHT),
                         ("TOPPADDING", (0, row), (-1, row), 5)]
        detail = section.title.pdf_detail if section.title is not None else PdfDetail.FULL
        if detail == PdfDetail.TOTAL:
            continue                        # only the subtotal on the section title
        show_prices = detail != PdfDetail.QUANTITIES
        for line in section.lines:
            content = [Paragraph(escape(line.description), cell)]
            if line.note:
                content.append(Paragraph(text(line.note), note))
            data.append([
                content,
                Paragraph(num(line.quantity) if line.quantity is not None else "", right),
                Paragraph(escape(line.unit or ""), cell),
                Paragraph(_price(line.unit_price), right) if show_prices else "",
                Paragraph(money(line.total), right) if show_prices else "",
            ])
            commands.append(("LINEBELOW", (0, len(data) - 1), (-1, len(data) - 1), 0.25, colors.HexColor("#DDDDDD")))
    if len(data) == 1:
        data.append([Paragraph("<i>Deze offerte bevat nog geen lijnen.</i>", cell), "", "", "", ""])
    widths = [None, 18 * mm, 13 * mm, 26 * mm, 26 * mm]
    widths[0] = WIDTH - sum(w for w in widths if w)
    lines = Table(data, colWidths=widths, repeatRows=1)
    lines.setStyle(TableStyle(commands))
    story += [lines, Spacer(1, 5 * mm)]

    # Totals (right aligned).
    totals = []
    if quote.material_total and quote.labour_total:
        totals += [("Materiaal", money(quote.material_total)),
                   (f"Werkuren ({num(quote.labour_hours)} u)", money(quote.labour_total))]
    totals += [("<b>Totaal excl. btw</b>", f"<b>{money(quote.total)}</b>"),
               (f"Btw {num(quote.vat_rate)}%", money(quote.vat_amount)),
               ("<b>Totaal incl. btw</b>", f"<b>{money(quote.total_incl_vat)}</b>")]
    total_table = Table([[Paragraph(label, normal), Paragraph(value, right)] for label, value in totals],
                        colWidths=[45 * mm, 30 * mm], hAlign="RIGHT")
    total_table.setStyle(TableStyle([
        ("LINEABOVE", (0, -3), (-1, -3), 0.6, colors.black),
        ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.black),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT),
    ]))
    story.append(KeepTogether([total_table]))

    # Signature block.
    sign = Table([[Paragraph("<b>Voor akkoord</b> (naam, datum en handtekening)", normal)], [""]],
                 colWidths=[85 * mm], rowHeights=[None, 22 * mm], hAlign="LEFT")
    sign.setStyle(TableStyle([("BOX", (0, 1), (0, 1), 0.5, colors.HexColor("#999999")),
                              ("LEFTPADDING", (0, 0), (0, 0), 0)]))
    story += [Spacer(1, 10 * mm), KeepTogether([sign])]
    if quote.valid_until:
        story += [Spacer(1, 3 * mm),
                  Paragraph(f"Deze offerte is geldig tot {quote.valid_until:%d/%m/%Y}. Alle prijzen in euro.", small)]

    page_footer = footer(f"Offerte {quote.number} · {company}")
    doc.build(story, onFirstPage=page_footer, onLaterPages=page_footer)
    return buffer.getvalue()
