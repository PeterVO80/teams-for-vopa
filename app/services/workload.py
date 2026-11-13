"""Team workload calculation for the team overview.

The overview is a grid: one row per team member and one column per day
(weekends included, so weekend work can be planned too). For every cell we
collect the assignments that cover that day and sum their hours; that sum
compared to a standard working day gives the load.

Capacity is only counted on working days: an empty Saturday is "weekend",
not "free", and hours planned in the weekend come *on top* of the normal
capacity (so they can push the period load above 100%).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import sqlalchemy as sa

from ..extensions import db
from ..models import OPEN_STATUSES, Assignment, Task, User, is_weekend


@dataclass
class DayCell:
    """Planning information for one person on one day."""

    day: date
    assignments: list[Assignment] = field(default_factory=list)
    planned_hours: float = 0.0
    is_absent: bool = False

    @property
    def is_weekend(self) -> bool:
        return is_weekend(self.day)

    def load_percent(self, workday_hours: float) -> int:
        return round(self.planned_hours / workday_hours * 100) if workday_hours else 0

    def level(self, workday_hours: float) -> str:
        """Classify the cell: free / weekend / partial / full / over(booked) / absent."""
        if self.is_absent:
            return "absent"
        pct = self.load_percent(workday_hours)
        if pct == 0:
            return "weekend" if self.is_weekend else "free"
        if pct < 100:
            return "partial"
        if pct == 100:
            return "full"
        return "over"


@dataclass
class Segment:
    """One planning bar: consecutive planned days of one assignment.

    An assignment becomes several segments when it is interrupted, e.g. by a
    weekend that is not planned (``include_weekends`` off).
    """

    assignment: Assignment
    #: Index of the first and last day column covered (inclusive).
    first: int
    last: int
    #: Vertical position within the member's row (0 = top), so overlapping
    #: bars (e.g. 4h project A + 4h project B) are drawn below each other.
    lane: int = 0
    #: The assignment continues before/after the visible period.
    continues_before: bool = False
    continues_after: bool = False

    @property
    def span(self) -> int:
        return self.last - self.first + 1


def build_segments(assignments: list[Assignment], days: list[date]) -> tuple[list[Segment], int]:
    """Turn assignments into bars per run of consecutive covered days.

    Returns the segments (with a lane each) and the number of lanes needed.
    """
    segments: list[Segment] = []
    for assignment in sorted(assignments, key=lambda a: (a.start_date, a.id or 0)):
        run_start = None
        for index, day in enumerate(days + [None]):  # sentinel closes the last run
            covered = day is not None and assignment.covers(day)
            if covered and run_start is None:
                run_start = index
            elif not covered and run_start is not None:
                segments.append(Segment(
                    assignment=assignment, first=run_start, last=index - 1,
                    continues_before=run_start == 0 and assignment.start_date < days[0],
                    continues_after=index == len(days) and assignment.end_date > days[-1],
                ))
                run_start = None

    # Greedy lane assignment: put each bar in the first lane that is free.
    lane_ends: list[int] = []
    for segment in sorted(segments, key=lambda s: (s.first, -s.span)):
        for lane, end in enumerate(lane_ends):
            if end < segment.first:
                segment.lane, lane_ends[lane] = lane, segment.last
                break
        else:
            segment.lane = len(lane_ends)
            lane_ends.append(segment.last)
    return segments, len(lane_ends)


@dataclass
class MemberRow:
    """One row in the team overview grid."""

    user: User
    cells: list[DayCell]
    open_tasks: int = 0
    open_task_hours: float = 0.0
    segments: list[Segment] = field(default_factory=list)
    lanes: int = 0

    def period_load(self, workday_hours: float) -> int:
        """Planned hours as a percentage of the available capacity.

        Capacity = working days that are not absence days. Weekend hours count
        as planned hours but add no capacity (they are extra work).
        """
        present = [c for c in self.cells if not c.is_absent]
        capacity = sum(workday_hours for c in present if not c.is_weekend)
        if not capacity:
            return 0
        planned = sum(c.planned_hours for c in present)
        return round(planned / capacity * 100)


def period_days(start: date, weeks: int) -> list[date]:
    """All dates (Monday to Sunday) in ``weeks`` weeks starting at ``start``."""
    return [start + timedelta(days=i) for i in range(weeks * 7)]


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def build_overview(start: date, weeks: int) -> tuple[list[date], list[MemberRow]]:
    """Return the list of days and a row per active user for the given period."""
    days = period_days(start, weeks)
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
        segments, lanes = build_segments(by_user.get(user.id, []), days)
        rows.append(MemberRow(user=user, cells=cells, open_tasks=count, open_task_hours=hours,
                              segments=segments, lanes=lanes))
    return days, rows
