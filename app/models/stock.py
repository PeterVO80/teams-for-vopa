"""Stock management: stock locations and stock movements.

Stock is never stored as a number that gets overwritten. Every change is a
:class:`StockMovement` (positive = in, negative = out) and the stock of an
article on a location is the **sum of its movements**. This gives a complete
audit trail (who took what, when, for which project) and makes it impossible
for the stock figure and its history to disagree. Mistakes are corrected with
a new correction or count movement, not by editing history.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import LabeledEnum, TimestampMixin, enum_column, utcnow


class StockLocation(TimestampMixin, db.Model):
    """A place where stock is kept: the warehouse, a service van, ..."""

    __tablename__ = "stock_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(80), unique=True)
    description: Mapped[str | None] = mapped_column(sa.String(200))
    #: Inactive locations are hidden from forms but keep their history.
    active: Mapped[bool] = mapped_column(default=True)

    @classmethod
    def choices(cls, include_empty: str | None = None) -> list[tuple[int, str]]:
        """``(id, name)`` of the active locations for select fields."""
        locations = db.session.scalars(
            sa.select(cls).where(cls.active.is_(True)).order_by(cls.name)
        )
        options = [(loc.id, loc.name) for loc in locations]
        return ([(0, include_empty)] if include_empty else []) + options


class MovementKind(LabeledEnum):
    """Type of stock movement."""

    RECEIPT = ("receipt", "Ontvangst")
    ISSUE = ("issue", "Uitgifte")
    TRANSFER = ("transfer", "Verplaatsing")
    COUNT = ("count", "Inventaris")
    CORRECTION = ("correction", "Correctie")

    @property
    def css_class(self) -> str:
        return {
            "receipt": "success",
            "issue": "primary",
            "transfer": "info",
            "count": "secondary",
            "correction": "warning",
        }[self.value]


class StockMovement(db.Model):
    """One change of the stock of one article on one location."""

    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(sa.ForeignKey("articles.id"), index=True)
    location_id: Mapped[int] = mapped_column(sa.ForeignKey("stock_locations.id"), index=True)
    kind: Mapped[MovementKind] = mapped_column(enum_column(MovementKind))
    #: Signed quantity: positive = into stock, negative = out of stock.
    quantity: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2))
    moved_on: Mapped[date] = mapped_column(default=date.today, index=True)
    #: Project the material was issued to (or returned from).
    project_id: Mapped[int | None] = mapped_column(sa.ForeignKey("projects.id"), index=True)
    #: Set when the movement was created by a delivery of a purchase order.
    goods_receipt_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("goods_receipts.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(sa.String(300))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)

    article = relationship("Article")
    location = relationship("StockLocation")
    project = relationship("Project")
    user = relationship("User")
    goods_receipt = relationship("GoodsReceipt", back_populates="stock_movements")
