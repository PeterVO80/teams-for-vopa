"""Relations: customers and suppliers.

Projects, articles, purchase orders and supplier price lists refer to a
:class:`Company` instead of storing a typed name. That rules out spelling
variants ("Rexel", "REXEL", "Rexel nv") that would otherwise split reports
and searches. One company can be both a customer and a supplier.
"""

from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .base import TimestampMixin


class Company(TimestampMixin, db.Model):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(200), unique=True, index=True)
    is_customer: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    is_supplier: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    vat_number: Mapped[str | None] = mapped_column(sa.String(30))
    street: Mapped[str | None] = mapped_column(sa.String(200))
    postal_code: Mapped[str | None] = mapped_column(sa.String(20))
    city: Mapped[str | None] = mapped_column(sa.String(120))
    country: Mapped[str | None] = mapped_column(sa.String(80), default="België")
    contact_person: Mapped[str | None] = mapped_column(sa.String(120))
    phone: Mapped[str | None] = mapped_column(sa.String(50))
    email: Mapped[str | None] = mapped_column(sa.String(120))
    website: Mapped[str | None] = mapped_column(sa.String(200))
    notes: Mapped[str | None] = mapped_column(sa.Text)
    #: Distance office -> this address by road, one way, in km (services/distance.py).
    #: Used for the kilometres of field service in the time registration.
    distance_km: Mapped[Decimal | None] = mapped_column(sa.Numeric(7, 1))
    #: True when distance_km was calculated (and may be recalculated when the
    #: address changes); False when someone typed it in.
    distance_auto: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    #: Inactive relations are hidden from selection lists but keep their history.
    active: Mapped[bool] = mapped_column(default=True, server_default=sa.true())

    def __str__(self) -> str:
        # Templates and f-strings print the name, e.g. {{ project.customer }}.
        return self.name

    @property
    def kinds_label(self) -> str:
        return " & ".join(k for k, on in (("Klant", self.is_customer), ("Leverancier", self.is_supplier)) if on) or "-"

    @property
    def address_lines(self) -> list[str]:
        city = " ".join(p for p in (self.postal_code, self.city) if p)
        return [line for line in (self.street, city, self.country) if line]

    @classmethod
    def by_name(cls, name: str | None) -> "Company | None":
        """Find a relation by name, ignoring case and surrounding spaces."""
        if not name or not name.strip():
            return None
        return db.session.scalar(sa.select(cls).where(sa.func.lower(cls.name) == name.strip().lower()))

    @classmethod
    def choices(cls, kind: str, current_id: int | None = None, empty: str | None = None) -> list[tuple[int, str]]:
        """``(id, name)`` options for a select: active customers or suppliers.

        ``current_id`` is always included (e.g. a relation that became
        inactive but is still linked to the record being edited).
        """
        flag = cls.is_customer if kind == "customer" else cls.is_supplier
        query = sa.select(cls).where(sa.or_(sa.and_(flag.is_(True), cls.active.is_(True)), cls.id == (current_id or 0)))
        options = [(c.id, c.name) for c in db.session.scalars(query.order_by(cls.name))]
        return ([(0, empty)] if empty else []) + options

    def __repr__(self) -> str:
        return f"<Company {self.name}>"
