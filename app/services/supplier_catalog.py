"""Supplier price lists: load a catalogue file and import selected articles.

Workflow
--------
1. :func:`load_catalog` reads the uploaded file (currently the Rexel DBF
   format) into the ``catalog_items`` table. Rows are streamed and inserted in
   chunks, so very large price lists (hundreds of thousands of articles) use
   little memory.
2. The user filters and selects items in the catalogue screen
   (``blueprints/catalog.py``) - :func:`filtered_items` builds that query.
3. :func:`import_items` creates or updates articles for the selected items.
   :func:`update_prices` refreshes all articles that were imported before
   (typically after uploading the supplier's new monthly price list).

Matching catalogue items with articles
---------------------------------------
An article belongs to a catalogue item when ``Article.supplier_id`` equals the
catalogue's supplier and ``Article.supplier_ref`` equals the item's
supplier reference. Those two fields are filled on import, so later price
lists find the same articles again, even if the article number was changed.

Adding another supplier
-----------------------
Describe its file in a :class:`CatalogFormat` (which field holds what) and
add it to :data:`FORMATS`. The rest of the module is format independent.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable, Iterator

import sqlalchemy as sa
from dbfread import DBF

from ..extensions import db
from ..models import Article, CatalogItem, Company, SupplierCatalog

#: Rows inserted per database round trip while loading a catalogue.
CHUNK_SIZE = 2000


# ---------------------------------------------------------------- formats
def _rexel_unit(value: str) -> str:
    """Rexel STOCK_UNIT -> unit used in the app (P = piece, M = metre)."""
    value = (value or "").strip().upper()
    return {"P": "st", "M": "m", "SET": "set", "": "st"}.get(value, value.lower())


def _rexel_date(value: str) -> date | None:
    """Rexel dates look like ``2026/11/07``."""
    try:
        return datetime.strptime(value.strip(), "%Y/%m/%d").date()
    except (ValueError, AttributeError):
        return None


def _url(value: str) -> str | None:
    """Rexel writes ``HTTPS://Netstore...``: normalise the scheme to lower case."""
    value = (value or "").strip()
    if value.upper().startswith(("HTTPS://", "HTTP://")):
        scheme, rest = value.split("://", 1)
        value = f"{scheme.lower()}://{rest}"
    return value or None


@dataclass(frozen=True)
class CatalogFormat:
    """How to read the price list file of one supplier."""

    name: str
    #: Fields that must be present to recognise this format.
    required_fields: frozenset[str]
    #: Convert one raw file record (dict) into CatalogItem column values.
    convert: Callable[[dict], dict]


def _clean(value) -> str:
    """Strip padding and collapse double spaces (``"SIE  255"`` -> ``"SIE 255"``)."""
    return " ".join(str(value or "").split())


def _rexel_record(rec: dict) -> dict:
    description = _clean(rec.get("DESC_NL")) or _clean(rec.get("DESC_FR"))
    return {
        "supplier_ref": _clean(rec.get("REXEL_REF")),
        "supplier_code": _clean(rec.get("ERP_NUMB")) or None,
        "manufacturer": _clean(rec.get("FABRICANT")) or None,
        "manufacturer_ref": _clean(rec.get("SUPPL_REF")) or None,
        "product_group": _clean(rec.get("PROD_GROUP")) or None,
        "product_group_desc": _clean(rec.get("PG_DESC_NL")) or None,
        "description": description or _clean(rec.get("REXEL_REF")),
        "description_fr": _clean(rec.get("DESC_FR")) or None,
        "unit": _rexel_unit(rec.get("STOCK_UNIT")),
        "net_price": _decimal(rec.get("NET_PRICE")),
        "gross_price": _decimal(rec.get("BRUT_PRICE")),
        "ean": _clean(rec.get("EAN_CODE")) or None,
        "url": _url(rec.get("URL")),
        "min_order_qty": _decimal(rec.get("MIN_ORDQTY")),
        "price_date": _rexel_date(rec.get("DATE")),
    }


REXEL = CatalogFormat(
    name="Rexel",
    required_fields=frozenset({"REXEL_REF", "SUPPL_REF", "DESC_NL", "NET_PRICE", "FABRICANT"}),
    convert=_rexel_record,
)

#: All supported formats; the first whose required fields are present wins.
FORMATS = [REXEL]


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.0001"))
    except InvalidOperation:
        return None


# ------------------------------------------------------------------ reading
class CatalogFileError(ValueError):
    """The uploaded file cannot be read as a supported price list."""


