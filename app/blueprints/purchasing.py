"""Purchasing: purchase orders, order lines and delivery follow-up."""

from datetime import date
from decimal import Decimal
from io import BytesIO

import sqlalchemy as sa
from flask import flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from openpyxl import Workbook

from ..access import project_scope, require_project
from ..extensions import db
from ..forms import AddArticleToOrderForm, OrderLineForm, PurchaseOrderForm, ReceiptForm, none_if_zero
from ..models import (
    Article,
    Company,
    GoodsReceipt,
    OrderStatus,
    Project,
    PurchaseOrder,
    PurchaseOrderLine,
    StockLocation,
)
from ..services.material import export_material, project_material
from ..services.numbering import next_number
from ..services.stock import receive_into_stock
from ..utils import int_arg, is_modal_request, next_url
from . import protected_blueprint
from .relations import add_button as add_relation_button

bp = protected_blueprint("purchasing", __name__)


def _next_order_number() -> str:
    """Suggest the next order number, e.g. ``PO-2026-0001``.

    Unlike project codes, order numbers stay editable (some customers or
    suppliers impose their own reference), so this is only a suggestion.
    """
    return next_number(PurchaseOrder.number, "PO", separator="-")


@bp.route("/")
def index():
    query = sa.select(PurchaseOrder).where(project_scope(PurchaseOrder.project_id)).order_by(PurchaseOrder.id.desc())
    status = request.args.get("status", "open")
    if status == "open":
        query = query.where(PurchaseOrder.status.in_([OrderStatus.DRAFT, OrderStatus.ORDERED, OrderStatus.PARTIAL]))
    elif status in OrderStatus._value2member_map_:
        query = query.where(PurchaseOrder.status == OrderStatus(status))
    project_id = int_arg("project")
    if project_id:
        query = query.where(PurchaseOrder.project_id == project_id)
    orders = db.session.scalars(query).all()
    return render_template(
        "purchasing/index.html",
        orders=orders,
        status=status,
        project_id=project_id,
        projects=Project.open_choices(include_empty=False),
    )


@bp.route("/deliveries")
def deliveries():
    """All order lines still waiting for (part of) their delivery."""
    lines = db.session.scalars(
        sa.select(PurchaseOrderLine)
        .join(PurchaseOrder)
        .where(PurchaseOrder.status.in_([OrderStatus.ORDERED, OrderStatus.PARTIAL]),
               project_scope(PurchaseOrder.project_id))
        .order_by(PurchaseOrder.expected_date.is_(None), PurchaseOrder.expected_date, PurchaseOrder.number)
    ).all()
    outstanding = [line for line in lines if not line.is_complete]
    return render_template("purchasing/deliveries.html", lines=outstanding)


@bp.route("/<int:order_id>")
def detail(order_id: int):
    order = db.get_or_404(PurchaseOrder, order_id)
    require_project(order.project)
    return render_template(
        "purchasing/detail.html",
        order=order,
        line_form=OrderLineForm(),
        receipt_form=ReceiptForm(received_on=date.today()),
    )


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:order_id>/edit", methods=["GET", "POST"])
def edit(order_id: int | None = None):
    order = db.get_or_404(PurchaseOrder, order_id) if order_id else None
    require_project(order.project if order else None)
    form = PurchaseOrderForm(obj=order)
    form.project_id.choices = Project.open_choices()
    form.stock_location_id.choices = StockLocation.choices(include_empty="- niet op stock (projectmateriaal) -")
    form.supplier_id.choices = Company.choices("supplier", order.supplier_id if order else None, empty="- kies een leverancier -")
    add_relation_button(form.supplier_id, "supplier")
    if not form.is_submitted():
        if order:
            form.project_id.data = order.project_id or 0
            form.stock_location_id.data = order.stock_location_id or 0
        else:
            form.number.data = _next_order_number()
            form.supplier_id.data = int_arg("supplier") or 0
            form.project_id.data = int_arg("project") or 0

    if form.validate_on_submit():
        duplicate = db.session.scalar(
            sa.select(PurchaseOrder).where(
                PurchaseOrder.number == form.number.data, PurchaseOrder.id != (order_id or 0)
            )
        )
        if duplicate:
            form.number.errors.append("Dit bestelnummer bestaat al.")
        else:
            if order is None:
                order = PurchaseOrder(created_by_id=current_user.id)
                db.session.add(order)
            form.populate_obj(order)
            order.project_id = none_if_zero(form.project_id.data)
            order.stock_location_id = none_if_zero(form.stock_location_id.data)
            order.status = OrderStatus(form.status.data)
            if order.status == OrderStatus.ORDERED and not order.order_date:
                order.order_date = date.today()
            order.refresh_status()
            db.session.commit()
            flash(f"Bestelling {order.number} opgeslagen.", "success")
            return redirect(url_for("purchasing.detail", order_id=order.id))

    return render_template(
        "form_page.html",
        form=form,
        title="Bestelling bewerken" if order else "Nieuwe bestelling",
        cancel_url=url_for("purchasing.detail", order_id=order.id) if order else url_for("purchasing.index"),
    )


