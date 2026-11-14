"""Authentication: log in, log out and change own password."""

from datetime import timedelta

import sqlalchemy as sa
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db, login_manager
from ..forms import ChangePasswordForm, LoginForm
from ..models import LoginAttempt, User
from ..models.base import utcnow
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
        username = form.username.data.strip()
        ip = request.remote_addr or "?"
        if is_blocked(username, ip):
            # Also when the password would be right: otherwise guessing just continues.
            flash(f"Te veel mislukte aanmeldpogingen. Probeer het over {LOCK_MINUTES} minuten opnieuw.", "danger")
            return render_template("auth/login.html", form=form), 429
        user = db.session.scalar(sa.select(User).filter_by(username=username))
        ok = user is not None and user.active and user.check_password(form.password.data)
        db.session.add(LoginAttempt(username=username.lower()[:150], ip=ip[:64], success=ok))
        # Keep the log small: 90 days is plenty to look back at suspicious attempts.
        db.session.execute(sa.delete(LoginAttempt).where(LoginAttempt.created_at < utcnow() - timedelta(days=90)))
        db.session.commit()
        if not ok:
            # Same message for unknown user and wrong password: do not reveal
            # which usernames exist.
            current_app.logger.warning("Mislukte aanmelding voor %r vanaf %s", username, ip)
            flash("Ongeldige gebruikersnaam of wachtwoord.", "danger")
        else:
            session.clear()   # fresh session after login (no session fixation)
            login_user(user, remember=form.remember.data)
            return redirect(next_url(url_for("main.dashboard")))
    return render_template("auth/login.html", form=form)


#: Login protection: after MAX_USER_FAILURES wrong passwords for one user name
#: (or MAX_IP_FAILURES from one address) logging in is refused for LOCK_MINUTES.
MAX_USER_FAILURES = 5
MAX_IP_FAILURES = 20
LOCK_MINUTES = 15


def is_blocked(username: str, ip: str) -> bool:
    """Too many recent failed logins for this user name or from this address?

    For a user name only failures after its last successful login count.
    """
    since = utcnow() - timedelta(minutes=LOCK_MINUTES)
    username = username.lower()
    last_success = (sa.select(sa.func.max(LoginAttempt.created_at))
                    .where(LoginAttempt.username == username, LoginAttempt.success.is_(True))
                    .scalar_subquery())
    failures = sa.select(sa.func.count(LoginAttempt.id)).where(
        LoginAttempt.success.is_(False), LoginAttempt.created_at > since)
    user_failures = db.session.scalar(failures.where(
        LoginAttempt.username == username,
        sa.or_(last_success.is_(None), LoginAttempt.created_at > last_success)))
    if user_failures >= MAX_USER_FAILURES:
        return True
    return db.session.scalar(failures.where(LoginAttempt.ip == ip)) >= MAX_IP_FAILURES


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
