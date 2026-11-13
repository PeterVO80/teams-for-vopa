"""Material follow-up per project: what is ordered and what is delivered.

All order lines of a project's purchase orders are grouped per material:

* lines linked to the article database are grouped per **article** (and
  unit), so the same article ordered in three orders appears once;
* free-text lines (no article) are grouped per description + unit.

For every material we sum what is still in draft orders, what is actually
ordered, what has been received and what is still outstanding. Cancelled
orders are ignored. This is the basis for the "Materiaal" tab on the project
page, the material page under Bestellingen and the Excel export.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from io import BytesIO

import sqlalchemy as sa
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from ..extensions import db
from ..models import Article, OrderStatus, PurchaseOrder, PurchaseOrderLine

ZERO = Decimal("0")


@dataclass
class MaterialRow:
    """One material (article or free-text item) within one project."""

    description: str
    unit: str
    article: Article | None = None
    #: Quantity on draft orders: planned, but not ordered yet.
    qty_draft: Decimal = ZERO
    #: Quantity on placed orders (ordered, partially or fully delivered).
    qty_ordered: Decimal = ZERO
    qty_received: Decimal = ZERO
    #: Value of the ordered + draft quantity (unit price x quantity).
    amount: Decimal = ZERO
    #: Purchase orders containing this material, in order of appearance.
    orders: list[PurchaseOrder] = field(default_factory=list)
    #: Earliest expected delivery date of the orders that are still open.
    expected_date: date | None = None
    is_overdue: bool = False

    @property
    def qty_outstanding(self) -> Decimal:
        """Ordered but not yet received."""
        return max(self.qty_ordered - self.qty_received, ZERO)

    @property
    def status(self) -> str:
        """``draft`` / ``ordered`` / ``partial`` / ``delivered`` (for badges and sorting)."""
        if not self.qty_ordered:
            return "draft"
        if self.qty_received >= self.qty_ordered:
            return "delivered"
        return "partial" if self.qty_received else "ordered"

    @property
    def status_label(self) -> str:
        return {
            "draft": "Nog niet besteld",
            "ordered": "Besteld",
            "partial": "Deels geleverd",
            "delivered": "Geleverd",
        }[self.status]


@dataclass
class MaterialOverview:
    rows: list[MaterialRow]

    @property
    def total_amount(self) -> Decimal:
        return sum((r.amount for r in self.rows), ZERO)

    @property
    def open_rows(self) -> int:
        """Materials that still have to be ordered or delivered."""
        return sum(1 for r in self.rows if r.status != "delivered")

    @property
    def progress(self) -> int:
        """Percentage of the ordered quantity that has been received."""
        ordered = sum((r.qty_ordered for r in self.rows), ZERO)
        if not ordered:
            return 0
        received = sum((min(r.qty_received, r.qty_ordered) for r in self.rows), ZERO)
        return int(received / ordered * 100)


#: Display order: what needs attention first.
_STATUS_ORDER = {"draft": 0, "ordered": 1, "partial": 2, "delivered": 3}


def project_material(project_id: int, only_open: bool = False) -> MaterialOverview:
    """Build the material overview of one project.

    :param only_open: leave out materials that are completely delivered.
    """
    lines = db.session.scalars(
        sa.select(PurchaseOrderLine)
        .join(PurchaseOrder)
        .where(PurchaseOrder.project_id == project_id, PurchaseOrder.status != OrderStatus.CANCELLED)
        .order_by(PurchaseOrder.id, PurchaseOrderLine.id)
    ).all()

    rows: dict[tuple, MaterialRow] = {}
    for line in lines:
        order = line.order
        key = (
            ("article", line.article_id, line.unit)
            if line.article_id
            else ("text", line.description.strip().lower(), line.unit)
        )
        row = rows.get(key)
        if row is None:
            row = rows[key] = MaterialRow(
                description=line.article.description if line.article else line.description,
                unit=line.unit,
                article=line.article,
            )

        if order.status == OrderStatus.DRAFT:
            row.qty_draft += line.quantity
        else:
            row.qty_ordered += line.quantity
            row.qty_received += line.quantity_received
            if not line.is_complete and order.expected_date:
                if row.expected_date is None or order.expected_date < row.expected_date:
                    row.expected_date = order.expected_date
                row.is_overdue = row.is_overdue or order.is_overdue
        row.amount += line.line_total
        if order not in row.orders:
            row.orders.append(order)

    result = sorted(
        rows.values(),
        key=lambda r: (
            _STATUS_ORDER[r.status],
            r.article.article_number if r.article else "~",  # free text after articles
            r.description.lower(),
        ),
    )
    if only_open:
        result = [r for r in result if r.status != "delivered"]
    return MaterialOverview(rows=result)


def export_material(project, overview: MaterialOverview) -> BytesIO:
    """Excel version of the material overview of ``project``."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Materiaal"
    ws.append([f"Materiaaloverzicht {project.display_name}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([f"Afgedrukt op {date.today():%d/%m/%Y}"])
    ws.append([])
    headers = ["Artikelnummer", "Bestelcode fabrikant", "Omschrijving", "Eenheid", "In concept",
               "Besteld", "Geleverd", "Openstaand", "Status", "Verwacht", "Bestellingen", "Bedrag"]
    ws.append(headers)
    for cell in ws[4]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E79")
    for row in overview.rows:
        ws.append([
            row.article.article_number if row.article else "",
            (row.article.manufacturer_ref or "") if row.article else "",
            row.description,
            row.unit,
            float(row.qty_draft),
            float(row.qty_ordered),
            float(row.qty_received),
            float(row.qty_outstanding),
            row.status_label,
            row.expected_date,
            ", ".join(o.number for o in row.orders),
            float(row.amount),
        ])
        ws.cell(row=ws.max_row, column=10).number_format = "DD/MM/YYYY"
    for col, width in zip("ABCDEFGHIJKL", (16, 22, 45, 9, 11, 9, 10, 11, 16, 12, 28, 12)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A5"
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
