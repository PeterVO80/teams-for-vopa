"""Projects: every customer assignment is handled as a project."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import LabeledEnum, TimestampMixin, enum_column


class ProjectStatus(LabeledEnum):
    PLANNED = ("planned", "Gepland")
    ACTIVE = ("active", "Actief")
    ON_HOLD = ("on_hold", "On hold")
    CLOSED = ("closed", "Afgesloten")


class Project(TimestampMixin, db.Model):
    """A customer assignment. Orders, tasks, planning and notes link to it."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Short unique reference, e.g. ``P2026-014``.
    code: Mapped[str] = mapped_column(sa.String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(sa.String(200))
    customer: Mapped[str | None] = mapped_column(sa.String(200))
    description: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[ProjectStatus] = mapped_column(
        enum_column(ProjectStatus), default=ProjectStatus.ACTIVE
    )
    start_date: Mapped[date | None]
    end_date: Mapped[date | None]
    manager_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))

    manager = relationship("User")

    @property
    def display_name(self) -> str:
        return f"{self.code} - {self.name}"

    @classmethod
    def open_choices(cls, include_empty: bool = True) -> list[tuple[int, str]]:
        """Select options for every project that is not closed.

        ``0`` is used as the "no project" value because WTForms coerces the
        submitted value with ``int`` and cannot represent ``None``.
        """
        projects = db.session.scalars(
            sa.select(cls)
            .where(cls.status != ProjectStatus.CLOSED)
            .order_by(cls.code)
        )
        options = [(p.id, p.display_name) for p in projects]
        return ([(0, "- geen project -")] if include_empty else []) + options

    def __repr__(self) -> str:
        return f"<Project {self.code}>"