@bp.route("/<int:order_id>/status/<status>", methods=["POST"])
def set_status(order_id: int, status: str):
    """Quick action buttons: mark as ordered, cancel, reopen, ..."""
    order = db.get_or_404(PurchaseOrder, order_id)
    require_project(order.project)
    order.status = OrderStatus(status)
    if order.status == OrderStatus.ORDERED and not order.order_date:
        order.order_date = date.today()
    order.refresh_status()
    db.session.commit()
    flash(f"Status gewijzigd naar '{order.status.label}'.", "success")
    return redirect(url_for("purchasing.detail", order_id=order.id))


@bp.route("/<int:order_id>/delete", methods=["POST"])
def delete(order_id: int):
    order = db.get_or_404(PurchaseOrder, order_id)
    require_project(order.project)
    if any(line.receipts for line in order.lines):
        flash("Deze bestelling heeft al leveringen; annuleer ze in plaats van te verwijderen.", "danger")
        return redirect(url_for("purchasing.detail", order_id=order.id))
    db.session.delete(order)
    db.session.commit()
    flash("Bestelling verwijderd.", "success")
    return redirect(url_for("purchasing.index"))


# ------------------------------------------------------------- order lines
@bp.route("/<int:order_id>/lines", methods=["POST"])
def add_line(order_id: int):
    order = db.get_or_404(PurchaseOrder, order_id)
    require_project(order.project)
    form = OrderLineForm()
    if form.validate_on_submit():
        article = db.session.get(Article, int(form.article_id.data)) if form.article_id.data else None
        order.lines.append(
            PurchaseOrderLine(
                article=article,
                description=form.description.data,
                quantity=form.quantity.data,
                unit=form.unit.data,
                unit_price=form.unit_price.data,
            )
        )
        order.refresh_status()
        db.session.commit()
        flash("Lijn toegevoegd.", "success")
    else:
        flash("Lijn niet toegevoegd: controleer omschrijving en aantal.", "danger")
    return redirect(url_for("purchasing.detail", order_id=order.id))


@bp.route("/lines/<int:line_id>/delete", methods=["POST"])
def delete_line(line_id: int):
    line = db.get_or_404(PurchaseOrderLine, line_id)
    require_project(line.order.project)
    order = line.order
    if line.receipts:
        flash("Lijn heeft al leveringen en kan niet verwijderd worden.", "danger")
    else:
        order.lines.remove(line)
        order.refresh_status()
        db.session.commit()
        flash("Lijn verwijderd.", "success")
    return redirect(url_for("purchasing.detail", order_id=order.id))


