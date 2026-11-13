"""Punch list: tasks per team member, shown as a kanban board."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import LabeledEnum, TimestampMixin, enum_column


class TaskStatus(LabeledEnum):
    """Kanban columns, in the order they are displayed."""

    NEW = ("new", "Nieuw")
    IN_PROGRESS = ("in_progress", "Wordt uitgevoerd")
    ON_HOLD = ("on_hold", "On hold")
    DONE = ("done", "Voltooid")
    CANCELLED = ("cancelled", "Geannuleerd")

    @property
    def is_open(self) -> bool:
        """Open tasks still represent work (used for workload counts)."""
        return self in (TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.ON_HOLD)


#: Statuses that still count as outstanding work.
OPEN_STATUSES = [s for s in TaskStatus if s.is_open]


class TaskPriority(LabeledEnum):
    LOW = ("low", "Laag")
    NORMAL = ("normal", "Normaal")
    HIGH = ("high", "Hoog")
    URGENT = ("urgent", "Dringend")


class Task(TimestampMixin, db.Model):
    """A single punch list item."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(sa.String(200))
    description: Mapped[str | None] = mapped_column(sa.Text)
    project_id: Mapped[int | None] = mapped_column(sa.ForeignKey("projects.id"), index=True)
    assignee_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"), index=True)
    created_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))
    status: Mapped[TaskStatus] = mapped_column(
        enum_column(TaskStatus), default=TaskStatus.NEW, index=True
    )
    priority: Mapped[TaskPriority] = mapped_column(
        enum_column(TaskPriority), default=TaskPriority.NORMAL
    )
    due_date: Mapped[date | None]
    #: Estimated effort in hours (optional, used for workload overview).
    estimated_hours: Mapped[float | None]
    #: Sort order inside a kanban column (lower = higher on the board).
    position: Mapped[int] = mapped_column(default=0)

    project = relationship("Project")
    assignee = relationship("User", foreign_keys=[assignee_id])
    created_by = relationship("User", foreign_keys=[created_by_id])

    @property
    def is_overdue(self) -> bool:
        return (
            self.due_date is not None
            and self.due_date < date.today()
            and self.status.is_open
        )