def read_dbf(path: str) -> tuple[CatalogFormat, int, Iterator[dict]]:
    """Open a DBF price list. Returns (format, number of records, converted rows).

    The DBF header of Rexel files declares no code page, so ``cp1252`` (the
    Windows code page for Dutch/French) is used; undecodable bytes are
    replaced instead of aborting the whole import.
    """
    try:
        table = DBF(path, load=False, encoding="cp1252", char_decode_errors="replace")
        fields = set(table.field_names)
    except Exception as exc:  # dbfread raises various errors for non-DBF files
        raise CatalogFileError(f"Geen geldig DBF-bestand ({exc}).") from exc

    fmt = next((f for f in FORMATS if f.required_fields <= fields), None)
    if fmt is None:
        raise CatalogFileError(
            "Onbekend prijslijstformaat. Gevonden velden: " + ", ".join(sorted(fields))
        )

    def rows() -> Iterator[dict]:
        for record in table:
            if getattr(record, "deleted", False):
                continue
            row = fmt.convert(record)
            if row["supplier_ref"]:
                yield row

    return fmt, len(table), rows()


def load_catalog(upload, supplier: Company, user_id: int | None, replace: bool = True) -> SupplierCatalog:
    """Store an uploaded price list as a new catalogue.

    :param upload: werkzeug ``FileStorage`` from the upload form.
    :param replace: delete older catalogues of the same supplier (they are
        superseded by the new price list; imported articles are not touched).
    """
    # dbfread needs a real file: write the upload to a temporary file first.
    fd, path = tempfile.mkstemp(suffix=".dbf")
    try:
        with os.fdopen(fd, "wb") as tmp:
            upload.save(tmp)
        fmt, _, rows = read_dbf(path)

        catalog = SupplierCatalog(
            supplier=supplier, filename=upload.filename or "prijslijst.dbf",
            uploaded_by_id=user_id,
        )
        db.session.add(catalog)
        db.session.flush()  # we need catalog.id for the items

        count, newest, chunk = 0, None, []
        for row in rows:
            row["catalog_id"] = catalog.id
            chunk.append(row)
            if row["price_date"] and (newest is None or row["price_date"] > newest):
                newest = row["price_date"]
            if len(chunk) >= CHUNK_SIZE:
                db.session.execute(sa.insert(CatalogItem), chunk)
                count += len(chunk)
                chunk = []
        if chunk:
            db.session.execute(sa.insert(CatalogItem), chunk)
            count += len(chunk)

        if not count:
            raise CatalogFileError("Het bestand bevat geen artikelen.")
        catalog.item_count, catalog.price_date = count, newest

        if replace:
            for old in db.session.scalars(
                sa.select(SupplierCatalog).where(
                    SupplierCatalog.supplier_id == catalog.supplier_id, SupplierCatalog.id != catalog.id
                )
            ):
                delete_catalog(old)
        return catalog
    finally:
        os.unlink(path)


def delete_catalog(catalog: SupplierCatalog) -> None:
    """Remove a catalogue and its items (imported articles stay)."""
    db.session.execute(sa.delete(CatalogItem).where(CatalogItem.catalog_id == catalog.id))
    db.session.delete(catalog)


# --------------------------------------------------------------- filtering
#: Values for the "status" filter.
STATUS_ALL, STATUS_NEW, STATUS_IMPORTED = "all", "new", "imported"


def _linked_article_exists(catalog: SupplierCatalog):
    """SQL condition: an article exists for this catalogue item."""
    return (
        sa.select(Article.id)
        .where(Article.supplier_id == catalog.supplier_id, Article.supplier_ref == CatalogItem.supplier_ref)
        .exists()
    )


def filtered_items(
    catalog: SupplierCatalog,
    search: str = "",
    manufacturer: str = "",
    product_group: str = "",
    status: str = STATUS_ALL,
) -> sa.Select:
    """Query for the catalogue items matching the filters of the screen."""
    query = sa.select(CatalogItem).where(CatalogItem.catalog_id == catalog.id)
    if manufacturer:
        query = query.where(CatalogItem.manufacturer == manufacturer)
    if product_group:
        query = query.where(CatalogItem.product_group_desc == product_group)
    # Every word must match somewhere: "et200sp digital" finds "SIMATIC ET 200SP, DIGITAL INPU"
    # (descriptions are also compared without spaces, so "et200sp" matches "ET 200SP").
    for word in search.split():
        pattern = f"%{word}%"
        query = query.where(
            sa.or_(
                CatalogItem.description.ilike(pattern),
                sa.func.replace(CatalogItem.description, " ", "").ilike(pattern),
                CatalogItem.description_fr.ilike(pattern),
                CatalogItem.manufacturer_ref.ilike(pattern),
                CatalogItem.supplier_ref.ilike(pattern),
                CatalogItem.supplier_code.ilike(pattern),
                CatalogItem.ean == word,
            )
        )
    if status == STATUS_NEW:
        query = query.where(~_linked_article_exists(catalog))
    elif status == STATUS_IMPORTED:
        query = query.where(_linked_article_exists(catalog))
    return query.order_by(CatalogItem.manufacturer, CatalogItem.manufacturer_ref, CatalogItem.id)


