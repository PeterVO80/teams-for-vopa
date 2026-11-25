"""Relations: manage customers and suppliers.

Other modules pick a relation from a list instead of typing a name (see
models/company.py). From those forms a new relation can be created in a
popup: ``/relations/new?kind=customer&select=customer_id`` answers with the
new option, which static/js/modal_form.js adds to the select and selects.

Contact persons belong to a relation: ``/relations/contacts`` lists them all,
the relation page shows its own and adds / edits them in a popup.
"""

import sqlalchemy as sa
from flask import abort, current_app, flash, jsonify, redirect, render_template, request, url_for

from ..access import project_scope, visible_projects_condition
from ..extensions import db
from ..forms import CompanyForm, ContactForm
from ..models import Article, Company, Contact, Project, PurchaseOrder, SupplierCatalog
from ..services import distance
from ..utils import int_arg, is_modal_request, next_url
from . import protected_blueprint

bp = protected_blueprint("relations", __name__)

KINDS = {"customer": "Klanten", "supplier": "Leveranciers"}


def _usage(company_ids: list[int]) -> dict[int, dict[str, int]]:
    """Number of projects, orders and articles per relation (grouped queries)."""
    usage = {cid: {"projects": 0, "orders": 0, "articles": 0} for cid in company_ids}
    if not company_ids:
        return usage
    for key, column in (("projects", Project.customer_id), ("orders", PurchaseOrder.supplier_id),
                        ("articles", Article.supplier_id)):
        for cid, count in db.session.execute(
            sa.select(column, sa.func.count()).where(column.in_(company_ids)).group_by(column)
        ):
            usage[cid][key] = count
    return usage


@bp.route("/")
def index():
    kind = request.args.get("kind", "")
    search = request.args.get("q", "").strip()
    query = sa.select(Company).order_by(Company.name)
    if kind == "customer":
        query = query.where(Company.is_customer.is_(True))
    elif kind == "supplier":
        query = query.where(Company.is_supplier.is_(True))
    if not request.args.get("inactive"):
        query = query.where(Company.active.is_(True))
    for word in search.split():
        pattern = f"%{word}%"
        query = query.where(sa.or_(Company.name.ilike(pattern), Company.city.ilike(pattern),
                                   Company.contact_person.ilike(pattern), Company.vat_number.ilike(pattern)))
    page = db.paginate(query, per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False)
    return render_template("relations/index.html", page=page, kind=kind, search=search, kinds=KINDS,
                           usage=_usage([c.id for c in page.items]))


@bp.route("/<int:company_id>")
def detail(company_id: int):
    company = db.get_or_404(Company, company_id)
    visible = visible_projects_condition()
    projects_query = sa.select(Project).filter_by(customer_id=company.id).order_by(Project.code.desc())
    if visible is not None:
        projects_query = projects_query.where(visible)
    projects = db.session.scalars(projects_query).all()
    orders = db.session.scalars(
        sa.select(PurchaseOrder).filter_by(supplier_id=company.id).where(project_scope(PurchaseOrder.project_id))
        .order_by(PurchaseOrder.id.desc()).limit(25)
    ).all()
    article_count = db.session.scalar(sa.select(sa.func.count(Article.id)).filter_by(supplier_id=company.id))
    catalogs = db.session.scalars(sa.select(SupplierCatalog).filter_by(supplier_id=company.id)).all()
    return render_template("relations/detail.html", company=company, projects=projects, orders=orders,
                           article_count=article_count, catalogs=catalogs)


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:company_id>/edit", methods=["GET", "POST"])
def edit(company_id: int | None = None):
    """Create or edit a relation, as a page or as a popup.

    Popup from another form: ``?kind=customer|supplier`` pre-ticks the role and
    ``?select=<field name>`` makes the answer carry the new option for that select.
    """
    company = db.get_or_404(Company, company_id) if company_id else None
    form = CompanyForm(obj=company)
    kind = request.args.get("kind")
    select_field = request.args.get("select")
    if company is None and not form.is_submitted():
        form.is_customer.data = kind == "customer"
        form.is_supplier.data = kind == "supplier"

    if form.validate_on_submit():
        existing = Company.by_name(form.name.data)
        if existing and existing.id != (company.id if company else None):
            form.name.errors.append(f"Er bestaat al een relatie '{existing.name}'.")
        else:
            if company is None:
                company = Company()
                db.session.add(company)
            old_address = _address(company)
            old_distance, was_auto = company.distance_km, company.distance_auto
            form.populate_obj(company)
            company.name = " ".join(form.name.data.split())  # no double / surrounding spaces
            _update_distance(company, old_address, old_distance, was_auto)
            db.session.commit()
            if is_modal_request():
                if select_field:
                    # Add the new relation to the select of the form underneath.
                    return jsonify(ok=True, option={"select": select_field, "value": company.id, "label": company.name})
                flash(f"Relatie {company.name} opgeslagen.", "success")
                return jsonify(ok=True)
            flash(f"Relatie {company.name} opgeslagen.", "success")
            return redirect(url_for("relations.detail", company_id=company.id))

    if is_modal_request():
        status = 422 if form.errors else 200
        return render_template("relations/_form_modal.html", form=form, company=company,
                               action=request.full_path), status
    return render_template("relations/edit.html", form=form, company=company)


def _address(company: Company) -> str:
    return distance.address_text(company.street, company.postal_code, company.city, company.country)


