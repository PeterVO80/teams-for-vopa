"""User management (administrators only)."""

import sqlalchemy as sa
from flask import flash, redirect, render_template, url_for
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
