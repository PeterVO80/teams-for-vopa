"""Quotes (offertes) for customers: material and labour, grouped in sections.

A quote is a list of lines in a fixed order (``position``):

* ``section`` - a title that starts a new section (e.g. "Schakelkast",
  "Programmatie"); the lines below it, up to the next section, form its
  subtotal;
* ``material`` - an article from the article database or a free-text item:
  quantity x sales price. The purchase price (``cost_price``) and the markup
  are kept for the margin; they are internal and never meant for the customer;
* ``labour`` - work hours: hours x hourly rate.

Every line can have an optional note (extra explanation for the customer).
A quote can be linked to a project, and a project can be created from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import LabeledEnum, TimestampMixin, enum_column

ZERO = Decimal("0")
CENT = Decimal("0.01")


class QuoteStatus(LabeledEnum):
    DRAFT = ("draft", "Concept")
    SENT = ("sent", "Verstuurd")
    ACCEPTED = ("accepted", "Aanvaard")
    REJECTED = ("rejected", "Afgewezen")


class QuoteLineKind(LabeledEnum):
    SECTION = ("section", "Sectie")
    MATERIAL = ("material", "Materiaal")
    LABOUR = ("labour", "Werkuren")


class PdfDetail(LabeledEnum):
    """How much of a section's lines the customer sees on the PDF."""

    FULL = ("full", "Volledige details (aantal, eenheidsprijs, totaal)")
    QUANTITIES = ("quantities", "Enkel aantallen (zonder prijzen)")
    TOTAL = ("total", "Enkel het subtotaal (lijnen niet tonen)")


#: Statuses in which a quote is locked: only the status can still change.
LOCKED_STATUSES = (QuoteStatus.SENT, QuoteStatus.ACCEPTED)


class ServiceType(db.Model):
    """A kind of service with its hourly rate (Instellingen), chosen on labour lines."""

    __tablename__ = "service_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(120), unique=True)
    #: Hourly rate excluding VAT (empty: typed on the quote line).
    hourly_rate: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))
    position: Mapped[int] = mapped_column(default=0)
    active: Mapped[bool] = mapped_column(default=True, server_default=sa.true())

    @classmethod
    def choices(cls) -> list["ServiceType"]:
        return list(db.session.scalars(sa.select(cls).where(cls.active.is_(True)).order_by(cls.position, cls.name)))


