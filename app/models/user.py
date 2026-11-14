"""Users of the application (= the team members)."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from flask_login import UserMixin
from sqlalchemy.orm import Mapped, mapped_column
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from .base import LabeledEnum, TimestampMixin, enum_column


class UserRole(LabeledEnum):
    """Authorisation level. Admins can additionally manage user accounts."""

    ADMIN = ("admin", "Beheerder")
    MEMBER = ("member", "Medewerker")


class User(UserMixin, TimestampMixin, db.Model):
    """A person who can log in and to whom tasks and planning can be assigned."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(sa.String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(sa.String(120))
    email: Mapped[str | None] = mapped_column(sa.String(120))
    password_hash: Mapped[str] = mapped_column(sa.String(256))
    role: Mapped[UserRole] = mapped_column(
        enum_column(UserRole), default=UserRole.MEMBER
    )
    #: Inactive users cannot log in and are hidden from selection lists, but
    #: their history (tasks, notes, ...) is preserved.
    active: Mapped[bool] = mapped_column(default=True)
    #: Colour used for this person in the team planning (CSS hex colour).
    color: Mapped[str] = mapped_column(sa.String(7), default="#0d6efd")

    # --- Outlook (Microsoft 365) calendar link, see services/outlook.py ------
    #: Synchronise this person's Outlook calendar (mailbox = ``email``).
    outlook_sync: Mapped[bool] = mapped_column(default=True, server_default=sa.true())
    outlook_synced_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: Last synchronisation problem for this person (None = OK).
    outlook_error: Mapped[str | None] = mapped_column(sa.String(300))
    #: Result of setting the colour of the "VOPA Teams" category in Outlook.
    outlook_category: Mapped[str | None] = mapped_column(sa.String(300))

    # ------------------------------------------------------------------ auth
    def set_password(self, password: str) -> None:
        """Hash and store ``password`` (the plain text is never stored)."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """Return ``True`` when ``password`` matches the stored hash."""
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self) -> bool:  # used by Flask-Login
        return self.active

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    @property
    def initials(self) -> str:
        """Up to two initials, used for compact avatars on the kanban board."""
        parts = self.full_name.split()
        return "".join(p[0] for p in parts[:2]).upper() or self.username[:2].upper()

    @classmethod
    def active_choices(cls) -> list[tuple[int, str]]:
        """``(id, name)`` tuples of active users for select fields."""
        users = db.session.scalars(
            sa.select(cls).where(cls.active.is_(True)).order_by(cls.full_name)
        )
        return [(u.id, u.full_name) for u in users]

    def __repr__(self) -> str:
        return f"<User {self.username}>"
