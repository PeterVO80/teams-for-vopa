"""Quotes (offertes): material and labour lines in sections (models/quote.py).

* ``/quotes/``                      - list with filters
* ``/quotes/new``, ``/<id>/edit``   - header (customer, project, dates, introduction)
* ``/quotes/<id>``                  - the quote with its lines, subtotals per section and totals
* ``/quotes/<id>/lines/new?kind=``  - popup: section title, material or labour line
                                      (``&section=<id>``: at the end of that section)
* ``/quotes/<id>/create-project``   - make a project from the quote and link it

A sent or accepted quote is locked: its details and lines can't change any
more, only its status (back to "Concept" unlocks it) - or make a copy.
Labour lines pick a kind of service; its hourly rate (Instellingen) is suggested.

Prices: the purchase price of an article is suggested as ``cost_price``; the
sales price is purchase price + markup (default markup and hourly rate on the
*Instellingen* page). Quotes on a project follow the project's visibility.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

import sqlalchemy as sa
from flask import abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user

from ..access import link_user, project_scope, require_project, visible_projects_condition
from ..extensions import db
from ..forms import QuoteForm, QuoteLineForm, none_if_zero
from ..models import (
    AppSetting,
    Article,
    Company,
    Contact,
    Project,
    Quote,
    QuoteLine,
    QuoteLineKind,
    PdfDetail,
    QuoteStatus,
    QuoteTextTemplate,
    ServiceType,
)
from ..services import distance
from ..services.numbering import insert_with_unique_number, next_number
from ..services.quote_pdf import build_quote_pdf
from ..utils import int_arg, is_modal_request
from . import protected_blueprint
from .relations import add_button as add_relation_button

bp = protected_blueprint("quotes", __name__)

#: Default validity of a new quote.
VALID_DAYS = 30


def default_hourly_rate() -> Decimal | None:
    value = AppSetting.get("quote.hourly_rate")
    try:
        return Decimal(value.replace(",", ".")) if value else None
    except ArithmeticError:
        return None


def default_markup() -> Decimal | None:
    value = AppSetting.get("quote.markup")
    try:
        return Decimal(value.replace(",", ".")) if value else None
    except ArithmeticError:
        return None


def _get_quote(quote_id: int) -> Quote:
    quote = db.get_or_404(Quote, quote_id)
    require_project(quote.project)
    return quote


def _locked(quote: Quote):
    """Response when ``quote`` is sent / accepted and may no longer change, else None."""
    if not quote.locked:
        return None
    message = (f"Offerte {quote.number} is {quote.status.label.lower()} en kan niet meer gewijzigd worden. "
               "Maak een kopie voor een nieuwe versie, of zet de status terug op Concept.")
    if is_modal_request():
        return render_template("quotes/_locked_modal.html", message=message), 409 if request.method == "POST" else 200
    flash(message, "warning")
    return redirect(url_for("quotes.detail", quote_id=quote.id))


def _assign_number(quote: Quote) -> None:
    quote.number = next_number(Quote.number, "OFF", separator="-")


# ------------------------------------------------------------------ list
@bp.route("/")
def index():
    status = request.args.get("status", "")
    search = request.args.get("q", "").strip()
    customer_id = int_arg("customer")
    query = (sa.select(Quote).outerjoin(Company, Quote.customer_id == Company.id)
             .where(project_scope(Quote.project_id)).order_by(Quote.number.desc()))
    if status == "open":
        query = query.where(Quote.status.in_([QuoteStatus.DRAFT, QuoteStatus.SENT]))
    elif status:
        query = query.where(Quote.status == QuoteStatus(status))
    if customer_id:
        query = query.where(Quote.customer_id == customer_id)
    if search:
        like = f"%{search}%"
        query = query.where(sa.or_(Quote.number.ilike(like), Quote.title.ilike(like), Company.name.ilike(like)))
    quotes = db.session.scalars(query).all()
    customers = db.session.scalars(
        sa.select(Company).where(Company.id.in_(sa.select(Quote.customer_id))).order_by(Company.name)).all()
    return render_template("quotes/index.html", quotes=quotes, status=status, search=search,
                           customer_id=customer_id, customers=customers, statuses=QuoteStatus)


# ------------------------------------------------------------------ header
def _project_choices(current: Project | None) -> list[tuple[int, str]]:
    query = sa.select(Project).order_by(Project.code.desc())
    condition = visible_projects_condition()
    if condition is not None:
        query = query.where(condition)
    projects = db.session.scalars(query).all()
    if current is not None and current not in projects:
        projects.insert(0, current)
    return [(0, "- geen project -")] + [(p.id, p.display_name) for p in projects]


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:quote_id>/edit", methods=["GET", "POST"])
def edit(quote_id: int | None = None):
    quote = _get_quote(quote_id) if quote_id else None
    if quote is not None and (blocked := _locked(quote)):
        return blocked
    form = QuoteForm(obj=quote)
    form.customer_id.choices = [(0, "- kies een klant -")] + [
        (c.id, c.name) for c in db.session.scalars(
            sa.select(Company).where(sa.or_(Company.is_customer.is_(True), Company.id == (quote.customer_id if quote else 0)))
            .order_by(Company.name))]
    add_relation_button(form.customer_id, "customer")
    form.project_id.choices = _project_choices(quote.project if quote else None)

    if not form.is_submitted():
        if quote:
            form.customer_id.data = quote.customer_id or 0
            form.contact_id.data = quote.contact_id or 0
            form.project_id.data = quote.project_id or 0
        else:
            form.quote_date.data = date.today()
            form.valid_until.data = date.today() + timedelta(days=VALID_DAYS)
            project = db.session.get(Project, int_arg("project") or 0)
            if project is not None:
                require_project(project)
                form.project_id.data = project.id
                form.customer_id.data = project.customer_id or 0
                form.title.data = project.name

    if form.validate_on_submit():
        project = db.session.get(Project, form.project_id.data) if form.project_id.data else None
        if project is not None:
            require_project(project)
        is_new = quote is None
        if is_new:
            quote = Quote(created_by_id=current_user.id)
        form.populate_obj(quote)
        quote.customer_id = none_if_zero(form.customer_id.data)
        # The contact person must belong to the chosen customer.
        contact = db.session.get(Contact, form.contact_id.data or 0)
        quote.contact_id = contact.id if contact is not None and contact.company_id == quote.customer_id else None
        quote.project_id = project.id if project else None
        if is_new:
            insert_with_unique_number(quote, lambda: _assign_number(quote))
        db.session.commit()
        flash(f"Offerte {quote.number} {'aangemaakt' if is_new else 'opgeslagen'}.", "success")
        return redirect(url_for("quotes.detail", quote_id=quote.id))

    # Contact persons of all customers; the form shows those of the chosen customer.
    contacts = db.session.scalars(
        sa.select(Contact).join(Company, Contact.company_id == Company.id)
        .where(sa.or_(sa.and_(Contact.active.is_(True), Company.is_customer.is_(True)),
                      Contact.id == ((quote.contact_id if quote else None) or 0)))
        .order_by(Contact.last_name, Contact.first_name)).all()
    return render_template("quotes/edit.html", form=form, quote=quote, text_templates=QuoteTextTemplate.ordered(),
                           contacts=contacts,
                           cancel_url=url_for("quotes.detail", quote_id=quote.id) if quote else url_for("quotes.index"))


@bp.route("/<int:quote_id>")
def detail(quote_id: int):
    quote = _get_quote(quote_id)
    return render_template("quotes/detail.html", quote=quote, sections=quote.sections(),
                           statuses=QuoteStatus, kinds=QuoteLineKind)


@bp.route("/<int:quote_id>/pdf")
def pdf(quote_id: int):
    """The quote as a PDF for the customer (without purchase prices and margins)."""
    quote = _get_quote(quote_id)
    logo = os.path.join(current_app.static_folder, "img", "vopa-logo-print.png")
    data = build_quote_pdf(quote, distance.company_name(), distance.office_display_text(), logo=logo,
                           company_lines=distance.company_lines())
    name = f"Offerte {quote.number}" + (f" {quote.customer.name}" if quote.customer else "") + ".pdf"
    return send_file(BytesIO(data), mimetype="application/pdf", as_attachment=True,
                     download_name=name.replace("/", "-"))


@bp.route("/<int:quote_id>/status", methods=["POST"])
def set_status(quote_id: int):
    quote = _get_quote(quote_id)
    quote.status = QuoteStatus(request.form.get("status", "draft"))
    db.session.commit()
    flash(f"Offerte {quote.number}: {quote.status.label}.", "success")
    return redirect(url_for("quotes.detail", quote_id=quote.id))


@bp.route("/<int:quote_id>/delete", methods=["POST"])
def delete(quote_id: int):
    quote = _get_quote(quote_id)
    if blocked := _locked(quote):
        return blocked
    number = quote.number
    db.session.delete(quote)
    db.session.commit()
    flash(f"Offerte {number} verwijderd.", "success")
    return redirect(url_for("quotes.index"))


@bp.route("/<int:quote_id>/copy", methods=["POST"])
def copy(quote_id: int):
    """New draft quote with the same customer and lines (e.g. a revised version)."""
    source = _get_quote(quote_id)
    quote = Quote(title=source.title, customer_id=source.customer_id, contact_id=source.contact_id,
                  project_id=source.project_id,
                  introduction=source.introduction, notes=source.notes, vat_rate=source.vat_rate,
                  quote_date=date.today(), valid_until=date.today() + timedelta(days=VALID_DAYS),
                  created_by_id=current_user.id)
    for line in source.lines:
        quote.lines.append(QuoteLine(position=line.position, kind=line.kind, article_id=line.article_id,
                                     service_type_id=line.service_type_id, pdf_detail=line.pdf_detail,
                                     description=line.description, quantity=line.quantity, unit=line.unit,
                                     unit_price=line.unit_price, cost_price=line.cost_price, note=line.note))
    insert_with_unique_number(quote, lambda: _assign_number(quote))
    db.session.commit()
    flash(f"Kopie {quote.number} gemaakt van offerte {source.number}.", "success")
    return redirect(url_for("quotes.detail", quote_id=quote.id))


@bp.route("/<int:quote_id>/create-project", methods=["POST"])
def create_project(quote_id: int):
    """Create a project from the quote (name, customer, introduction) and link both."""
    quote = _get_quote(quote_id)
    if not current_user.can_write("projects"):
        abort(403)
    if quote.project is not None:
        flash(f"Deze offerte is al gekoppeld aan project {quote.project.code}.", "warning")
        return redirect(url_for("quotes.detail", quote_id=quote.id))
    project = Project(name=quote.title, customer_id=quote.customer_id, manager_id=current_user.id,
                      description=f"Aangemaakt uit offerte {quote.number}."
                                  + (f"\n\n{quote.introduction}" if quote.introduction else ""))
    link_user(project, current_user)
    insert_with_unique_number(project, project.assign_code)
    quote.project = project
    if quote.status in (QuoteStatus.DRAFT, QuoteStatus.SENT):
        quote.status = QuoteStatus.ACCEPTED
    db.session.commit()
    flash(f"Project {project.code} aangemaakt uit offerte {quote.number}.", "success")
    return redirect(url_for("projects.detail", project_id=project.id))


# ------------------------------------------------------------------ lines
def _line_form(line: QuoteLine | None, kind: QuoteLineKind) -> QuoteLineForm:
    form = QuoteLineForm(obj=line)
    services = ServiceType.choices()
    if line is not None and line.service_type is not None and line.service_type not in services:
        services.append(line.service_type)                 # deactivated since
    form.service_types = services
    form.service_type_id.choices = [(0, "- kies de soort dienst -")] + [(s.id, s.name) for s in services]
    if kind == QuoteLineKind.SECTION:
        form.description.label.text = "Titel van de sectie"
    elif kind == QuoteLineKind.LABOUR:
        form.quantity.label.text = "Aantal uren"
        form.unit_price.label.text = "Uurtarief (€, excl. btw)"
    if not form.is_submitted():
        form.kind.data = kind.value
        if line is not None:
            form.pdf_detail.data = line.pdf_detail.value if line.pdf_detail else PdfDetail.FULL.value
            form.service_type_id.data = line.service_type_id or 0
            form.markup.data = line.markup
        elif kind == QuoteLineKind.LABOUR:
            form.unit.data = "u"
            form.unit_price.data = default_hourly_rate()
        elif kind == QuoteLineKind.MATERIAL:
            form.unit.data = "st"
            form.markup.data = default_markup()
    return form


def _validate_amounts(form: QuoteLineForm, kind: QuoteLineKind) -> bool:
    service = db.session.get(ServiceType, form.service_type_id.data or 0)
    if not (form.description.data or "").strip():
        if kind == QuoteLineKind.LABOUR and service:
            form.description.data = service.name
        else:
            form.description.errors.append("Vul " + ("de titel" if kind == QuoteLineKind.SECTION else "de omschrijving") + " in.")
            return False
    if kind == QuoteLineKind.SECTION:
        return True
    ok = True
    if not form.quantity.data:
        form.quantity.errors.append("Vul " + ("het aantal uren" if kind == QuoteLineKind.LABOUR else "het aantal") + " in.")
        ok = False
    if kind == QuoteLineKind.LABOUR and form.unit_price.data is None and service and service.hourly_rate is not None:
        form.unit_price.data = service.hourly_rate
    if form.unit_price.data is None:
        if kind == QuoteLineKind.MATERIAL and form.cost_price.data is not None and form.markup.data is not None:
            form.unit_price.data = (form.cost_price.data * (1 + form.markup.data / 100)).quantize(Decimal("0.0001"))
        else:
            form.unit_price.errors.append("Vul de " + ("uurprijs" if kind == QuoteLineKind.LABOUR else "verkoopprijs") + " in.")
            ok = False
    return ok


def _fill_line(line: QuoteLine, form: QuoteLineForm, kind: QuoteLineKind) -> None:
    line.kind = kind
    line.description = form.description.data.strip()
    line.note = (form.note.data or "").strip() or None
    if kind == QuoteLineKind.SECTION:
        line.article_id = line.quantity = line.unit = line.unit_price = line.cost_price = line.service_type_id = None
        try:
            line.pdf_detail = PdfDetail(form.pdf_detail.data)
        except ValueError:
            line.pdf_detail = PdfDetail.FULL
        return
    line.quantity = form.quantity.data
    line.unit = (form.unit.data or "").strip() or None
    line.unit_price = form.unit_price.data
    if kind == QuoteLineKind.MATERIAL:
        article = db.session.get(Article, int(form.article_id.data)) if (form.article_id.data or "").isdigit() else None
        line.article_id = article.id if article else None
        line.cost_price = form.cost_price.data
        line.service_type_id = None
    else:
        line.article_id = line.cost_price = None
        service = db.session.get(ServiceType, form.service_type_id.data or 0)
        line.service_type_id = service.id if service else None


def _line_response(form, quote, line, kind, action):
    context = dict(form=form, quote=quote, line=line, kind=kind, action=action, kinds=QuoteLineKind,
                   linked={"label": f"{line.article.article_number} - {line.article.description}"}
                   if line is not None and line.article else None)
    status = 422 if form.errors else 200
    if is_modal_request():
        return render_template("quotes/_line_modal.html", **context), status
    return render_template("quotes/line.html", cancel_url=url_for("quotes.detail", quote_id=quote.id), **context), status


def _done(quote: Quote, message: str):
    flash(message, "success")
    if is_modal_request():
        return jsonify(ok=True)
    return redirect(url_for("quotes.detail", quote_id=quote.id))


@bp.route("/<int:quote_id>/lines/new", methods=["GET", "POST"])
def add_line(quote_id: int):
    quote = _get_quote(quote_id)
    if blocked := _locked(quote):
        return blocked
    try:
        kind = QuoteLineKind(request.values.get("kind", "material"))
    except ValueError:
        abort(404)
    section = db.session.get(QuoteLine, int_arg("section") or 0)
    if section is not None and (section.quote_id != quote.id or section.kind != QuoteLineKind.SECTION):
        section = None
    form = _line_form(None, kind)
    if form.validate_on_submit() and _validate_amounts(form, kind):
        line = QuoteLine()
        _fill_line(line, form, kind)
        if section is not None:
            # At the end of that section: just before the next section title.
            following = [l.position for l in quote.lines if l.kind == QuoteLineKind.SECTION and l.position > section.position]
            line.position = (min(following) - 5) if following else quote.next_position
        else:
            line.position = quote.next_position
        quote.lines.append(line)
        db.session.flush()
        quote.renumber()
        db.session.commit()
        return _done(quote, f"{kind.label} toegevoegd.")
    action = url_for("quotes.add_line", quote_id=quote.id, kind=kind.value, section=section.id if section else None)
    return _line_response(form, quote, None, kind, action)


@bp.route("/lines/<int:line_id>/edit", methods=["GET", "POST"])
def edit_line(line_id: int):
    line = db.get_or_404(QuoteLine, line_id)
    quote = _get_quote(line.quote_id)
    if blocked := _locked(quote):
        return blocked
    kind = line.kind
    form = _line_form(line, kind)
    if form.validate_on_submit() and _validate_amounts(form, kind):
        _fill_line(line, form, kind)
        db.session.commit()
        return _done(quote, "Lijn opgeslagen.")
    return _line_response(form, quote, line, kind, url_for("quotes.edit_line", line_id=line.id))


@bp.route("/lines/<int:line_id>/delete", methods=["POST"])
def delete_line(line_id: int):
    line = db.get_or_404(QuoteLine, line_id)
    quote = _get_quote(line.quote_id)
    if blocked := _locked(quote):
        return blocked
    quote.lines.remove(line)
    db.session.flush()
    quote.renumber()
    db.session.commit()
    return _done(quote, "Lijn verwijderd.")


@bp.route("/lines/<int:line_id>/move", methods=["POST"])
def move_line(line_id: int):
    """Move a line one place up or down (``direction`` = up / down)."""
    line = db.get_or_404(QuoteLine, line_id)
    quote = _get_quote(line.quote_id)
    if blocked := _locked(quote):
        return blocked
    quote.renumber()
    lines = sorted(quote.lines, key=lambda l: l.position)
    index = lines.index(line)
    other = index - 1 if request.form.get("direction") == "up" else index + 1
    if 0 <= other < len(lines):
        line.position, lines[other].position = lines[other].position, line.position
        db.session.commit()
    return redirect(url_for("quotes.detail", quote_id=quote.id) + f"#line-{line.id}")
