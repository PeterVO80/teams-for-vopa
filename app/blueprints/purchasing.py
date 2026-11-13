"""Purchasing: purchase orders, order lines and delivery follow-up."""

from datetime import date
from io import BytesIO

import sqlalchemy as sa
from flask import flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from openpyxl import Workbook

from ..extensions import db
from ..forms import OrderLineForm, PurchaseOrderForm, ReceiptForm, none_if_zero
from ..models import (
    Article,
    GoodsReceipt,
    OrderStatus,
    Project,
    PurchaseOrder,
    PurchaseOrderLine,
)
from ..utils import int_arg, next_url
from . import protected_blueprint

bp = protected_blueprint("purchasing", __name__)


def _next_order_number() -> str:
    """Suggest the next order number in the form ``PO-<year>-<0001>``."""
    prefix = f"PO-{date.today():%Y}-"
    last = db.session.scalar(
        sa.select(PurchaseOrder.number)
        .where(PurchaseOrder.number.like(f"{prefix}%"))
        .order_by(PurchaseOrder.number.desc())
        .limit(1)
    )
    try:
        seq = int(last.removeprefix(prefix)) + 1 if last else 1
    except ValueError:
        seq = 1
    return f"{prefix}{seq:04d}"


@bp.route("/")
def index():
    query = sa.select(PurchaseOrder).order_by(PurchaseOrder.id.desc())
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
        .where(PurchaseOrder.status.in_([OrderStatus.ORDERED, OrderStatus.PARTIAL]))
        .order_by(PurchaseOrder.expected_date.is_(None), PurchaseOrder.expected_date, PurchaseOrder.number)
    ).all()
    outstanding = [line for line in lines if not line.is_complete]
    return render_template("purchasing/deliveries.html", lines=outstanding)


@bp.route("/<int:order_id>")
def detail(order_id: int):
    order = db.get_or_404(PurchaseOrder, order_id)
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
    form = PurchaseOrderForm(obj=order)
    form.project_id.choices = Project.open_choices()
    if not form.is_submitted():
        if order:
            form.project_id.data = order.project_id or 0
        else:
            form.number.data = _next_order_number()
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
    order = line.order
    if line.receipts:
        flash("Lijn heeft al leveringen en kan niet verwijderd worden.", "danger")
    else:
        order.lines.remove(line)
        order.refresh_status()
        db.session.commit()
        flash("Lijn verwijderd.", "success")
    return redirect(url_for("purchasing.detail", order_id=order.id))


# ---------------------------------------------------------------- receipts
@bp.route("/lines/<int:line_id>/receive", methods=["POST"])
def receive(line_id: int):
    """Register a (partial) delivery for an order line."""
    line = db.get_or_404(PurchaseOrderLine, line_id)
    form = ReceiptForm()
    if form.validate_on_submit():
        line.receipts.append(
            GoodsReceipt(
                quantity=form.quantity.data,
                received_on=form.received_on.data,
                note=form.note.data or None,
                received_by_id=current_user.id,
            )
        )
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
    for line in order.lines:
        if line.quantity_outstanding > 0:
            line.receipts.append(
                GoodsReceipt(
                    quantity=line.quantity_outstanding,
                    received_on=date.today(),
                    note="Volledig ontvangen",
                    received_by_id=current_user.id,
                )
            )
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
    wb = Workbook()
    ws = wb.active
    ws.title = order.number
    ws.append(["Bestelling", order.number])
    ws.append(["Leverancier", order.supplier])
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