@bp.route("/lines/<int:line_id>/edit", methods=["GET", "POST"])
def edit_line(line_id: int):
    """Edit an order line in a popup, e.g. to link a free-text line to an article."""
    line = db.get_or_404(PurchaseOrderLine, line_id)
    require_project(line.order.project)
    # The prefix keeps the field ids unique next to the "add line" form.
    form = OrderLineForm(obj=line, prefix="edit")
    if not form.is_submitted():
        form.article_id.data = line.article_id or ""

    if form.validate_on_submit():
        article = db.session.get(Article, int(form.article_id.data)) if form.article_id.data else None
        line.article = article
        line.description = form.description.data
        line.quantity = form.quantity.data
        line.unit = form.unit.data
        line.unit_price = form.unit_price.data
        line.order.refresh_status()
        db.session.commit()
        flash("Lijn bijgewerkt.", "success")
        if is_modal_request():
            return jsonify(ok=True)
        return redirect(url_for("purchasing.detail", order_id=line.order_id))

    status = 422 if form.errors else 200
    return render_template("purchasing/_line_modal.html", form=form, line=line), status


def _open_order_choices(project_id: int | None = None) -> list[tuple[int, str]]:
    """Orders that can still receive lines (draft, ordered, partially delivered)."""
    query = sa.select(PurchaseOrder).where(
        PurchaseOrder.status.in_([OrderStatus.DRAFT, OrderStatus.ORDERED, OrderStatus.PARTIAL]),
        project_scope(PurchaseOrder.project_id),
    )
    if project_id:
        query = query.where(PurchaseOrder.project_id == project_id)
    orders = db.session.scalars(query.order_by(PurchaseOrder.id.desc()))
    return [
        (o.id, f"{o.number} · {o.supplier} · {o.project.code if o.project else 'geen project'} ({o.status.label})")
        for o in orders
    ]


@bp.route("/add-article/<int:article_id>", methods=["GET", "POST"])
def add_article(article_id: int):
    """Popup: put an article from the article database on an open order.

    When the order already has a line for this article (same unit), its
    quantity is increased instead of adding a duplicate line.
    """
    article = db.get_or_404(Article, article_id)
    form = AddArticleToOrderForm()
    form.order_id.choices = [(0, "- kies een bestelling -")] + _open_order_choices()
    if not form.is_submitted():
        form.unit_price.data = article.unit_price
        form.order_id.data = int_arg("order") or 0
        if request.args.get("qty"):
            form.quantity.data = request.args.get("qty", type=Decimal)

    if form.validate_on_submit():
        order = db.session.get(PurchaseOrder, form.order_id.data)
        require_project(order.project)
        existing = next((l for l in order.lines if l.article_id == article.id and l.unit == article.unit), None)
        if existing:
            existing.quantity += form.quantity.data
            message = f"Aantal van {article.article_number} op {order.number} verhoogd naar {existing.quantity.normalize():f}."
        else:
            order.lines.append(PurchaseOrderLine(
                article=article,
                description=article.description,
                quantity=form.quantity.data,
                unit=article.unit,
                unit_price=form.unit_price.data,
            ))
            message = f"{article.article_number} toegevoegd aan {order.number}."
        order.refresh_status()
        db.session.commit()
        flash(message, "success")
        if is_modal_request():
            return jsonify(ok=True)
        return redirect(url_for("purchasing.detail", order_id=order.id))

    status = 422 if form.errors else 200
    return render_template("purchasing/_add_article_modal.html", form=form, article=article), status


# ---------------------------------------------------------------- material
@bp.route("/material")
def material():
    """Material overview per project: ordered vs. delivered, per article."""
    project_id = int_arg("project")
    project = db.session.get(Project, project_id) if project_id else None
    require_project(project)
    overview = project_material(project.id, only_open=bool(request.args.get("open"))) if project else None
    return render_template(
        "purchasing/material.html",
        project=project,
        overview=overview,
        projects=Project.open_choices(include_empty=False),
    )


@bp.route("/material/<int:project_id>.xlsx")
def material_export(project_id: int):
    project = db.get_or_404(Project, project_id)
    require_project(project)
    overview = project_material(project.id, only_open=bool(request.args.get("open")))
    return send_file(
        export_material(project, overview),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"materiaal_{project.code.replace('/', '-')}_{date.today():%Y%m%d}.xlsx",
    )


