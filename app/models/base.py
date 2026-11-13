"""Shared building blocks for ORM models."""

import enum
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class LabeledEnum(str, enum.Enum):
    """String enum with a human readable (Dutch) label for the UI.

    Members are declared as ``NAME = ("value", "Label")``. The *value* is what
    is stored in the database, the *label* is what the user sees. Keeping both
    together guarantees every status always has a translation.
    """

    def __new__(cls, value: str, label: str):
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.label = label
        return obj

    def __str__(self) -> str:
        # Return the raw value (not "ClassName.MEMBER") so enums can be used
        # directly as form data, in URLs and in templates.
        return self.value

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        """Return ``(value, label)`` tuples, ready for a WTForms SelectField."""
        return [(member.value, member.label) for member in cls]


def enum_column(enum_cls: type[enum.Enum], **kwargs) -> sa.Enum:
    """Build a portable SQLAlchemy Enum type for ``enum_cls``.

    ``native_enum=False`` stores the value as a plain VARCHAR instead of a
    PostgreSQL ``ENUM`` type, so adding a new status later needs no database
    migration at all. SQLAlchemy still validates every value against the
    Python enum before it is written (``validate_strings=True``).
    """
    return sa.Enum(
        enum_cls,
        native_enum=False,
        length=30,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
        **kwargs,
    )


class TimestampMixin:
    """Adds ``created_at`` / ``updated_at`` columns maintained automatically."""

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
