"""Team planning: who is scheduled on what, and who is absent."""

from __future__ import annotations

from datetime import date, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import LabeledEnum, TimestampMixin, enum_column


def is_weekend(day: date) -> bool:
    """Saturday or Sunday."""
    return day.weekday() >= 5


class AssignmentKind(LabeledEnum):
    """Type of planning entry. Everything except PROJECT/OTHER is an absence."""

    PROJECT = ("project", "Project")
    LEAVE = ("leave", "Verlof")
    SICK = ("sick", "Ziek")
    TRAINING = ("training", "Opleiding")
    OTHER = ("other", "Andere")

    @property
    def is_absence(self) -> bool:
        return self in (AssignmentKind.LEAVE, AssignmentKind.SICK, AssignmentKind.TRAINING)

    @property
    def css_class(self) -> str:
        """Bootstrap colour used to render this kind in the planning grid."""
        return {
            "project": "primary",
            "leave": "success",
            "sick": "danger",
            "training": "info",
            "other": "secondary",
        }[self.value]


class Assignment(TimestampMixin, db.Model):
    """A period (one or more days) in which a person is booked.

    ``hours_per_day`` lets you plan part-time work, e.g. 4h/day on project A
    and 4h/day on project B in the same week.

    By default only Monday-Friday inside the period are planned, so a period
    of two weeks does not silently book the weekend in between. Set
    ``include_weekends`` to also plan the Saturdays and Sundays in the period
    (e.g. a commissioning during a production stop).
    """

    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(sa.ForeignKey("projects.id"))
    kind: Mapped[AssignmentKind] = mapped_column(
        enum_column(AssignmentKind), default=AssignmentKind.PROJECT
    )
    start_date: Mapped[date] = mapped_column(index=True)
    end_date: Mapped[date] = mapped_column(index=True)
    hours_per_day: Mapped[float] = mapped_column(default=8.0)
    include_weekends: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    note: Mapped[str | None] = mapped_column(sa.String(300))

    user = relationship("User")
    project = relationship("Project")

    __table_args__ = (
        sa.CheckConstraint("end_date >= start_date", name="end_after_start"),
    )

    def covers(self, day: date) -> bool:
        """Return ``True`` when this assignment applies to ``day``.

        Weekend days only count when ``include_weekends`` is set.
        """
        if not self.start_date <= day <= self.end_date:
            return False
        return self.include_weekends or not is_weekend(day)

    @property
    def planned_days(self) -> int:
        """Number of days in the period that are actually planned."""
        days = (self.end_date - self.start_date).days + 1
        return sum(
            1 for i in range(days)
            if self.include_weekends or not is_weekend(self.start_date + timedelta(days=i))
        )

    @property
    def label(self) -> str:
        if self.kind == AssignmentKind.PROJECT and self.project:
            return self.project.code
        return self.kind.label
