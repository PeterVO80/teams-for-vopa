"""Application settings that administrators change in the app itself."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


class AppSetting(db.Model):
    """A key/value setting, e.g. ``outlook.category_color`` = ``preset1``.

    Use :meth:`get` / :meth:`put`; a missing row means "use the default"
    (usually the environment variable).
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(sa.String(100), primary_key=True)
    value: Mapped[str] = mapped_column(sa.String(500), default="")

    @staticmethod
    def get(key: str, default: str | None = None) -> str | None:
        row = db.session.get(AppSetting, key)
        return row.value if row is not None else default

    @staticmethod
    def put(key: str, value: str) -> None:
        row = db.session.get(AppSetting, key)
        if row is None:
            db.session.add(AppSetting(key=key, value=value))
        else:
            row.value = value
