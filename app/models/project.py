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


#: Team members of a project (many-to-many). Employees who are not an admin
#: only see the projects they are a member of (or manage) - see app/access.py.
project_members = sa.Table(
    "project_members",
    db.metadata,
    sa.Column("project_id", sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("user_id", sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


class Project(TimestampMixin, db.Model):
    """A customer assignment. Orders, tasks, planning and notes link to it."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Unique reference, generated automatically: ``PRJ/2026/0001`` (see
    #: :meth:`assign_code`). Projects created before automatic numbering keep
    #: their original code.
    code: Mapped[str] = mapped_column(sa.String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(sa.String(200))
    customer_id: Mapped[int | None] = mapped_column(sa.ForeignKey("companies.id"), index=True)
    description: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[ProjectStatus] = mapped_column(
        enum_column(ProjectStatus), default=ProjectStatus.ACTIVE
    )
    start_date: Mapped[date | None]
    end_date: Mapped[date | None]
    manager_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))

    manager = relationship("User")
    #: Employees linked to the project (the manager always counts as linked too).
    members = relationship("User", secondary=project_members, order_by="User.full_name")
    #: The customer (a relation marked as customer, see models/company.py).
    customer = relationship("Company")

    def has_member(self, user) -> bool:
        """True when ``user`` is linked to this project (member or manager)."""
        return user is not None and (self.manager_id == user.id or user in self.members)

    def add_member(self, user) -> bool:
        """Link ``user`` to the project; returns True when newly added."""
        if user is None or self.has_member(user):
            return False
        self.members.append(user)
        return True

    @property
    def team(self) -> list:
        """Manager + members, without duplicates (for display)."""
        people = ([self.manager] if self.manager else []) + [m for m in self.members if m != self.manager]
        return people

    def assign_code(self) -> None:
        """Give this project the next automatic code, e.g. ``PRJ/2026/0001``.

        The code is assigned once, when the project is created, and never
        changes afterwards (it is printed on documents and used in folder
        names, so it must stay stable).
        """
        from flask import current_app

        from ..services.numbering import next_number

        self.code = next_number(Project.code, current_app.config["PROJECT_CODE_PREFIX"])

    @property
    def customer_label(self) -> str:
        """Customer and project description, e.g. "Brouwerij De Kroon - Verpakkingslijn L3".

        Used wherever a project is shown in a short label (kanban cards,
        planning bars), because people know projects by customer and name
        rather than by project number.
        """
        return f"{self.customer} - {self.name}" if self.customer else self.name

    @property
    def display_name(self) -> str:
        return f"{self.code} - {self.name}"

    @classmethod
    def open_choices(cls, include_empty: bool = True) -> list[tuple[int, str]]:
        """Select options for every project that is not closed.

        Only projects the logged-in user may see are offered (app/access.py),
        so every project selection list in the application respects access.
        ``0`` is used as the "no project" value because WTForms coerces the
        submitted value with ``int`` and cannot represent ``None``.
        """
        from ..access import visible_projects_condition

        query = sa.select(cls).where(cls.status != ProjectStatus.CLOSED).order_by(cls.code)
        condition = visible_projects_condition()
        if condition is not None:
            query = query.where(condition)
        projects = db.session.scalars(query)
        options = [(p.id, p.display_name) for p in projects]
        return ([(0, "- geen project -")] if include_empty else []) + options

    def __repr__(self) -> str:
        return f"<Project {self.code}>"
