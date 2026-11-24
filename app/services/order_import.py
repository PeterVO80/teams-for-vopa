"""Import supplier order confirmations as purchase orders (Rexel "Order Info").

Rexel's web shop exports an order as an Excel file (``Rexel_Order_Info.xls``,
the old .xls format; .xlsx is accepted too) with one row per order line::

    Rexel productreferentie | Omschrijving | Mijn referentie | ERP code |
    Eenheid | Aantal | Netto eenheidsprijs (excl. Ecotax) | ... |
    Rexel ordernummer | Mijn orderreferentie | Mijn extra orderreferentie |
    Besteldatum | Leveringsdatum

Every Rexel order number in the file becomes one purchase order:

* ``Rexel ordernummer`` -> ``PurchaseOrder.supplier_order_number``. An order
  whose number is already known for this supplier is skipped, so the same
  file can't be imported twice (also guarded by a unique constraint).
* each line is linked to the article database when an article has the
  ``Rexel productreferentie`` as its supplier reference (``Article.supplier_ref``,
  filled by the Rexel price-list import); otherwise it becomes a free-text
  line "<reference> - <description>".
* numbers use a decimal comma ("0,1065"), dates are day/month/year ("23/11/26").
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa

from ..extensions import db
from ..models import Article, Company, OrderStatus, PurchaseOrder, PurchaseOrderLine


class OrderImportError(Exception):
    """The file can't be imported (message shown to the user)."""


#: Columns of the Rexel file (lower case) -> internal names.
COLUMNS = {
    "rexel productreferentie": "ref",
    "omschrijving": "description",
    "erp code": "erp_code",
    "eenheid": "unit",
    "aantal": "quantity",
    "netto eenheidsprijs (excl. ecotax)": "unit_price",
    "rexel ordernummer": "order_number",
    "mijn orderreferentie": "my_reference",
    "mijn extra orderreferentie": "my_extra_reference",
    "besteldatum": "order_date",
    "leveringsdatum": "delivery_date",
}
REQUIRED = ("ref", "description", "quantity", "order_number")

#: Rexel unit codes -> our units.
UNITS = {"P": "st", "ST": "st", "PCE": "st", "M": "m", "KG": "kg", "L": "l", "ROL": "rol", "DOOS": "doos"}


@dataclass
class ImportedLine:
    ref: str
    description: str
    quantity: Decimal
    unit: str
    unit_price: Decimal | None
    erp_code: str = ""


@dataclass
class ImportedOrder:
    supplier_order_number: str
    my_reference: str = ""
    my_extra_reference: str = ""
    order_date: date | None = None
    delivery_date: date | None = None
    lines: list[ImportedLine] = field(default_factory=list)


@dataclass
class ImportResult:
    created: list[PurchaseOrder] = field(default_factory=list)
    #: (supplier order number, existing purchase order)
    skipped: list[tuple[str, PurchaseOrder]] = field(default_factory=list)
    linked: int = 0
    free_text: int = 0


# ------------------------------------------------------------------ reading
def _read_rows(data: bytes) -> list[list]:
    """All rows of the first sheet of an .xls or .xlsx file."""
    if data[:4] == b"PK\x03\x04":                        # .xlsx (zip)
        from openpyxl import load_workbook
        try:
            sheet = load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets[0]
        except Exception as exc:  # noqa: BLE001 - any parse error: not a valid file
            raise OrderImportError("Het bestand kan niet gelezen worden als Excel-bestand.") from exc
        return [list(row) for row in sheet.iter_rows(values_only=True)]
    import xlrd
    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception as exc:  # noqa: BLE001
        raise OrderImportError("Het bestand kan niet gelezen worden als Excel-bestand (.xls of .xlsx).") from exc
    sheet = book.sheet_by_index(0)
    rows = []
    for r in range(sheet.nrows):
        row = []
        for c in range(sheet.ncols):
            value = sheet.cell_value(r, c)
            if sheet.cell_type(r, c) == xlrd.XL_CELL_DATE:
                value = datetime(*xlrd.xldate_as_tuple(value, book.datemode)).date()
            row.append(value)
        rows.append(row)
    return rows


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _number(value, what: str, row: int) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip().replace(" ", "").replace("€", "")
    if "," in text:                                       # Belgian notation: 1.234,56
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise OrderImportError(f"Rij {row}: '{value}' is geen geldig getal voor {what}.") from exc


