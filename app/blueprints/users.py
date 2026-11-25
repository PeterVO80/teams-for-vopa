"""User management (administrators only)."""

import secrets

import sqlalchemy as sa
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..modules import FULL_ACCESS, MODULE_KEYS, MODULES, READ, WRITE, dump_access, parse_access
from ..extensions import db
from ..services import microsoft_login
from ..forms import UserForm
from ..models import User, UserRole
from ..utils import admin_required
from . import protected_blueprint

bp = protected_blueprint("users", __name__)


@bp.route("/")
@admin_required
def index():
    users = db.session.scalars(sa.select(User).order_by(User.active.desc(), User.full_name)).all()
    return render_template("users/index.html", users=users)


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(user_id: int | None = None):
    """Create a new user (no ``user_id``) or edit an existing one."""
    user = db.get_or_404(User, user_id) if user_id else None
    form = UserForm(obj=user)

    if form.validate_on_submit():
        duplicate = db.session.scalar(
            sa.select(User).where(User.username == form.username.data, User.id != (user_id or 0))
        )
        if duplicate:
            form.username.errors.append("Deze gebruikersnaam bestaat al.")
        elif user is None and not form.password.data and not microsoft_login.enabled():
            form.password.errors.append("Een wachtwoord is verplicht voor een nieuwe gebruiker.")
        elif user is not None and user.id == current_user.id and (
            not form.active.data or form.role.data != UserRole.ADMIN.value
        ):
            # Prevent administrators from locking themselves out.
            flash("Je kan je eigen account niet deactiveren of de beheerdersrol afnemen.", "danger")
        else:
            if user is None:
                user = User()
                db.session.add(user)
            user.username = form.username.data.strip()
            user.full_name = form.full_name.data
            outlook_before = (user.email, user.outlook_sync, user.active)
            if (form.email.data or None) != user.email:
                user.ms_oid = None     # new address = link to (possibly) another Microsoft account
            user.email = form.email.data or None
            user.role = UserRole(form.role.data)
            user.color = form.color.data
            user.active = form.active.data
            user.outlook_sync = form.outlook_sync.data
            user.module_access = dump_access(_submitted_levels())
            if user.id and (user.email, user.outlook_sync, user.active) != outlook_before:
                from ..services import outlook
                outlook.mark_user_pending(user)   # (re)write their planning to the right mailbox
            if form.password.data:
                user.set_password(form.password.data)
            elif not user.password_hash:
                # Logs in with Microsoft only: a random password nobody knows.
                user.set_password(secrets.token_urlsafe(32))
            db.session.commit()
            flash(f"Gebruiker {user.full_name} opgeslagen.", "success")
            return redirect(url_for("users.index"))

    if form.is_submitted():
        levels = _submitted_levels()                  # keep the ticks after a validation error
    else:
        levels = parse_access(user.module_access if user else FULL_ACCESS)
    return render_template(
        "users/edit.html",
        form=form,
        modules=MODULES,
        levels=levels,
        title="Gebruiker bewerken" if user else "Nieuwe gebruiker",
        cancel_url=url_for("users.index"),
    )


def _submitted_levels() -> dict[str, str]:
    """Ticks of the access table: write (includes read), read or nothing per module."""
    levels = {}
    for key in MODULE_KEYS:
        if request.form.get(f"write_{key}"):
            levels[key] = WRITE
        elif request.form.get(f"read_{key}"):
            levels[key] = READ
    return levels


@bp.route("/outlook", methods=["GET", "POST"])
@admin_required
def outlook_status():
    """Status of the Outlook (Microsoft 365) link, with a "synchronise now" button."""
    from ..models import AppSetting, CalendarEvent, OutlookLink
    from ..services import outlook

    if request.method == "POST" and request.form.get("action") == "color":
        color = request.form.get("color", "")
        if color and color not in {preset for preset, *_ in outlook.CATEGORY_PALETTE}:
            abort(400)
        AppSetting.put(outlook.COLOR_SETTING, color)
        db.session.commit()
        outlook._category_done.clear()
        flash("Kleur opgeslagen. Ze wordt bij de volgende synchronisatie in ieders Outlook ingesteld."
              if color else "De app past de kleur van de categorie niet meer aan.", "success")
        return redirect(url_for("users.outlook_status"))

    if request.method == "POST":
        outlook._category_done.clear()   # re-check the category colour (permission may be new)
        report = outlook.sync_all()
        flash(f"Synchronisatie klaar: {report.summary}.", "warning" if report.errors else "success")
        for error in report.errors[:10]:
            flash(error, "warning")
        return redirect(url_for("users.outlook_status"))

    users = db.session.scalars(sa.select(User).where(User.active.is_(True)).order_by(User.full_name)).all()
    counts = dict(db.session.execute(
        sa.select(CalendarEvent.user_id, sa.func.count(CalendarEvent.id)).group_by(CalendarEvent.user_id)).all())
    written = dict(db.session.execute(
        sa.select(OutlookLink.user_id, sa.func.count(OutlookLink.id)).group_by(OutlookLink.user_id)).all())
    pending = len(outlook.pending_assignments(*outlook.window()))
    return render_template("users/outlook.html", configured=outlook.is_configured(), users=users,
                           counts=counts, written=written, pending=pending,
                           palette=outlook.CATEGORY_PALETTE, color=outlook.category_color() or "",
                           category=outlook.CATEGORY)


@bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    """Company settings: name and office address (used for distances and the timesheet PDF)."""
    from ..models import AppSetting
    from ..services import distance

    fields = [("company.name", "Bedrijfsnaam (op urenstaat en offerte)"),
              ("company.vat", "Btw-nummer"), ("company.phone", "Telefoon"), ("company.email", "E-mail"), ("office.street", "Straat en nummer"),
              ("office.box", "Bus"), ("office.postal_code", "Postcode"), ("office.city", "Gemeente"), ("office.country", "Land"),
              ("quote.hourly_rate", "Uurtarief zonder soort dienst (€, excl. btw)"), ("quote.markup", "Standaard marge op materiaal (%)")]
    if request.method == "POST":
        for key, _ in fields:
            AppSetting.put(key, (request.form.get(key) or "").strip()[:200])
        errors = _save_service_types()
        if errors:
            db.session.rollback()
            for error in errors:
                flash(error, "danger")
        else:
            db.session.commit()
            flash("Instellingen opgeslagen.", "success")
        return redirect(url_for("users.settings"))
    values = {key: AppSetting.get(key) or "" for key, _ in fields}
    if not values["office.country"]:
        values["office.country"] = "België"
    from ..models import ServiceType

    service_types = db.session.scalars(sa.select(ServiceType).order_by(ServiceType.position, ServiceType.name)).all()
    return render_template("users/settings.html", fields=fields, values=values, service_types=service_types,
                           provider=distance.provider_name())


def _rate(value: str | None, name: str, errors: list[str]):
    """Hourly rate typed on the settings page ("65" / "72,50"); None when empty."""
    from decimal import Decimal, InvalidOperation

    text = (value or "").strip().replace("€", "").replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        rate = Decimal(text)
    except InvalidOperation:
        errors.append(f"Uurtarief van \"{name}\" is geen geldig bedrag: {value}")
        return None
    if rate < 0 or rate > 100000:
        errors.append(f"Uurtarief van \"{name}\" is ongeldig: {value}")
        return None
    return rate.quantize(Decimal("0.01"))


def _save_service_types() -> list[str]:
    """Kinds of service and their hourly rates (quotes): edit, add, remove."""
    from ..models import QuoteLine, ServiceType

    errors: list[str] = []
    services = db.session.scalars(sa.select(ServiceType)).all()
    for service in services:
        prefix = f"service-{service.id}-"
        if f"{prefix}name" not in request.form:
            continue
        if request.form.get(f"{prefix}delete"):
            used = db.session.scalar(sa.select(sa.func.count(QuoteLine.id)).where(QuoteLine.service_type_id == service.id))
            if used:
                service.active = False          # keep it for the quote lines that use it
            else:
                db.session.delete(service)
            continue
        name = " ".join((request.form.get(f"{prefix}name") or "").split())[:120]
        if not name:
            errors.append("Een dienst heeft een naam nodig.")
            continue
        service.name = name
        service.hourly_rate = _rate(request.form.get(f"{prefix}rate"), name, errors)
        service.active = bool(request.form.get(f"{prefix}active"))
    name = " ".join((request.form.get("service-new-name") or "").split())[:120]
    if name:
        position = max((s.position for s in services), default=0) + 10
        db.session.add(ServiceType(name=name, hourly_rate=_rate(request.form.get("service-new-rate"), name, errors),
                                   position=position))
    names = [s.name.lower() for s in services if s not in db.session.deleted]   # unique, also inactive ones
    if name:
        names.append(name.lower())
    if len(names) != len(set(names)):
        errors.append("Elke dienst moet een andere naam hebben.")
    return errors
