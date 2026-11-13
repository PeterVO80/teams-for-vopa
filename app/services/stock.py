"""Stock management logic: stock levels and registering movements.

The stock of an article on a location is the sum of its movements (see
``models/stock.py``). All changes go through the ``register_*`` functions
below, which validate the input (e.g. you cannot issue more than is on the
location) and create the right movement(s). They raise :class:`StockError`
with a user-friendly (Dutch) message; the caller commits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from io import BytesIO

import sqlalchemy as sa
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from ..extensions import db
from ..models import Article, GoodsReceipt, MovementKind, StockLocation, StockMovement

ZERO = Decimal("0")


class StockError(ValueError):
    """A stock movement that is not allowed (message is shown to the user)."""


# ------------------------------------------------------------------ levels
def stock_on(article_id: int, location_id: int) -> Decimal:
    """Current stock of one article on one location."""
    total = db.session.scalar(
        sa.select(sa.func.sum(StockMovement.quantity)).where(
            StockMovement.article_id == article_id, StockMovement.location_id == location_id
        )
    )
    return total or ZERO


def levels(article_ids: list[int]) -> dict[int, dict[int, Decimal]]:
    """Stock per location for several articles: ``{article_id: {location_id: qty}}``.

    One grouped query, used for a whole page of the stock overview.
    """
    result: dict[int, dict[int, Decimal]] = {a: {} for a in article_ids}
    if not article_ids:
        return result
    rows = db.session.execute(
        sa.select(StockMovement.article_id, StockMovement.location_id, sa.func.sum(StockMovement.quantity))
        .where(StockMovement.article_id.in_(article_ids))
        .group_by(StockMovement.article_id, StockMovement.location_id)
    )
    for article_id, location_id, qty in rows:
        if qty:
            result[article_id][location_id] = qty
    return result


def _totals_subquery(location_id: int | None = None):
    """Total stock per article (optionally on one location) as a subquery."""
    query = sa.select(
        StockMovement.article_id.label("article_id"),
        sa.func.sum(StockMovement.quantity).label("qty"),
    ).group_by(StockMovement.article_id)
    if location_id:
        query = query.where(StockMovement.location_id == location_id)
    return query.subquery()


def overview_query(search: str = "", location_id: int | None = None, below_minimum: bool = False,
                   category: str = "") -> sa.Select:
    """Articles for the stock overview.

    An article is listed when it has (had) stock movements or a minimum stock,
    so the overview is not cluttered with the whole article database.
    """
    totals = _totals_subquery(location_id)
    query = (
        sa.select(Article)
        .outerjoin(totals, totals.c.article_id == Article.id)
        .where(sa.or_(totals.c.article_id.is_not(None), Article.min_stock.is_not(None)))
        .order_by(Article.article_number)
    )
    if location_id:
        query = query.where(totals.c.article_id.is_not(None))
    for word in search.split():
        pattern = f"%{word}%"
        query = query.where(sa.or_(
            Article.article_number.ilike(pattern), Article.description.ilike(pattern),
            Article.manufacturer_ref.ilike(pattern), Article.manufacturer.ilike(pattern),
        ))
    if category:
        query = query.where(Article.category == category)
    if below_minimum:
        # Compare the *total* stock with the minimum, even when a location is filtered.
        all_totals = _totals_subquery()
        query = query.outerjoin(all_totals, all_totals.c.article_id == Article.id).where(
            Article.min_stock.is_not(None), sa.func.coalesce(all_totals.c.qty, 0) < Article.min_stock
        )
    return query


def below_minimum_count() -> int:
    """Number of articles whose total stock is below their minimum (dashboard)."""
    return db.session.scalar(sa.select(sa.func.count()).select_from(overview_query(below_minimum=True).subquery()))


@dataclass
class StockRow:
    """One article in the stock overview."""

    article: Article
    per_location: dict[int, Decimal]

    @property
    def total(self) -> Decimal:
        return sum(self.per_location.values(), ZERO)

    @property
    def below_minimum(self) -> bool:
        return self.article.min_stock is not None and self.total < self.article.min_stock

    @property
    def shortage(self) -> Decimal:
        """How much is needed to get back to the minimum stock."""
        return max((self.article.min_stock or ZERO) - self.total, ZERO)

    @property
    def value(self) -> Decimal:
        return self.total * (self.article.unit_price or ZERO)


def rows_for(articles: list[Article]) -> list[StockRow]:
    per_article = levels([a.id for a in articles])
    return [StockRow(article=a, per_location=per_article[a.id]) for a in articles]


# --------------------------------------------------------------- movements
def _movement(article, location_id, kind, quantity, user_id, moved_on=None, project_id=None, note=None):
    movement = StockMovement(
        article_id=article.id, location_id=location_id, kind=kind, quantity=quantity,
        moved_on=moved_on or date.today(), project_id=project_id, user_id=user_id, note=note or None,
    )
    db.session.add(movement)
    return movement


def _check_positive(quantity: Decimal) -> None:
    if quantity is None or quantity <= 0:
        raise StockError("Het aantal moet groter zijn dan 0.")


def _check_available(article: Article, location_id: int, quantity: Decimal) -> None:
    available = stock_on(article.id, location_id)
    if quantity > available:
        location = db.session.get(StockLocation, location_id)
        raise StockError(
            f"Niet genoeg stock: er is maar {available.normalize():f} {article.unit} "
            f"van {article.article_number} in {location.name}."
        )


def register_receipt(article, location_id, quantity, user_id, moved_on=None, project_id=None, note=None):
    """Put material into stock (purchase, return from a project, ...)."""
    _check_positive(quantity)
    return _movement(article, location_id, MovementKind.RECEIPT, quantity, user_id, moved_on, project_id, note)


def register_issue(article, location_id, quantity, user_id, moved_on=None, project_id=None, note=None):
    """Take material out of stock, usually for a project."""
    _check_positive(quantity)
    _check_available(article, location_id, quantity)
    return _movement(article, location_id, MovementKind.ISSUE, -quantity, user_id, moved_on, project_id, note)


def register_transfer(article, from_location_id, to_location_id, quantity, user_id, moved_on=None, note=None):
    """Move material between two locations (two movements: out and in)."""
    _check_positive(quantity)
    if from_location_id == to_location_id:
        raise StockError("Kies een andere bestemming dan de huidige locatie.")
    _check_available(article, from_location_id, quantity)
    to_name = db.session.get(StockLocation, to_location_id).name
    from_name = db.session.get(StockLocation, from_location_id).name
    out = _movement(article, from_location_id, MovementKind.TRANSFER, -quantity, user_id, moved_on,
                    note=f"Naar {to_name}" + (f" - {note}" if note else ""))
    into = _movement(article, to_location_id, MovementKind.TRANSFER, quantity, user_id, moved_on,
                     note=f"Van {from_name}" + (f" - {note}" if note else ""))
    return out, into


def register_count(article, location_id, counted, user_id, moved_on=None, note=None):
    """Inventory count: record the difference between counted and expected stock.

    Returns ``None`` when the count matches the stock (nothing to record).
    """
    if counted is None or counted < 0:
        raise StockError("Het getelde aantal kan niet negatief zijn.")
    difference = counted - stock_on(article.id, location_id)
    if difference == 0:
        return None
    return _movement(article, location_id, MovementKind.COUNT, difference, user_id, moved_on,
                     note=note or f"Geteld: {counted.normalize():f}")


def receive_into_stock(receipt: GoodsReceipt) -> StockMovement | None:
    """Delivery of a purchase order -> stock, when the order has a stock location.

    Only lines linked to an article can go into stock. The movement is tied
    to the delivery, so undoing the delivery also undoes the stock movement.
    """
    line = receipt.line
    order = line.order
    if not (order.stock_location_id and line.article_id):
        return None
    movement = StockMovement(
        article_id=line.article_id, location_id=order.stock_location_id, kind=MovementKind.RECEIPT,
        quantity=receipt.quantity, moved_on=receipt.received_on, user_id=receipt.received_by_id,
        project_id=order.project_id, note=f"Levering {order.number}",
    )
    receipt.stock_movements.append(movement)
    return movement


# --------------------------------------------------------- project usage
@dataclass
class ProjectStockUsage:
    article: Article
    quantity: Decimal  # net quantity taken from stock (issues minus returns)


def project_usage(project_id: int) -> list[ProjectStockUsage]:
    """Material issued from stock to a project (net of returns to stock).

    Deliveries of the project's own orders that went into stock are not
    counted: they are already in the project's material overview.
    """
    rows = db.session.execute(
        sa.select(StockMovement.article_id, sa.func.sum(-StockMovement.quantity))
        .where(
            StockMovement.project_id == project_id,
            StockMovement.goods_receipt_id.is_(None),
            StockMovement.kind.in_([MovementKind.ISSUE, MovementKind.RECEIPT]),
        )
        .group_by(StockMovement.article_id)
    ).all()
    articles = {a.id: a for a in db.session.scalars(sa.select(Article).where(Article.id.in_([r[0] for r in rows])))}
    usage = [ProjectStockUsage(articles[a], q) for a, q in rows if q]
    return sorted(usage, key=lambda u: u.article.article_number)


# ------------------------------------------------------------------ export
def export_overview(rows: list[StockRow], locations: list[StockLocation]) -> BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = "Stock"
    headers = ["Artikelnummer", "Omschrijving", "Eenheid"] + [loc.name for loc in locations] + [
        "Totaal", "Minimum", "Tekort", "Eenheidsprijs", "Waarde"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E79")
    for row in rows:
        a = row.article
        ws.append([a.article_number, a.description, a.unit]
                  + [float(row.per_location.get(loc.id, 0)) for loc in locations]
                  + [float(row.total), float(a.min_stock) if a.min_stock is not None else None,
                     float(row.shortage) if row.below_minimum else None,
                     float(a.unit_price) if a.unit_price is not None else None, float(row.value)])
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 20, 45
    ws.freeze_panes = "A2"
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
