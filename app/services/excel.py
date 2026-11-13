"""Excel (.xlsx) import and export of the article database.

The column layout is defined once in :data:`ARTICLE_COLUMNS`; both the export
and the import use it, so an exported file can be edited in Excel and
imported again without any changes ("round-trip").

Import rules
------------
* The first row must contain the column headers. Headers are matched
  case-insensitively against the Dutch header *or* the English field name,
  and the column order does not matter.
* ``Artikelnummer`` is the unique key: existing articles are **updated**,
  unknown numbers are **created**. Articles are never deleted by an import.
* Empty cells in an update leave the existing value untouched.
* Invalid rows are skipped and reported; valid rows are still imported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import IO, Iterable

import sqlalchemy as sa
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from ..extensions import db
from ..models import Article

#: (model attribute, column header in Excel, column width)
ARTICLE_COLUMNS: list[tuple[str, str, int]] = [
    ("article_number", "Artikelnummer", 18),
    ("description", "Omschrijving", 45),
    ("manufacturer", "Fabrikant", 18),
    ("manufacturer_ref", "Bestelcode fabrikant", 24),
    ("supplier", "Leverancier", 18),
    ("supplier_ref", "Referentie leverancier", 22),
    ("category", "Categorie", 18),
    ("unit", "Eenheid", 9),
    ("unit_price", "Eenheidsprijs", 14),
    ("ean", "EAN", 15),
    ("url", "Link", 30),
    ("notes", "Opmerkingen", 40),
    ("active", "Actief", 8),
]

_HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
_HEADER_FONT = Font(bold=True, color="FFFFFF")


def _write_sheet(rows: Iterable[Article]) -> BytesIO:
    """Create a workbook with one header row followed by ``rows``."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Artikelen"
    ws.append([header for _, header, _ in ARTICLE_COLUMNS])
    for col_idx, (_, _, width) in enumerate(ARTICLE_COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill, cell.font = _HEADER_FILL, _HEADER_FONT
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.freeze_panes = "A2"  # keep the header visible while scrolling

    for article in rows:
        values = []
        for attr, _, _ in ARTICLE_COLUMNS:
            value = getattr(article, attr)
            if isinstance(value, Decimal):
                value = float(value)
            elif isinstance(value, bool):
                value = "ja" if value else "nee"
            values.append(value)
        ws.append(values)

    ws.auto_filter.ref = ws.dimensions
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def export_articles(articles: Iterable[Article]) -> BytesIO:
    """Return an in-memory .xlsx file containing ``articles``."""
    return _write_sheet(articles)


def import_template() -> BytesIO:
    """Return an empty workbook with only the header row (import template)."""
    return _write_sheet([])


# --------------------------------------------------------------------- import
@dataclass
class ImportResult:
    """Summary of an import run, shown to the user afterwards."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "nee", "no", "false", "n", "inactief"}


def _parse_decimal(value) -> Decimal:
    """Accept both ``12.5`` and the Belgian notation ``12,50`` / ``€ 1.234,50``."""
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value)).quantize(Decimal("0.0001"))
    text = str(value).replace("€", "").replace(" ", "")
    if "," in text:  # comma used as decimal separator -> dots are thousands
        text = text.replace(".", "").replace(",", ".")
    return Decimal(text).quantize(Decimal("0.0001"))


def _map_headers(header_row: tuple) -> dict[int, str]:
    """Map column index -> model attribute based on the header texts."""
    lookup = {}
    for attr, header, _ in ARTICLE_COLUMNS:
        lookup[header.lower()] = attr
        lookup[attr.lower()] = attr
    mapping = {}
    for idx, cell in enumerate(header_row):
        if cell is not None and str(cell).strip().lower() in lookup:
            mapping[idx] = lookup[str(cell).strip().lower()]
    return mapping


def import_articles(file: IO[bytes]) -> ImportResult:
    """Create or update articles from an uploaded .xlsx file.

    The caller is responsible for committing the session afterwards, so the
    whole import can be rolled back as one unit if something unexpected
    happens.
    """
    result = ImportResult()
    try:
        wb = load_workbook(file, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises many different exception types
        result.errors.append(f"Bestand kon niet gelezen worden: {exc}")
        return result

    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)
    mapping = _map_headers(header or ())
    if "article_number" not in mapping.values():
        result.errors.append("Kolom 'Artikelnummer' niet gevonden in de eerste rij.")
        return result

    # Cache existing articles once instead of querying per row.
    existing = {a.article_number: a for a in db.session.scalars(sa.select(Article))}

    for row_number, row in enumerate(rows, start=2):
        data = {
            attr: row[idx]
            for idx, attr in mapping.items()
            if idx < len(row) and row[idx] not in (None, "")
        }
        if not data:
            continue  # completely empty row
        number = str(data.get("article_number", "")).strip()
        if not number:
            result.skipped += 1
            result.errors.append(f"Rij {row_number}: artikelnummer ontbreekt.")
            continue

        try:
            if "unit_price" in data:
                data["unit_price"] = _parse_decimal(data["unit_price"])
            if "active" in data:
                data["active"] = _parse_bool(data["active"])
        except (InvalidOperation, ValueError):
            result.skipped += 1
            result.errors.append(f"Rij {row_number}: ongeldige prijs '{data['unit_price']}'.")
            continue

        article = existing.get(number)
        if article is None:
            if not data.get("description"):
                result.skipped += 1
                result.errors.append(f"Rij {row_number}: omschrijving ontbreekt voor nieuw artikel {number}.")
                continue
            article = Article(article_number=number)
            db.session.add(article)
            existing[number] = article
            result.created += 1
        else:
            result.updated += 1

        for attr, value in data.items():
            if attr == "article_number":
                continue
            if isinstance(value, str):
                value = value.strip()
            elif attr not in ("unit_price", "active"):
                value = str(value)  # e.g. numeric order codes read as int
            setattr(article, attr, value)

    wb.close()
    return result
