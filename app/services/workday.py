"""Working hours: when a planned day starts and ends.

The company's working day (default 08:30 - 17:00 with a break from 12:00 to
12:30, set with WORKDAY_START / WORKDAY_END / BREAK_START / BREAK_END) is
used

* for planning entries without explicit hours: a full day is 08:30 - 17:00,
  4 hours is 08:30 - 12:30 ... (the break is skipped);
* to calculate the hours of an entry planned "per hour" (08:30 - 12:00 =
  3.5 hours; the break does not count);
* for the times of the Outlook appointments (services/outlook.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from flask import current_app


@dataclass(frozen=True)
class Workday:
    start: time
    end: time
    break_start: time
    break_end: time

    @property
    def hours(self) -> float:
        """Working hours in a full day (8.0 for 08:30 - 17:00 with 30 min break)."""
        return worked_hours(self.start, self.end, self)


def _parse(value: str, default: str) -> time:
    try:
        return time.fromisoformat((value or default).strip())
    except ValueError:
        return time.fromisoformat(default)


def workday() -> Workday:
    config = current_app.config
    return Workday(
        start=_parse(config.get("WORKDAY_START"), "08:30"),
        end=_parse(config.get("WORKDAY_END"), "17:00"),
        break_start=_parse(config.get("BREAK_START"), "12:00"),
        break_end=_parse(config.get("BREAK_END"), "12:30"),
    )


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def worked_hours(start: time, end: time, wd: Workday | None = None) -> float:
    """Hours between ``start`` and ``end``, without the part that falls in the break."""
    wd = wd or workday()
    total = _minutes(end) - _minutes(start)
    overlap = min(_minutes(end), _minutes(wd.break_end)) - max(_minutes(start), _minutes(wd.break_start))
    return max(total - max(overlap, 0), 0) / 60


def default_times(hours: float, wd: Workday | None = None) -> tuple[time, time]:
    """Start and end of ``hours`` working hours from the start of the working day."""
    wd = wd or workday()
    if hours >= wd.hours:
        return wd.start, wd.end
    start = _minutes(wd.start)
    end = start + round(hours * 60)
    if end > _minutes(wd.break_start) and start < _minutes(wd.break_start):
        end += _minutes(wd.break_end) - _minutes(wd.break_start)   # skip the break
    end = min(end, 24 * 60 - 1)
    return wd.start, time(end // 60, end % 60)


def times_for(assignment, wd: Workday | None = None) -> tuple[time, time]:
    """Times of a planning entry on each planned day."""
    if assignment.start_time and assignment.end_time:
        return assignment.start_time, assignment.end_time
    return default_times(assignment.hours_per_day or 0, wd)


def fmt(t: time) -> str:
    return t.strftime("%H:%M")
