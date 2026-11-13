"""Article database: the catalogue of materials that can be ordered."""

from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .base import TimestampMixin


class Article(TimestampMixin, db.Model):
    """A purchasable item (PLC, cable, sensor, terminal, ...)."""

    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Internal article number - the unique key used by the Excel import.
    article_number: Mapped[str] = mapped_column(sa.String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(sa.String(300))
    manufacturer: Mapped[str | None] = mapped_column(sa.String(120), index=True)
    #: Order code of the manufacturer, e.g. ``6ES7 214-1AG40-0XB0``.
    manufacturer_ref: Mapped[str | None] = mapped_column(sa.String(120))
    supplier: Mapped[str | None] = mapped_column(sa.String(120))
    #: The supplier's own reference, e.g. Rexel ``SIE6ES72141AG400XB0``. Used to
    #: find the article again when a new price list of that supplier arrives.
    supplier_ref: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    ean: Mapped[str | None] = mapped_column(sa.String(20))
    #: Product page in the supplier's web shop.
    url: Mapped[str | None] = mapped_column(sa.String(300))
    category: Mapped[str | None] = mapped_column(sa.String(120), index=True)
    unit: Mapped[str] = mapped_column(sa.String(20), default="st")
    #: Net purchase price per unit. 4 decimals: cable is priced per metre
    #: (e.g. 0.3422 EUR/m), where rounding to cents would be inaccurate.
    unit_price: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 4))
    notes: Mapped[str | None] = mapped_column(sa.Text)
    #: Reorder level: the article is flagged when total stock drops below it.
    #: Leave empty for articles that are not kept in stock.
    min_stock: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 2))
    #: Obsolete articles stay in the database for history but are hidden by default.
    active: Mapped[bool] = mapped_column(default=True)

    @property
    def label(self) -> str:
        return f"{self.article_number} - {self.description}"

    def __repr__(self) -> str:
        return f"<Article {self.article_number}>"
