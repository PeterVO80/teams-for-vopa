"""Log of login attempts, used to slow down password guessing."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .base import utcnow


class LoginAttempt(db.Model):
    """One login attempt (successful or not), see ``app/blueprints/auth.py``."""

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(sa.String(150), index=True)
    ip: Mapped[str] = mapped_column(sa.String(64), index=True)
    success: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow, index=True)
