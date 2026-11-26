"""Authentication: log in (password + authenticator-app code, or Microsoft),
log out, change own password and manage the two-step login.

Password login with two-step verification (services/mfa.py):

1. ``/auth/login``: user name + password. When right, the user is *not* logged
   in yet: the pending login is kept in the session (10 minutes).
2. ``/auth/mfa``: the 6-digit code (or a recovery code) -> logged in.
   ``/auth/mfa/setup`` first, when the account has no authenticator app yet
   and two-step login is required: scan the QR code, confirm with a code,
   write down the recovery codes.

Wrong codes count as failed logins (blocking after too many). A right
password alone is not recorded as a successful login, so it can't reset the
counter of wrong codes.
"""

import time
from datetime import timedelta

import sqlalchemy as sa
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db, login_manager
from ..forms import ChangePasswordForm, LoginForm, MfaCodeForm
from ..models import LoginAttempt, User
from ..models.base import utcnow
from ..services import mfa, microsoft_login
from ..utils import next_url

bp = Blueprint("auth", __name__)


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    """Flask-Login callback: reload the user object from the session id.

    The id has the form "<user id>:<login generation>" (User.get_id); a
    session from before the last restore of a back-up is no longer valid.
    """
    from ..services.backup import session_generation

    uid, _, generation = (user_id or "").partition(":")
    if not uid.isdigit() or generation != session_generation():
        return None
    return db.session.get(User, int(uid))


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
        if ok and microsoft_login.required() and not user.is_admin:
            # Only Microsoft (with its two-step verification); administrators
            # keep their password as an emergency entrance.
            flash("Meld je aan met je Microsoft-account.", "warning")
            return render_template("auth/login.html", form=form, microsoft=microsoft_login.mode())
        if ok and mfa.needed_for(user):
            # Right password: on to the second step (not logged in yet).
            target = next_url(url_for("main.dashboard"))
            session.clear()
            session[PENDING] = {"user": user.id, "remember": bool(form.remember.data), "next": target,
                                "at": int(time.time())}
            return redirect(url_for("auth.mfa_verify" if user.mfa_enabled else "auth.mfa_setup"))
        _record(username, ip, ok)
        if not ok:
            # Same message for unknown user and wrong password: do not reveal
            # which usernames exist.
            current_app.logger.warning("Mislukte aanmelding voor %r vanaf %s", username, ip)
            flash("Ongeldige gebruikersnaam of wachtwoord.", "danger")
        else:
            session.clear()   # fresh session after login (no session fixation)
            login_user(user, remember=form.remember.data)
            return redirect(next_url(url_for("main.dashboard")))
    return render_template("auth/login.html", form=form, microsoft=microsoft_login.mode())


@bp.route("/microsoft")
def microsoft():
    """Start "Aanmelden met Microsoft": redirect to Microsoft's sign-in page."""
    if not microsoft_login.enabled():
        abort(404)
    url, flow = microsoft_login.authorize_url()
    flow["next"] = next_url(url_for("main.dashboard"))
    session["ms_login"] = flow
    return redirect(url)


@bp.route("/microsoft/callback")
def microsoft_callback():
    """Microsoft sends the user back here after signing in."""
    if not microsoft_login.enabled():
        abort(404)
    flow = session.pop("ms_login", None)
    try:
        user = microsoft_login.complete_login(request.args, flow)
    except microsoft_login.LoginError as exc:
        db.session.rollback()
        current_app.logger.warning("Microsoft-aanmelding mislukt vanaf %s: %s", request.remote_addr, exc)
        flash(str(exc), "danger")
        return redirect(url_for("auth.login"))
    db.session.add(LoginAttempt(username=user.username.lower()[:150], ip=(request.remote_addr or "?")[:64], success=True))
    db.session.commit()
    session.clear()   # fresh session after login (no session fixation)
    # No "remember me": a new browser session asks Microsoft again, so an
    # account disabled in Microsoft 365 loses access quickly.
    login_user(user, remember=False)
    target = flow.get("next") if flow else None
    return redirect(target if target and target.startswith("/") and not target.startswith("//")
                    else url_for("main.dashboard"))


def _record(username: str, ip: str, success: bool) -> None:
    """Log a login attempt (and keep the log at 90 days)."""
    db.session.add(LoginAttempt(username=username.lower()[:150], ip=ip[:64], success=success))
    db.session.execute(sa.delete(LoginAttempt).where(LoginAttempt.created_at < utcnow() - timedelta(days=90)))
    db.session.commit()


# ------------------------------------------------------------------ two-step login
#: Session key of a login waiting for its second step, and how long it may wait.
PENDING = "mfa_pending"
PENDING_SECONDS = 600
#: Session key of a secret being set up (encrypted: the session cookie is only signed).
SETUP_SECRET = "mfa_setup_secret"


