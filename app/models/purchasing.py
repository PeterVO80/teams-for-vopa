"""Purchasing: purchase orders, order lines and goods receipts (deliveries).

Delivery tracking works at line level: every physical delivery is recorded as
a :class:`GoodsReceipt` against an order line. The received quantity of a line
is the sum of its receipts, and the status of the whole order is derived from
its lines (see :meth:`PurchaseOrder.refresh_status`). Keeping each receipt as a
separate record gives a full delivery history (who received what, when).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import LabeledEnum, TimestampMixin, enum_column


class OrderStatus(LabeledEnum):
    DRAFT = ("draft", "Concept")
    ORDERED = ("ordered", "Besteld")
    PARTIAL = ("partial", "Deels geleverd")
    DELIVERED = ("delivered", "Volledig geleverd")
    CANCELLED = ("cancelled", "Geannuleerd")


class PurchaseOrder(TimestampMixin, db.Model):
    """An order placed with one supplier, usually for one project."""

    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Order reference, e.g. the PO number sent to the supplier.
    number: Mapped[str] = mapped_column(sa.String(40), unique=True, index=True)
    project_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("projects.id"), index=True
    )
    supplier: Mapped[str] = mapped_column(sa.String(120))
    status: Mapped[OrderStatus] = mapped_column(
        enum_column(OrderStatus), default=OrderStatus.DRAFT
    )
    order_date: Mapped[date | None]
    expected_date: Mapped[date | None]
    notes: Mapped[str | None] = mapped_column(sa.Text)
    #: When set, deliveries of article lines are put in stock on this location
    #: automatically (replenishment orders). Empty = material for the project.
    stock_location_id: Mapped[int | None] = mapped_column(sa.ForeignKey("stock_locations.id"))
    created_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))

    project = relationship("Project")
    stock_location = relationship("StockLocation")
    created_by = relationship("User")
    lines: Mapped[list["PurchaseOrderLine"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="PurchaseOrderLine.id",
    )

    @property
    def total_amount(self) -> Decimal:
        return sum((line.line_total for line in self.lines), Decimal("0"))

    @property
    def delivery_progress(self) -> int:
        """Percentage (0-100) of ordered quantity that has been received."""
        ordered = sum((line.quantity for line in self.lines), Decimal("0"))
        if not ordered:
            return 0
        received = sum((line.quantity_received_capped for line in self.lines), Decimal("0"))
        return int(received / ordered * 100)

    @property
    def is_overdue(self) -> bool:
        return (
            self.expected_date is not None
            and self.expected_date < date.today()
            and self.status in (OrderStatus.ORDERED, OrderStatus.PARTIAL)
        )

    def refresh_status(self) -> None:
        """Derive the delivery status from the order lines.

        Draft and cancelled orders are left untouched: those are deliberate
        user decisions. For placed orders the status becomes *delivered* when
        every line is complete, *partial* when something was received and
        *ordered* otherwise.
        """
        if self.status in (OrderStatus.DRAFT, OrderStatus.CANCELLED) or not self.lines:
            return
        if all(line.is_complete for line in self.lines):
            self.status = OrderStatus.DELIVERED
        elif any(line.quantity_received > 0 for line in self.lines):
            self.status = OrderStatus.PARTIAL
        else:
            self.status = OrderStatus.ORDERED


class PurchaseOrderLine(db.Model):
    """One article (or free-text item) on a purchase order."""

    __tablename__ = "purchase_order_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True
    )
    #: Optional link to the article database. When empty, ``description`` is
    #: used on its own (handy for one-off items).
    article_id: Mapped[int | None] = mapped_column(sa.ForeignKey("articles.id"))
    description: Mapped[str] = mapped_column(sa.String(300))
    quantity: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2))
    unit: Mapped[str] = mapped_column(sa.String(20), default="st")
    unit_price: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 4))

    order: Mapped[PurchaseOrder] = relationship(back_populates="lines")
    article = relationship("Article")
    receipts: Mapped[list["GoodsReceipt"]] = relationship(
        back_populates="line",
        cascade="all, delete-orphan",
        order_by="GoodsReceipt.received_on",
    )

    @property
    def quantity_received(self) -> Decimal:
        return sum((r.quantity for r in self.receipts), Decimal("0"))

    @property
    def quantity_received_capped(self) -> Decimal:
        """Received quantity, capped at the ordered quantity (for progress %)."""
        return min(self.quantity_received, self.quantity)

    @property
    def quantity_outstanding(self) -> Decimal:
        return max(self.quantity - self.quantity_received, Decimal("0"))

    @property
    def is_complete(self) -> bool:
        return self.quantity_received >= self.quantity

    @property
    def line_total(self) -> Decimal:
        return (self.unit_price or Decimal("0")) * self.quantity


class GoodsReceipt(db.Model):
    """A (partial) delivery of an order line."""

    __tablename__ = "goods_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(
        sa.ForeignKey("purchase_order_lines.id", ondelete="CASCADE"), index=True
    )
    quantity: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2))
    received_on: Mapped[date] = mapped_column(default=date.today)
    received_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))
    #: E.g. delivery note number, storage location or a remark about damage.
    note: Mapped[str | None] = mapped_column(sa.String(300))

    line: Mapped[PurchaseOrderLine] = relationship(back_populates="receipts")
    received_by = relationship("User")
    #: Stock movement(s) created by this delivery; removed with it when a
    #: delivery is undone.
    stock_movements = relationship(
        "StockMovement", back_populates="goods_receipt", cascade="all, delete-orphan"
    )