def _date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    match = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", str(value).strip())
    if not match:
        return None
    day, month, year = (int(x) for x in match.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_rexel_order_file(data: bytes) -> list[ImportedOrder]:
    """Read a Rexel order file; one ImportedOrder per Rexel order number."""
    rows = [r for r in _read_rows(data) if any(_text(v) for v in r)]
    if not rows:
        raise OrderImportError("Het bestand is leeg.")
    header = [_text(v).lower() for v in rows[0]]
    index = {COLUMNS[name]: i for i, name in enumerate(header) if name in COLUMNS}
    missing = [name for name, key in COLUMNS.items() if key in REQUIRED and key not in index]
    if missing:
        raise OrderImportError(
            "Dit lijkt geen Rexel-orderbestand: de kolom(men) "
            + ", ".join(f"'{m}'" for m in missing) + " ontbreken.")

    orders: dict[str, ImportedOrder] = {}
    for number, row in enumerate(rows[1:], start=2):
        def get(key):
            i = index.get(key)
            return row[i] if i is not None and i < len(row) else None

        order_number = _text(get("order_number"))
        ref, description = _text(get("ref")), _text(get("description"))
        if not (ref or description):
            continue
        if not order_number:
            raise OrderImportError(f"Rij {number}: het Rexel ordernummer ontbreekt.")
        quantity = _number(get("quantity"), "het aantal", number)
        if not quantity or quantity <= 0:
            raise OrderImportError(f"Rij {number} ({ref}): het aantal ontbreekt of is 0.")
        order = orders.setdefault(order_number, ImportedOrder(
            supplier_order_number=order_number,
            my_reference=_text(get("my_reference")),
            my_extra_reference=_text(get("my_extra_reference")),
            order_date=_date(get("order_date")),
            delivery_date=_date(get("delivery_date")),
        ))
        unit_code = _text(get("unit")).upper()
        order.lines.append(ImportedLine(
            ref=ref,
            description=description,
            quantity=quantity,
            unit=UNITS.get(unit_code, unit_code.lower() or "st")[:20],
            unit_price=_number(get("unit_price"), "de netto eenheidsprijs", number),
            erp_code=_text(get("erp_code")),
        ))
    if not orders:
        raise OrderImportError("Het bestand bevat geen bestellijnen.")
    return list(orders.values())


# ------------------------------------------------------------------ importing
def find_articles(refs: list[str], supplier: Company) -> dict[str, Article]:
    """Articles by supplier reference (case-insensitive); this supplier's first."""
    wanted = {r.upper() for r in refs if r}
    if not wanted:
        return {}
    candidates = db.session.scalars(
        sa.select(Article).where(sa.func.upper(Article.supplier_ref).in_(wanted))
        .order_by(sa.case((Article.supplier_id == supplier.id, 0), else_=1), Article.id)
    ).all()
    found: dict[str, Article] = {}
    for article in candidates:
        found.setdefault(article.supplier_ref.upper(), article)
    return found


def import_orders(orders: list[ImportedOrder], supplier: Company, *, number_for, project_id: int | None,
                  stock_location_id: int | None, user_id: int | None) -> ImportResult:
    """Create purchase orders for ``orders``; already imported ones are skipped.

    ``number_for`` returns our own order number for a new order (e.g. the
    next "PO-2026-0007"). Nothing is committed here.
    """
    result = ImportResult()
    articles = find_articles([line.ref for o in orders for line in o.lines], supplier)
    for imported in orders:
        existing = db.session.scalar(sa.select(PurchaseOrder).where(
            PurchaseOrder.supplier_id == supplier.id,
            PurchaseOrder.supplier_order_number == imported.supplier_order_number))
        if existing:
            result.skipped.append((imported.supplier_order_number, existing))
            continue
        notes = [f"Geïmporteerd uit het orderbestand van {supplier.name}."]
        if imported.my_reference:
            notes.append(f"Mijn orderreferentie: {imported.my_reference}")
        if imported.my_extra_reference:
            notes.append(f"Mijn extra orderreferentie: {imported.my_extra_reference}")
        order = PurchaseOrder(
            number=number_for(), supplier_id=supplier.id,
            supplier_order_number=imported.supplier_order_number,
            project_id=project_id, stock_location_id=stock_location_id, created_by_id=user_id,
            status=OrderStatus.ORDERED, order_date=imported.order_date or date.today(),
            expected_date=imported.delivery_date, notes="\n".join(notes),
        )
        db.session.add(order)
        for line in imported.lines:
            article = articles.get(line.ref.upper())
            if article:
                result.linked += 1
                description = article.description
            else:
                result.free_text += 1
                description = f"{line.ref} - {line.description}" if line.ref else line.description
            order.lines.append(PurchaseOrderLine(
                article_id=article.id if article else None, description=description[:300],
                quantity=line.quantity, unit=line.unit, unit_price=line.unit_price,
            ))
        db.session.flush()   # our order number must be taken before the next one is chosen
        result.created.append(order)
    return result
