"""Time registration: hours worked on a project (blueprints/timesheet.py)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import TimestampMixin


class TimeEntry(TimestampMixin, db.Model):
    """Hours one employee worked on one project on one day.

    ``field_service`` (buitendienst): work at the customer; ``km`` is then the
    distance driven (by default the round trip office - customer, from
    ``Company.distance_km`` of ``customer``, and editable).
    """

    __tablename__ = "time_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id"), index=True)
    project_id: Mapped[int] = mapped_column(sa.ForeignKey("projects.id"), index=True)
    work_date: Mapped[date] = mapped_column(index=True)
    hours: Mapped[Decimal] = mapped_column(sa.Numeric(5, 2))
    description: Mapped[str | None] = mapped_column(sa.Text)
    field_service: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    customer_id: Mapped[int | None] = mapped_column(sa.ForeignKey("companies.id"))
    km: Mapped[Decimal | None] = mapped_column(sa.Numeric(7, 1))

    user = relationship("User")
    project = relationship("Project")
    customer = relationship("Company")