def _update_distance(company: Company, old_address: str, old_distance, was_auto: bool) -> None:
    """Keep ``distance_km`` up to date (services/distance.py).

    A value typed by the user is kept (``distance_auto`` False). The distance
    is (re)calculated when it is empty, or when it was calculated and the
    address changed. A failing calculation never blocks saving.
    """
    typed = company.distance_km is not None and company.distance_km != old_distance
    if typed:
        company.distance_auto = False
        return
    address = _address(company)
    needed = company.distance_km is None or (was_auto and address != old_address)
    if not needed or not (company.street or company.city) or not distance.office_text():
        return                              # nothing to do, or no office address set yet
    try:
        company.distance_km = distance.road_distance_km(address)
        company.distance_auto = True
    except distance.DistanceError as exc:
        if was_auto and address != old_address:
            company.distance_km = None      # the old calculated value is no longer right
        flash(f"Afstand niet berekend: {exc}", "warning")


@bp.route("/distance")
def calc_distance():
    """JSON for the "Bereken afstand" button in the relation form."""
    address = distance.address_text(*(request.args.get(f, "") for f in ("street", "postal_code", "city", "country")))
    try:
        km = distance.road_distance_km(address)
    except distance.DistanceError as exc:
        return jsonify(ok=False, error=str(exc))
    return jsonify(ok=True, km=float(km), provider=distance.provider_name())


@bp.route("/<int:company_id>/delete", methods=["POST"])
def delete(company_id: int):
    """Delete an unused relation; a relation that is in use is deactivated instead."""
    company = db.get_or_404(Company, company_id)
    used = sum(_usage([company.id])[company.id].values()) + db.session.scalar(
        sa.select(sa.func.count(SupplierCatalog.id)).filter_by(supplier_id=company.id))
    if used:
        company.active = False
        flash(f"{company.name} wordt gebruikt en werd daarom op inactief gezet (de historiek blijft bewaard).", "warning")
        db.session.commit()
        return redirect(url_for("relations.detail", company_id=company.id))
    db.session.delete(company)
    db.session.commit()
    flash("Relatie verwijderd.", "success")
    return redirect(url_for("relations.index"))


# ------------------------------------------------------------------ contact persons
@bp.route("/contacts")
def contacts():
    """All contact persons, with search (name, first name, relation, function, e-mail, phone)."""
    search = request.args.get("q", "").strip()
    query = (sa.select(Contact).join(Company, Contact.company_id == Company.id)
             .order_by(Contact.last_name, Contact.first_name, Company.name))
    if not request.args.get("inactive"):
        query = query.where(Contact.active.is_(True), Company.active.is_(True))
    for word in search.split():
        pattern = f"%{word}%"
        query = query.where(sa.or_(Contact.last_name.ilike(pattern), Contact.first_name.ilike(pattern),
                                   Company.name.ilike(pattern), Contact.function.ilike(pattern),
                                   Contact.email.ilike(pattern), Contact.phone.ilike(pattern),
                                   Contact.mobile.ilike(pattern)))
    page = db.paginate(query, per_page=current_app.config["ITEMS_PER_PAGE"], error_out=False)
    return render_template("relations/contacts.html", page=page, search=search, kinds=KINDS)


@bp.route("/contacts/new", methods=["GET", "POST"])
@bp.route("/contacts/<int:contact_id>/edit", methods=["GET", "POST"])
def edit_contact(contact_id: int | None = None):
    """Add or edit a contact person (popup on the relation page, or a page)."""
    contact = db.get_or_404(Contact, contact_id) if contact_id else None
    form = ContactForm(obj=contact)
    current = contact.company_id if contact else int_arg("company")
    form.company_id.choices = [(0, "- kies de klant of leverancier -")] + [
        (c.id, c.name) for c in db.session.scalars(
            sa.select(Company).where(sa.or_(Company.active.is_(True), Company.id == (current or 0))).order_by(Company.name))]
    if not form.is_submitted() and contact is None:
        form.company_id.data = current or 0
    if form.validate_on_submit():
        if contact is None:
            contact = Contact()
            db.session.add(contact)
        form.populate_obj(contact)
        for field in ("first_name", "function", "email", "phone", "mobile", "notes"):
            setattr(contact, field, (getattr(contact, field) or "").strip() or None)
        contact.last_name = contact.last_name.strip()
        db.session.commit()
        flash(f"Contactpersoon {contact.full_name} opgeslagen.", "success")
        if is_modal_request():
            return jsonify(ok=True)
        return redirect(next_url(url_for("relations.detail", company_id=contact.company_id)))
    context = dict(form=form, contact=contact,
                   action=url_for("relations.edit_contact", contact_id=contact.id) if contact
                   else url_for("relations.edit_contact", company=current or None))
    if is_modal_request():
        return render_template("relations/_contact_modal.html", **context), 422 if form.errors else 200
    return render_template("relations/contact_edit.html", cancel_url=next_url(url_for("relations.contacts")), **context)


@bp.route("/contacts/<int:contact_id>/delete", methods=["POST"])
def delete_contact(contact_id: int):
    contact = db.get_or_404(Contact, contact_id)
    company_id, name = contact.company_id, contact.full_name
    db.session.delete(contact)
    db.session.commit()
    flash(f"Contactpersoon {name} verwijderd.", "success")
    return redirect(next_url(url_for("relations.detail", company_id=company_id)))


def add_button(field, kind: str) -> None:
    """Show a '+' button next to a relation select to create one in a popup.

    Used by the project, article, order and catalogue forms; rendered by the
    render_field macro (templates/_macros.html).
    """
    if kind not in KINDS:
        abort(500)
    field.render_kw = dict(field.render_kw or {}, **{
        "data-add-url": url_for("relations.edit", kind=kind, select=field.name),
        "data-add-title": "Nieuwe klant" if kind == "customer" else "Nieuwe leverancier",
    })
