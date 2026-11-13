"""Team planning: who is scheduled on what, and who is absent."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import LabeledEnum, TimestampMixin, enum_column


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
    note: Mapped[str | None] = mapped_column(sa.String(300))

    user = relationship("User")
    project = relationship("Project")

    __table_args__ = (
        sa.CheckConstraint("end_date >= start_date", name="end_after_start"),
    )

    def covers(self, day: date) -> bool:
        """Return ``True`` when this assignment applies to ``day``."""
        return self.start_date <= day <= self.end_date

    @property
    def label(self) -> str:
        if self.kind == AssignmentKind.PROJECT and self.project:
            return self.project.code
        return self.kind.label
