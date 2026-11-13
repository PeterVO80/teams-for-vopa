"""Authentication: log in, log out and change own password."""

import sqlalchemy as sa
from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db, login_manager
from ..forms import ChangePasswordForm, LoginForm
from ..models import User
from ..utils import next_url

bp = Blueprint("auth", __name__)


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    """Flask-Login callback: reload the user object from the session id."""
    return db.session.get(User, int(user_id))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(sa.select(User).filter_by(username=form.username.data.strip()))
        # Same message for unknown user and wrong password: do not reveal
        # which usernames exist.
        if user is None or not user.check_password(form.password.data) or not user.active:
            flash("Ongeldige gebruikersnaam of wachtwoord.", "danger")
        else:
            login_user(user, remember=form.remember.data)
            return redirect(next_url(url_for("main.dashboard")))
    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Je bent afgemeld.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append("Huidig wachtwoord is onjuist.")
        else:
            current_user.set_password(form.new_password.data)
            db.session.commit()
            flash("Wachtwoord gewijzigd.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("form_page.html", form=form, title="Wachtwoord wijzigen")
