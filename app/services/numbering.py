"""Automatic, human readable document numbers.

Numbers have the form ``<PREFIX><SEPARATOR><YEAR><SEPARATOR><SEQUENCE>``,
for example ``PRJ/2026/0001`` for projects or ``PO-2026-0001`` for purchase
orders. The sequence restarts at 1 every calendar year.

How the next number is determined
---------------------------------
All existing numbers of the current year are read and the highest sequence is
incremented. Numbers that do not follow the pattern (e.g. codes typed by hand
before automatic numbering existed) are simply ignored.

Two users creating a record at the very same moment can compute the same
number. The database's UNIQUE constraint rejects the second insert;
:func:`insert_with_unique_number` catches that and retries with the next
number, so callers never see a duplicate-key error.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Callable

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import InstrumentedAttribute

from ..extensions import db

#: How many times to retry when a concurrent insert grabbed the same number.
MAX_ATTEMPTS = 5


def next_number(
    column: InstrumentedAttribute,
    prefix: str,
    separator: str = "/",
    digits: int = 4,
    year: int | None = None,
) -> str:
    """Return the next free number for ``column``, e.g. ``PRJ/2026/0007``.

    :param column: model column holding the numbers, e.g. ``Project.code``.
    :param prefix: fixed text at the start, e.g. ``"PRJ"``.
    :param separator: text between prefix, year and sequence.
    :param digits: minimum width of the sequence (zero padded). A sequence
        that outgrows it simply gets longer (``PRJ/2026/10000``).
    :param year: defaults to the current year.
    """
    year = year or date.today().year
    stem = f"{prefix}{separator}{year}{separator}"
    pattern = re.compile(rf"^{re.escape(stem)}(\d+)$")

    # Escape LIKE wildcards in case the prefix/separator contains % or _.
    like = stem.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
    existing = db.session.scalars(sa.select(column).where(column.like(like, escape="\\")))

    # Compare numerically, not as text: "10000" must beat "9999".
    highest = max(
        (int(m.group(1)) for value in existing if (m := pattern.match(value))),
        default=0,
    )
    return f"{stem}{highest + 1:0{digits}d}"


def insert_with_unique_number(obj, assign_number: Callable[[], None]) -> None:
    """Add ``obj`` to the session, assigning a number that is guaranteed unique.

    ``assign_number`` is called before every attempt and must set the number
    on ``obj`` (typically via :func:`next_number`). Each attempt runs in a
    SAVEPOINT, so a collision only rolls back that attempt and not the rest
    of the caller's transaction.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        assign_number()
        try:
            with db.session.begin_nested():
                db.session.add(obj)
                db.session.flush()
            return
        except IntegrityError:
            # Someone else just took this number: try again with a fresh one.
            if attempt == MAX_ATTEMPTS:
                raise
