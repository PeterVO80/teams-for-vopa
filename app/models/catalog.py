"""Supplier catalogues: price lists delivered by suppliers (e.g. Rexel).

A supplier's price list can contain hundreds of thousands of articles, far
more than the company ever uses. So the file is not imported into the article
database directly. It is loaded into a separate *catalogue* first; there the
user filters (manufacturer, product group, search text), selects the articles
that are needed and imports only those (see ``services/supplier_catalog.py``).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import TimestampMixin


class SupplierCatalog(TimestampMixin, db.Model):
    """One uploaded price list file."""

    __tablename__ = "supplier_catalogs"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: The supplier (relation), copied to ``Article.supplier`` on import.
    supplier_id: Mapped[int] = mapped_column(sa.ForeignKey("companies.id"), index=True)
    filename: Mapped[str] = mapped_column(sa.String(255))
    item_count: Mapped[int] = mapped_column(default=0)
    #: Price date as stated in the file (newest date found).
    price_date: Mapped[date | None]
    uploaded_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))

    uploaded_by = relationship("User")
    supplier = relationship("Company")
    # passive_deletes: let PostgreSQL's ON DELETE CASCADE remove the (many)
    # items in one statement instead of loading them all into Python.
    items = relationship("CatalogItem", back_populates="catalog", passive_deletes=True)


class CatalogItem(db.Model):
    """One article in a supplier catalogue."""

    __tablename__ = "catalog_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    catalog_id: Mapped[int] = mapped_column(
        sa.ForeignKey("supplier_catalogs.id", ondelete="CASCADE"), index=True
    )
    #: Supplier reference (Rexel: REXEL_REF, e.g. ``SIE6ES75101SK030AB0``).
    supplier_ref: Mapped[str] = mapped_column(sa.String(64), index=True)
    #: Supplier's internal code (Rexel: ERP_NUMB, e.g. ``E5808005``).
    supplier_code: Mapped[str | None] = mapped_column(sa.String(32))
    manufacturer: Mapped[str | None] = mapped_column(sa.String(120), index=True)
    manufacturer_ref: Mapped[str | None] = mapped_column(sa.String(120))
    product_group: Mapped[str | None] = mapped_column(sa.String(60))
    #: Readable product group, e.g. "SIE 255" or "MOD TBS W SCREW CONNECTION".
    product_group_desc: Mapped[str | None] = mapped_column(sa.String(120), index=True)
    description: Mapped[str] = mapped_column(sa.String(300))
    description_fr: Mapped[str | None] = mapped_column(sa.String(300))
    unit: Mapped[str] = mapped_column(sa.String(20), default="st")
    net_price: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 4))
    gross_price: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 4))
    ean: Mapped[str | None] = mapped_column(sa.String(20))
    url: Mapped[str | None] = mapped_column(sa.String(300))
    min_order_qty: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 2))
    price_date: Mapped[date | None]

    catalog: Mapped[SupplierCatalog] = relationship(back_populates="items")
