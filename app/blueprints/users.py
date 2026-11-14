"""User management (administrators only)."""

import sqlalchemy as sa
from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
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
        elif user is None and not form.password.data:
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
            user.email = form.email.data or None
            user.role = UserRole(form.role.data)
            user.color = form.color.data
            user.active = form.active.data
            user.outlook_sync = form.outlook_sync.data
            if form.password.data:
                user.set_password(form.password.data)
            db.session.commit()
            flash(f"Gebruiker {user.full_name} opgeslagen.", "success")
            return redirect(url_for("users.index"))

    return render_template(
        "form_page.html",
        form=form,
        title="Gebruiker bewerken" if user else "Nieuwe gebruiker",
        cancel_url=url_for("users.index"),
    )


@bp.route("/outlook", methods=["GET", "POST"])
@admin_required
def outlook_status():
    """Status of the Outlook (Microsoft 365) link, with a "synchronise now" button."""
    from ..models import Assignment, CalendarEvent, OutlookLink
    from ..services import outlook

    if request.method == "POST":
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
    pending = db.session.scalar(sa.select(sa.func.count(Assignment.id)).where(
        sa.or_(Assignment.outlook_synced_at.is_(None), Assignment.outlook_synced_at < Assignment.updated_at)))
    return render_template("users/outlook.html", configured=outlook.is_configured(), users=users,
                           counts=counts, written=written, pending=pending)
