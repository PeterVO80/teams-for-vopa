"""Knowledge modules: tagged notes and a hierarchical wiki.

Both store their content as **Markdown**. Markdown is plain text, so it is
easy to diff, search, back up and export, while the WYSIWYG editor in the
browser (Toast UI Editor) means nobody has to learn the syntax.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import TimestampMixin, utcnow

#: Association table for the many-to-many relation between notes and tags.
note_tags = sa.Table(
    "note_tags",
    db.metadata,
    sa.Column("note_id", sa.ForeignKey("notes.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("tag_id", sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(db.Model):
    """A label such as *Status meeting* or *Klant feedback*."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(60), unique=True)
    #: Bootstrap-compatible hex colour for the tag badge.
    color: Mapped[str] = mapped_column(sa.String(7), default="#6c757d")

    @classmethod
    def get_or_create(cls, name: str) -> "Tag":
        """Return the tag called ``name`` (case-insensitive), creating it if needed."""
        name = name.strip()
        tag = db.session.scalar(sa.select(cls).where(sa.func.lower(cls.name) == name.lower()))
        if tag is None:
            tag = cls(name=name)
            db.session.add(tag)
        return tag


class Note(TimestampMixin, db.Model):
    """A free-form note, e.g. the minutes of a meeting with the customer."""

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(sa.String(200))
    content: Mapped[str] = mapped_column(sa.Text, default="")
    project_id: Mapped[int | None] = mapped_column(sa.ForeignKey("projects.id"), index=True)
    author_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))
    pinned: Mapped[bool] = mapped_column(default=False)

    project = relationship("Project")
    author = relationship("User")
    tags: Mapped[list[Tag]] = relationship(secondary=note_tags, order_by=Tag.name)


class WikiPage(TimestampMixin, db.Model):
    """A wiki article. Pages can be nested via ``parent_id``.

    A page either belongs to the **general wiki** (``project_id`` is NULL:
    company-wide knowledge) or to the **wiki of one project** (``project_id``
    set: e.g. the I/O conventions or network plan of that installation).
    A page and all its sub pages always belong to the same wiki.
    """

    __tablename__ = "wiki_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(sa.String(200))
    #: URL-friendly unique identifier derived from the title.
    slug: Mapped[str] = mapped_column(sa.String(220), unique=True, index=True)
    content: Mapped[str] = mapped_column(sa.Text, default="")
    parent_id: Mapped[int | None] = mapped_column(sa.ForeignKey("wiki_pages.id"))
    #: NULL = general wiki, otherwise the project this page belongs to.
    project_id: Mapped[int | None] = mapped_column(sa.ForeignKey("projects.id"), index=True)
    updated_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))

    parent: Mapped["WikiPage | None"] = relationship(
        remote_side=[id], back_populates="children"
    )
    children: Mapped[list["WikiPage"]] = relationship(
        back_populates="parent", order_by="WikiPage.title"
    )
    updated_by = relationship("User")
    project = relationship("Project")
    revisions: Mapped[list["WikiRevision"]] = relationship(
        back_populates="page",
        cascade="all, delete-orphan",
        order_by="WikiRevision.created_at.desc()",
    )

    @property
    def breadcrumbs(self) -> list["WikiPage"]:
        """All ancestors from the root down to (excluding) this page."""
        trail, node = [], self.parent
        while node is not None and node not in trail:  # guard against cycles
            trail.insert(0, node)
            node = node.parent
        return trail

    def descendant_ids(self) -> set[int]:
        """Ids of all pages below this one (used to prevent cyclic parents)."""
        ids: set[int] = set()
        stack = list(self.children)
        while stack:
            page = stack.pop()
            if page.id not in ids:
                ids.add(page.id)
                stack.extend(page.children)
        return ids


class WikiRevision(db.Model):
    """Snapshot of a wiki page *before* each edit, giving a full history."""

    __tablename__ = "wiki_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(
        sa.ForeignKey("wiki_pages.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(sa.String(200))
    content: Mapped[str] = mapped_column(sa.Text)
    author_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow
    )

    page: Mapped[WikiPage] = relationship(back_populates="revisions")
    author = relationship("User")
