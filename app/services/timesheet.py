"""Time registration: grouping and periods for the timesheet (urenstaat).

The timesheet of a project lists the hours per day per employee: all entries
of one employee on one day are added up, their descriptions joined, and the
kilometres of field service added. Totals per employee and overall follow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import sqlalchemy as sa

from ..extensions import db
from ..models import TimeEntry, User

ZERO = Decimal("0")


def previous_month(today: date | None = None) -> tuple[date, date]:
    """First and last day of the month before ``today`` (default for the timesheet)."""
    first_this = (today or date.today()).replace(day=1)
    last = first_this - timedelta(days=1)
    return last.replace(day=1), last


def month_bounds(month: str | None, today: date | None = None) -> tuple[date, date]:
    """``"2026-11"`` -> (2026-11-01, 2026-11-30); invalid/empty = this month."""
    try:
        year, mon = (int(x) for x in (month or "").split("-"))
        first = date(year, mon, 1)
    except ValueError:
        first = (today or date.today()).replace(day=1)
    next_first = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, next_first - timedelta(days=1)


@dataclass
class DayRow:
    """Hours of one employee on one day (several entries added up)."""

    work_date: date
    user: User
    hours: Decimal = ZERO
    km: Decimal = ZERO
    field_service: bool = False
    descriptions: list[str] = field(default_factory=list)

    @property
    def description(self) -> str:
        return "\n".join(self.descriptions)


@dataclass
class Timesheet:
    rows: list[DayRow]
    per_user: list[tuple[User, Decimal, Decimal]]   # (employee, hours, km)

    @property
    def total_hours(self) -> Decimal:
        return sum((r.hours for r in self.rows), ZERO)

    @property
    def total_km(self) -> Decimal:
        return sum((r.km for r in self.rows), ZERO)


def entries(project_id: int, start: date, end: date) -> list[TimeEntry]:
    return db.session.scalars(
        sa.select(TimeEntry).join(User, TimeEntry.user_id == User.id)
        .where(TimeEntry.project_id == project_id, TimeEntry.work_date >= start, TimeEntry.work_date <= end)
        .order_by(TimeEntry.work_date, User.full_name, TimeEntry.id)
    ).all()


def build(project_id: int, start: date, end: date) -> Timesheet:
    grouped: dict[tuple, DayRow] = {}
    for e in entries(project_id, start, end):
        row = grouped.setdefault((e.work_date, e.user_id), DayRow(e.work_date, e.user))
        row.hours += e.hours
        row.km += e.km or ZERO
        row.field_service = row.field_service or e.field_service
        if e.description and e.description.strip():
            row.descriptions.append(e.description.strip())
    rows = list(grouped.values())
    totals: dict[int, list] = {}
    for r in rows:
        t = totals.setdefault(r.user.id, [r.user, ZERO, ZERO])
        t[1] += r.hours
        t[2] += r.km
    per_user = sorted((tuple(t) for t in totals.values()), key=lambda t: t[0].full_name)
    return Timesheet(rows=rows, per_user=per_user)