# ---------------------------------------------------------------- receipts
@bp.route("/lines/<int:line_id>/receive", methods=["POST"])
def receive(line_id: int):
    """Register a (partial) delivery for an order line."""
    line = db.get_or_404(PurchaseOrderLine, line_id)
    require_project(line.order.project)
    form = ReceiptForm()
    if form.validate_on_submit():
        receipt = GoodsReceipt(
            quantity=form.quantity.data,
            received_on=form.received_on.data,
            note=form.note.data or None,
            received_by_id=current_user.id,
        )
        line.receipts.append(receipt)
        receive_into_stock(receipt)  # only for orders with a stock location
        if line.order.status == OrderStatus.DRAFT:  # receiving implies it was ordered
            line.order.status = OrderStatus.ORDERED
        line.order.refresh_status()
        db.session.commit()
        if line.quantity_received > line.quantity:
            flash("Opgelet: er werd meer ontvangen dan besteld.", "warning")
        else:
            flash("Levering geregistreerd.", "success")
    else:
        flash("Levering niet geregistreerd: ongeldig aantal of datum.", "danger")
    return redirect(next_url(url_for("purchasing.detail", order_id=line.order_id)))


@bp.route("/<int:order_id>/receive-all", methods=["POST"])
def receive_all(order_id: int):
    """Shortcut: mark every outstanding quantity on the order as received today."""
    order = db.get_or_404(PurchaseOrder, order_id)
    require_project(order.project)
    for line in order.lines:
        if line.quantity_outstanding > 0:
            receipt = GoodsReceipt(
                quantity=line.quantity_outstanding,
                received_on=date.today(),
                note="Volledig ontvangen",
                received_by_id=current_user.id,
            )
            line.receipts.append(receipt)
            receive_into_stock(receipt)
    if order.status == OrderStatus.DRAFT:
        order.status = OrderStatus.ORDERED
    order.refresh_status()
    db.session.commit()
    flash("Alle lijnen als geleverd gemarkeerd.", "success")
    return redirect(url_for("purchasing.detail", order_id=order.id))


@bp.route("/receipts/<int:receipt_id>/delete", methods=["POST"])
def delete_receipt(receipt_id: int):
    """Undo a wrongly registered delivery."""
    receipt = db.get_or_404(GoodsReceipt, receipt_id)
    require_project(receipt.line.order.project)
    order = receipt.line.order
    receipt.line.receipts.remove(receipt)
    order.refresh_status()
    db.session.commit()
    flash("Levering verwijderd.", "success")
    return redirect(url_for("purchasing.detail", order_id=order.id))


@bp.route("/<int:order_id>/export")
def export(order_id: int):
    """Export a single order to Excel, e.g. to mail it to the supplier."""
    order = db.get_or_404(PurchaseOrder, order_id)
    require_project(order.project)
    wb = Workbook()
    ws = wb.active
    ws.title = order.number
    ws.append(["Bestelling", order.number])
    ws.append(["Leverancier", order.supplier.name])
    ws.append(["Project", order.project.display_name if order.project else ""])
    ws.append(["Besteldatum", order.order_date])
    ws.append([])
    ws.append(["Artikelnummer", "Bestelcode", "Omschrijving", "Aantal", "Eenheid",
               "Eenheidsprijs", "Totaal", "Ontvangen"])
    for line in order.lines:
        ws.append([
            line.article.article_number if line.article else "",
            line.article.manufacturer_ref if line.article else "",
            line.description,
            float(line.quantity),
            line.unit,
            float(line.unit_price) if line.unit_price is not None else None,
            float(line.line_total),
            float(line.quantity_received),
        ])
    for col, width in zip("ABCDEFGH", (16, 22, 45, 9, 9, 13, 12, 11)):
        ws.column_dimensions[col].width = width
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"{order.number}.xlsx",
    )
