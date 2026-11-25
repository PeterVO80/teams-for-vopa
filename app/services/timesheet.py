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
from ..models import Project, TimeEntry, User

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
    return _sheet(entries(project_id, start, end))


def build_all(start: date, end: date, project_condition=None) -> list[tuple]:
    """Timesheets of every project with hours in the period: [(project, Timesheet)].

    ``project_condition`` limits the projects (app/access.py: the projects
    the user may see). Sorted by project code.
    """
    query = (sa.select(TimeEntry).join(User, TimeEntry.user_id == User.id)
             .join(Project, TimeEntry.project_id == Project.id)
             .where(TimeEntry.work_date >= start, TimeEntry.work_date <= end)
             .order_by(Project.code, TimeEntry.work_date, User.full_name, TimeEntry.id))
    if project_condition is not None:
        query = query.where(project_condition)
    per_project: dict[int, list[TimeEntry]] = {}
    for e in db.session.scalars(query):
        per_project.setdefault(e.project_id, []).append(e)
    return [(items[0].project, _sheet(items)) for items in per_project.values()]


def _sheet(items: list[TimeEntry]) -> Timesheet:
    grouped: dict[tuple, DayRow] = {}
    for e in items:
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


# ------------------------------------------------------------------ overview per week
@dataclass
class Day:
    day: date
    entries: list[TimeEntry]

    @property
    def total(self) -> Decimal:
        return sum((e.hours for e in self.entries), ZERO)


@dataclass
class Week:
    start: date                      # Monday
    days: list[Day]

    @property
    def end(self) -> date:
        return self.start + timedelta(days=6)

    @property
    def number(self) -> int:
        return self.start.isocalendar()[1]

    @property
    def total(self) -> Decimal:
        return sum((d.total for d in self.days), ZERO)

    @property
    def total_km(self) -> Decimal:
        return sum((e.km or ZERO for d in self.days for e in d.entries), ZERO)


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def weeks(items: list[TimeEntry], first: date, last: date, *, empty_workdays_until: date | None = None) -> list[Week]:
    """Entries of ``first``..``last`` grouped per week (newest week first) and per day.

    With ``empty_workdays_until`` the working days (Mon-Fri) up to that date
    without any hours are included too, so gaps in someone's hours stand out.
    """
    by_day: dict[date, list[TimeEntry]] = {}
    for e in sorted(items, key=lambda e: (e.work_date, e.id)):
        by_day.setdefault(e.work_date, []).append(e)
    if empty_workdays_until:
        day = first
        while day <= min(last, empty_workdays_until):
            if day.weekday() < 5:
                by_day.setdefault(day, [])
            day += timedelta(days=1)
    result: dict[date, Week] = {}
    for day in sorted(by_day):
        week = result.setdefault(monday(day), Week(monday(day), []))
        week.days.append(Day(day, by_day[day]))
    return sorted(result.values(), key=lambda w: w.start, reverse=True)


def hours_per_day(user_id: int, start: date, end: date) -> dict[date, Decimal]:
    """Hours already registered by an employee per day (all projects)."""
    rows = db.session.execute(
        sa.select(TimeEntry.work_date, sa.func.sum(TimeEntry.hours))
        .where(TimeEntry.user_id == user_id, TimeEntry.work_date >= start, TimeEntry.work_date <= end)
        .group_by(TimeEntry.work_date)
    ).all()
    return {d: Decimal(h) for d, h in rows}