class Quote(TimestampMixin, db.Model):
    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: e.g. OFF-2026-0001 (assigned automatically when the quote is created).
    number: Mapped[str] = mapped_column(sa.String(40), unique=True, index=True)
    title: Mapped[str] = mapped_column(sa.String(200))
    customer_id: Mapped[int | None] = mapped_column(sa.ForeignKey("companies.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(sa.ForeignKey("projects.id"), index=True)
    status: Mapped[QuoteStatus] = mapped_column(enum_column(QuoteStatus), default=QuoteStatus.DRAFT)
    quote_date: Mapped[date | None]
    valid_until: Mapped[date | None]
    #: Text for the customer above the lines (scope, conditions ...).
    introduction: Mapped[str | None] = mapped_column(sa.Text)
    #: Internal notes, not for the customer.
    notes: Mapped[str | None] = mapped_column(sa.Text)
    vat_rate: Mapped[Decimal] = mapped_column(sa.Numeric(5, 2), default=Decimal("21"), server_default="21")
    created_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))

    customer = relationship("Company")
    project = relationship("Project")
    created_by = relationship("User")
    lines: Mapped[list["QuoteLine"]] = relationship(
        back_populates="quote", cascade="all, delete-orphan", order_by="(QuoteLine.position, QuoteLine.id)")

    @property
    def locked(self) -> bool:
        """Sent or accepted: lines and details can't be changed any more (only the status)."""
        return self.status in LOCKED_STATUSES

    @property
    def priced_lines(self) -> list["QuoteLine"]:
        return [line for line in self.lines if line.kind != QuoteLineKind.SECTION]

    @property
    def material_total(self) -> Decimal:
        return sum((line.total for line in self.lines if line.kind == QuoteLineKind.MATERIAL), ZERO)

    @property
    def labour_total(self) -> Decimal:
        return sum((line.total for line in self.lines if line.kind == QuoteLineKind.LABOUR), ZERO)

    @property
    def labour_hours(self) -> Decimal:
        return sum((line.quantity or ZERO for line in self.lines if line.kind == QuoteLineKind.LABOUR), ZERO)

    @property
    def total(self) -> Decimal:
        """Total excluding VAT."""
        return self.material_total + self.labour_total

    @property
    def vat_amount(self) -> Decimal:
        return (self.total * (self.vat_rate or ZERO) / 100).quantize(CENT, rounding=ROUND_HALF_UP)

    @property
    def total_incl_vat(self) -> Decimal:
        return self.total + self.vat_amount

    @property
    def material_cost(self) -> Decimal | None:
        """Purchase price of the material (None when unknown for every line)."""
        costs = [line.cost_total for line in self.lines if line.kind == QuoteLineKind.MATERIAL]
        known = [c for c in costs if c is not None]
        return sum(known, ZERO) if known else None

    @property
    def material_margin(self) -> Decimal | None:
        """Sales minus purchase price of the material lines whose purchase price is known."""
        lines = [line for line in self.lines if line.kind == QuoteLineKind.MATERIAL and line.cost_total is not None]
        if not lines:
            return None
        return sum((line.total - line.cost_total for line in lines), ZERO)

    def sections(self) -> list["Section"]:
        """Lines grouped per section; lines before the first title form a section without title."""
        result: list[Section] = []
        current = Section(None)
        for line in self.lines:
            if line.kind == QuoteLineKind.SECTION:
                if current.title is not None or current.lines:
                    result.append(current)
                current = Section(line)
            else:
                current.lines.append(line)
        if current.title is not None or current.lines:
            result.append(current)
        return result

    def renumber(self) -> None:
        """Positions 10, 20, 30 ... in the current order."""
        for index, line in enumerate(sorted(self.lines, key=lambda l: (l.position, l.id or 0)), start=1):
            line.position = index * 10

    @property
    def next_position(self) -> int:
        return max((line.position for line in self.lines), default=0) + 10


@dataclass
class Section:
    title: "QuoteLine | None"
    lines: list["QuoteLine"] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((line.total for line in self.lines), ZERO)


class QuoteLine(db.Model):
    __tablename__ = "quote_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(sa.ForeignKey("quotes.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(default=0)
    kind: Mapped[QuoteLineKind] = mapped_column(enum_column(QuoteLineKind), default=QuoteLineKind.MATERIAL)
    article_id: Mapped[int | None] = mapped_column(sa.ForeignKey("articles.id"))
    #: Labour: the kind of service (its name and hourly rate are suggested).
    service_type_id: Mapped[int | None] = mapped_column(sa.ForeignKey("service_types.id"))
    #: The text of the line; for a section: its title.
    description: Mapped[str] = mapped_column(sa.String(300))
    quantity: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 2))
    unit: Mapped[str | None] = mapped_column(sa.String(20))
    #: Sales price per unit (hourly rate for labour), excluding VAT.
    unit_price: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 4))
    #: Purchase price per unit (material; internal).
    cost_price: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 4))
    #: Optional note / comment shown under the line.
    note: Mapped[str | None] = mapped_column(sa.Text)
    #: Section titles: how the section's lines appear on the PDF.
    pdf_detail: Mapped[PdfDetail] = mapped_column(enum_column(PdfDetail), default=PdfDetail.FULL,
                                                  server_default=PdfDetail.FULL.value)

    quote: Mapped[Quote] = relationship(back_populates="lines")
    article = relationship("Article")
    service_type = relationship("ServiceType")

    @property
    def total(self) -> Decimal:
        if self.kind == QuoteLineKind.SECTION or self.quantity is None or self.unit_price is None:
            return ZERO
        return (self.quantity * self.unit_price).quantize(CENT, rounding=ROUND_HALF_UP)

    @property
    def cost_total(self) -> Decimal | None:
        if self.kind != QuoteLineKind.MATERIAL or self.quantity is None or self.cost_price is None:
            return None
        return (self.quantity * self.cost_price).quantize(CENT, rounding=ROUND_HALF_UP)

    @property
    def markup(self) -> Decimal | None:
        """Markup on the purchase price in %, e.g. 25 (None when unknown)."""
        if not self.cost_price or self.unit_price is None:
            return None
        return ((self.unit_price / self.cost_price - 1) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
