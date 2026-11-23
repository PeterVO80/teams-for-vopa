"""Back-up and restore page for administrators (services/backup.py)."""

from flask import abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, logout_user

from ..services import backup
from ..utils import admin_required
from . import protected_blueprint

bp = protected_blueprint("backups", __name__)

#: Text an administrator types to confirm a restore.
CONFIRM_WORD = "HERSTEL"


@bp.route("/")
@admin_required
def index():
    return render_template("backups/index.html", backups=backup.list_backups(), warning=backup.backup_warning(),
                           revision=backup.current_revision(), confirm_word=CONFIRM_WORD,
                           keep_days=current_app.config["BACKUP_KEEP_DAYS"])


@bp.route("/new", methods=["POST"])
@admin_required
def create():
    info = backup.create_backup("manual", current_user.username)
    flash(f"Back-up {info.name} gemaakt ({info.rows} records).", "success")
    return redirect(url_for("backups.index"))


@bp.route("/download-now", methods=["POST"])
@admin_required
def create_and_download():
    """Make a back-up and send it straight to the browser."""
    info = backup.create_backup("manual", current_user.username)
    return send_file(info.path, as_attachment=True, download_name=info.name, mimetype="application/zip")


@bp.route("/upload", methods=["POST"])
@admin_required
def upload():
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Kies een back-upbestand.", "warning")
        return redirect(url_for("backups.index"))
    restore_now = bool(request.form.get("restore"))
    if restore_now and request.form.get("confirm", "").strip().upper() != CONFIRM_WORD:
        flash(f"Typ {CONFIRM_WORD} om te bevestigen dat alle huidige gegevens vervangen worden.", "warning")
        return redirect(url_for("backups.index"))
    try:
        info = backup.save_upload(file)
    except backup.BackupError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("backups.index"))
    if restore_now:
        return _restore(info.name, info.path)
    flash(f"Back-up opgeladen als {info.name}. Je kan ze nu terugzetten.", "success")
    return redirect(url_for("backups.index"))


@bp.route("/<name>/download")
@admin_required
def download(name: str):
    path = _existing(name)
    return send_file(path, as_attachment=True, download_name=name, mimetype="application/zip")


@bp.route("/<name>/delete", methods=["POST"])
@admin_required
def delete(name: str):
    _existing(name).unlink()
    flash(f"Back-up {name} verwijderd.", "success")
    return redirect(url_for("backups.index"))


@bp.route("/<name>/restore", methods=["POST"])
@admin_required
def restore(name: str):
    path = _existing(name)
    if request.form.get("confirm", "").strip().upper() != CONFIRM_WORD:
        flash(f"Typ {CONFIRM_WORD} om te bevestigen dat alle huidige gegevens vervangen worden.", "warning")
        return redirect(url_for("backups.index"))
    return _restore(name, path)


def _restore(name, path):
    username = current_user.username
    try:
        safety = backup.restore_backup(path, username)
    except backup.BackupError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("backups.index"))
    current_app.logger.warning("Back-up %s teruggezet door %s", name, username)
    # The user accounts themselves may have changed: log in again.
    logout_user()
    flash(f"Back-up {name} is teruggezet. De gegevens van vóór het terugzetten staan in {safety.name}. "
          "Meld je opnieuw aan.", "success")
    return redirect(url_for("auth.login"))


def _existing(name: str):
    try:
        path = backup.backup_path(name)
    except backup.BackupError:
        abort(404)
    if not path.is_file():
        abort(404)
    return path
