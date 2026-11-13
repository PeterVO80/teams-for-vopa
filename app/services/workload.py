"""Team workload calculation for the team overview.

The overview is a grid: one row per team member and one column per working
day. For every cell we collect the assignments that cover that day and sum
their hours; that sum compared to a standard working day gives the load.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import sqlalchemy as sa

from ..extensions import db
from ..models import OPEN_STATUSES, Assignment, Task, User


@dataclass
class DayCell:
    """Planning information for one person on one day."""

    day: date
    assignments: list[Assignment] = field(default_factory=list)
    planned_hours: float = 0.0
    is_absent: bool = False

    def load_percent(self, workday_hours: float) -> int:
        return round(self.planned_hours / workday_hours * 100) if workday_hours else 0

    def level(self, workday_hours: float) -> str:
        """Classify the cell: free / partial / full / over(booked) / absent."""
        if self.is_absent:
            return "absent"
        pct = self.load_percent(workday_hours)
        if pct == 0:
            return "free"
        if pct < 100:
            return "partial"
        if pct == 100:
            return "full"
        return "over"


@dataclass
class MemberRow:
    """One row in the team overview grid."""

    user: User
    cells: list[DayCell]
    open_tasks: int = 0
    open_task_hours: float = 0.0

    def period_load(self, workday_hours: float) -> int:
        """Average load over the whole period, ignoring absence days."""
        workdays = [c for c in self.cells if not c.is_absent]
        if not workdays:
            return 0
        planned = sum(c.planned_hours for c in workdays)
        return round(planned / (len(workdays) * workday_hours) * 100)


def working_days(start: date, weeks: int) -> list[date]:
    """All Monday-Friday dates in ``weeks`` weeks starting at ``start``."""
    days = (start + timedelta(days=i) for i in range(weeks * 7))
    return [d for d in days if d.weekday() < 5]


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def build_overview(start: date, weeks: int) -> tuple[list[date], list[MemberRow]]:
    """Return the list of days and a row per active user for the given period."""
    days = working_days(start, weeks)
    end = start + timedelta(days=weeks * 7 - 1)

    users = db.session.scalars(
        sa.select(User).where(User.active.is_(True)).order_by(User.full_name)
    ).all()

    # One query for all assignments overlapping the period.
    assignments = db.session.scalars(
        sa.select(Assignment).where(
            Assignment.start_date <= end, Assignment.end_date >= start
        )
    ).all()
    by_user: dict[int, list[Assignment]] = {}
    for a in assignments:
        by_user.setdefault(a.user_id, []).append(a)

    # Open task counts / estimated hours per user in one aggregate query.
    task_stats = {
        user_id: (count, hours or 0.0)
        for user_id, count, hours in db.session.execute(
            sa.select(Task.assignee_id, sa.func.count(Task.id), sa.func.sum(Task.estimated_hours))
            .where(Task.status.in_(OPEN_STATUSES))
            .group_by(Task.assignee_id)
        )
    }

    rows = []
    for user in users:
        cells = []
        for day in days:
            covering = [a for a in by_user.get(user.id, []) if a.covers(day)]
            cells.append(
                DayCell(
                    day=day,
                    assignments=covering,
                    planned_hours=sum(a.hours_per_day for a in covering if not a.kind.is_absence),
                    is_absent=any(a.kind.is_absence for a in covering),
                )
            )
        count, hours = task_stats.get(user.id, (0, 0.0))
        rows.append(MemberRow(user=user, cells=cells, open_tasks=count, open_task_hours=hours))
    return days, rows