def _pending_user() -> User | None:
    """The user whose password was right and who still has to give a code."""
    pending = session.get(PENDING)
    if not pending or int(time.time()) - pending.get("at", 0) > PENDING_SECONDS:
        session.pop(PENDING, None)
        return None
    user = db.session.get(User, pending.get("user") or 0)
    return user if user is not None and user.active else None


def _finish_login(user: User):
    pending = session.get(PENDING) or {}
    session.clear()   # fresh session after login (no session fixation)
    login_user(user, remember=bool(pending.get("remember")))
    target = pending.get("next") or ""
    return target if target.startswith("/") and not target.startswith("//") else url_for("main.dashboard")


@bp.route("/mfa", methods=["GET", "POST"])
def mfa_verify():
    """Second step of the login: the code from the authenticator app (or a recovery code)."""
    user = _pending_user()
    if user is None:
        flash("Meld je opnieuw aan.", "warning")
        return redirect(url_for("auth.login"))
    if not user.mfa_enabled:
        return redirect(url_for("auth.mfa_setup"))
    form = MfaCodeForm()
    ip = request.remote_addr or "?"
    if form.validate_on_submit():
        if is_blocked(user.username, ip):
            flash(f"Te veel mislukte aanmeldpogingen. Probeer het over {LOCK_MINUTES} minuten opnieuw.", "danger")
            return render_template("auth/mfa_verify.html", form=form), 429
        used = mfa.verify(user, form.code.data)
        _record(user.username, ip, used is not None)
        if used is None:
            current_app.logger.warning("Foute verificatiecode voor %r vanaf %s", user.username, ip)
            form.code.errors.append("Ongeldige of al gebruikte code. Probeer de volgende code van de app.")
        else:
            target = _finish_login(user)
            if used == "recovery":
                left = mfa.remaining_recovery_codes(user)
                flash(f"Je hebt een herstelcode gebruikt; er blijven er nog {left} over. "
                      "Maak nieuwe aan via Tweestapsverificatie als je gsm kwijt is.", "warning")
            return redirect(target)
    return render_template("auth/mfa_verify.html", form=form)


@bp.route("/mfa/setup", methods=["GET", "POST"])
def mfa_setup():
    """Set up the authenticator app: during the login (required) or later from the account menu."""
    pending = None if current_user.is_authenticated else _pending_user()
    user = current_user if current_user.is_authenticated else pending
    if user is None:
        return redirect(url_for("auth.login"))
    if pending is not None and pending.mfa_enabled:
        return redirect(url_for("auth.mfa_verify"))       # already set up: just the code
    secret = mfa.decrypt(session.get(SETUP_SECRET))
    if not secret:
        secret = mfa.new_secret()
        session[SETUP_SECRET] = mfa.encrypt(secret)
    form = MfaCodeForm()
    if form.validate_on_submit():
        step = mfa.check_new_secret(secret, form.code.data)
        if step is None:
            form.code.errors.append("Deze code klopt niet. Controleer of je de QR-code van deze pagina hebt gescand "
                                    "en of de klok van je gsm juist staat.")
        else:
            codes = mfa.enable(user, secret, step)
            db.session.commit()
            current_app.logger.info("Tweestapsverificatie ingesteld voor %s", user.username)
            target = url_for("main.dashboard")
            if pending is not None:
                _record(user.username, request.remote_addr or "?", True)
                target = _finish_login(user)
            session.pop(SETUP_SECRET, None)
            return render_template("auth/mfa_recovery.html", codes=codes, target=target, first=True)
    uri = mfa.provisioning_uri(secret, user.username)
    return render_template("auth/mfa_setup.html", form=form, qr=mfa.qr_data_uri(uri), secret=mfa.grouped(secret),
                           forced=pending is not None, replacing=current_user.is_authenticated and user.mfa_enabled)


@bp.route("/mfa/manage", methods=["GET", "POST"])
@login_required
def mfa_manage():
    """Status of the own two-step login; new recovery codes (with a valid code)."""
    form = MfaCodeForm()
    if form.validate_on_submit() and current_user.mfa_enabled:
        if not mfa.confirm(current_user, form.code.data):
            form.code.errors.append("Ongeldige code: geef de huidige code uit je authenticator-app.")
        else:
            codes = mfa.new_recovery_codes(current_user)
            db.session.commit()
            return render_template("auth/mfa_recovery.html", codes=codes, target=url_for("auth.mfa_manage"), first=False)
    return render_template("auth/mfa_manage.html", form=form, required=mfa.required(),
                           remaining=mfa.remaining_recovery_codes(current_user),
                           microsoft_only=bool(current_user.ms_oid) and microsoft_login.required())


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
