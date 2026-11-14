"""Outlook (Microsoft 365) calendar data, see services/outlook.py.

* :class:`CalendarEvent` - appointments read from an employee's Outlook
  calendar, shown in the team planning. It is a local copy, refreshed by the
  synchronisation, so the planning page never waits for Microsoft.
* :class:`OutlookLink` - the Outlook appointments this application wrote for
  a planning entry, so they can be updated or removed later and are not read
  back as "foreign" appointments.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import utcnow


class CalendarEvent(db.Model):
    """One appointment from an employee's Outlook calendar."""

    __tablename__ = "calendar_events"
    __table_args__ = (sa.UniqueConstraint("user_id", "external_id", name="uq_calendar_events_user_external"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    #: Outlook's id of the appointment.
    external_id: Mapped[str] = mapped_column(sa.String(255))
    subject: Mapped[str] = mapped_column(sa.String(300), default="")
    location: Mapped[str | None] = mapped_column(sa.String(200))
    #: Local time (the configured Outlook time zone); ``end`` is exclusive.
    start: Mapped[datetime] = mapped_column(sa.DateTime(), index=True)
    end: Mapped[datetime] = mapped_column(sa.DateTime(), index=True)
    is_all_day: Mapped[bool] = mapped_column(default=False)
    #: Outlook "show as": free / tentative / busy / oof (out of office) / workingElsewhere.
    show_as: Mapped[str] = mapped_column(sa.String(20), default="busy")
    is_private: Mapped[bool] = mapped_column(default=False)
    synced_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)

    user = relationship("User")

    @property
    def first_day(self) -> date:
        return self.start.date()

    @property
    def last_day(self) -> date:
        """Last day covered (``end`` is exclusive: an all-day event ends at midnight)."""
        last = self.end if self.end.time() != datetime.min.time() else self.end - timedelta(days=1)
        return max(last.date(), self.start.date())

    @property
    def is_long(self) -> bool:
        """All-day or longer than a day: drawn as a bar in the planning."""
        return self.is_all_day or (self.end - self.start).total_seconds() >= 24 * 3600

    @property
    def display_subject(self) -> str:
        return "Privé" if self.is_private else (self.subject or "(geen onderwerp)")


class OutlookLink(db.Model):
    """An Outlook appointment written by this application for a planning entry."""

    __tablename__ = "outlook_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: NULL once the planning entry was deleted: the appointment must then be
    #: removed from Outlook by the next synchronisation.
    assignment_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("assignments.id", ondelete="SET NULL"), index=True
    )
    #: Whose calendar the appointment is in (the entry may have moved to another person).
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str] = mapped_column(sa.String(255), index=True)
    start_date: Mapped[date]
    end_date: Mapped[date]

    user = relationship("User")