def linked_articles(catalog: SupplierCatalog, items: list[CatalogItem]) -> dict[str, Article]:
    """Articles already in the database for ``items``, keyed by supplier reference."""
    refs = [i.supplier_ref for i in items]
    if not refs:
        return {}
    articles = db.session.scalars(
        sa.select(Article).where(Article.supplier_id == catalog.supplier_id, Article.supplier_ref.in_(refs))
    )
    return {a.supplier_ref: a for a in articles}


# ------------------------------------------------------------------ import
#: Which catalogue field becomes the internal article number of new articles.
NUMBER_SOURCES = {
    "supplier_ref": "Leveranciersreferentie (bv. SIE6ES75101SK030AB0)",
    "manufacturer_ref": "Bestelcode fabrikant (bv. 6ES7510-1SK03-0AB0)",
    "supplier_code": "Interne code leverancier (bv. E5808005)",
}


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [f"{self.created} nieuw", f"{self.updated} bijgewerkt"]
        if self.unchanged:
            parts.append(f"{self.unchanged} ongewijzigd")
        if self.skipped:
            parts.append(f"{self.skipped} overgeslagen")
        return ", ".join(parts)


def _apply(article: Article, item: CatalogItem, catalog: SupplierCatalog) -> bool:
    """Copy catalogue data onto ``article``. Returns True when something changed."""
    values = {
        "description": item.description,
        "manufacturer": item.manufacturer,
        "manufacturer_ref": item.manufacturer_ref,
        "supplier_id": catalog.supplier_id,
        "supplier_ref": item.supplier_ref,
        "unit": item.unit,
        "unit_price": item.net_price,
        "ean": item.ean,
        "url": item.url,
    }
    changed = False
    for attr, value in values.items():
        if getattr(article, attr) != value:
            setattr(article, attr, value)
            changed = True
    return changed


def import_items(
    catalog: SupplierCatalog,
    items: sa.Select,
    number_source: str = "supplier_ref",
    category: str | None = None,
    update_existing: bool = True,
) -> ImportResult:
    """Create articles for the catalogue items selected by the ``items`` query.

    * Items that already have an article (same supplier + supplier reference)
      are updated when ``update_existing`` is set (description, price, ...),
      otherwise left alone. Their article number and category never change.
    * New articles get their article number from ``number_source``. When that
      number is already used by an unrelated article the item is skipped and
      reported, nothing is overwritten.

    The caller commits.
    """
    result = ImportResult()
    category = (category or "").strip() or None
    # Lookups loaded once: article numbers in use, and articles per supplier ref.
    used_numbers = set(db.session.scalars(sa.select(Article.article_number)))
    by_ref = {
        a.supplier_ref: a
        for a in db.session.scalars(
            sa.select(Article).where(Article.supplier_id == catalog.supplier_id, Article.supplier_ref.is_not(None))
        )
    }

    for item in db.session.scalars(items).yield_per(1000):
        article = by_ref.get(item.supplier_ref)
        if article is not None:
            if not update_existing:
                result.unchanged += 1
            elif _apply(article, item, catalog):
                result.updated += 1
            else:
                result.unchanged += 1
            continue

        number = (getattr(item, number_source) or "").strip()
        if not number:
            result.skipped += 1
            result.errors.append(f"{item.supplier_ref}: geen waarde voor het gekozen artikelnummer.")
            continue
        if number in used_numbers:
            result.skipped += 1
            result.errors.append(
                f"{item.supplier_ref}: artikelnummer {number} is al in gebruik door een ander artikel."
            )
            continue

        article = Article(article_number=number, category=category, active=True)
        _apply(article, item, catalog)
        db.session.add(article)
        used_numbers.add(number)
        by_ref[item.supplier_ref] = article
        result.created += 1
    return result


def update_prices(catalog: SupplierCatalog) -> ImportResult:
    """Refresh every article that was imported from this supplier before."""
    return import_items(
        catalog, filtered_items(catalog, status=STATUS_IMPORTED), update_existing=True
    )
