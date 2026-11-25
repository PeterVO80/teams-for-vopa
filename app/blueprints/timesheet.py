"""Time registration per project and the timesheet (urenstaat) as PDF.

* ``/time/``                - my hours of a month (administrators: anyone's)
* ``/time/new``, ``/<id>/edit`` - popup to register hours (static/js/timesheet.js)
* ``/time/week``             - popup: one project, several days of a week at once
* ``/time/report``          - timesheet of a project for a period (default:
                              the previous month) with a PDF download; without
                              a project: the hours of all projects, grouped per
                              project, and ``/time/report.zip`` = one PDF per project

Everybody can register hours on the projects they can see (app/access.py).
Only the employee themselves and administrators can change an entry.
"""

from __future__ import annotations

import os
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

import sqlalchemy as sa
from flask import abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user

from ..access import require_project, visible_projects_condition
from ..extensions import db
from ..forms import TimeEntryForm, WeekEntryForm, none_if_zero
from ..models import Company, Project, TimeEntry, User
from ..services import distance
from ..services import timesheet as svc
from ..services.timesheet_pdf import build_pdf
from ..utils import int_arg, is_modal_request, next_url
from . import protected_blueprint

bp = protected_blueprint("timesheet", __name__)


def _visible_projects(open_only: bool) -> list[Project]:
    query = sa.select(Project).order_by(Project.code.desc())
    condition = visible_projects_condition()
    if condition is not None:
        query = query.where(condition)
    if open_only:
        from ..models import ProjectStatus
        query = query.where(Project.status != ProjectStatus.CLOSED)
    return db.session.scalars(query).all()


def _may_edit(entry: TimeEntry) -> bool:
    return current_user.is_admin or entry.user_id == current_user.id


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value or "")
    except ValueError:
        return None


# ------------------------------------------------------------------ overview
@bp.route("/")
def index():
    first, last = svc.month_bounds(request.args.get("month"))
    # Employees see their own hours; administrators can look at anyone (or everybody).
    user_arg = request.args.get("user", "")
    if current_user.is_admin and user_arg == "all":
        user_id = None
    elif current_user.is_admin and user_arg.isdigit():
        user_id = int(user_arg)
    else:
        user_id = current_user.id
    project_id = int_arg("project")

    query = (sa.select(TimeEntry).join(Project, TimeEntry.project_id == Project.id)
             .where(TimeEntry.work_date >= first, TimeEntry.work_date <= last)
             .order_by(TimeEntry.work_date.desc(), TimeEntry.id.desc()))
    condition = visible_projects_condition()
    if condition is not None:
        query = query.where(condition)
    if user_id:
        query = query.where(TimeEntry.user_id == user_id)
    if project_id:
        query = query.where(TimeEntry.project_id == project_id)
    entries = db.session.scalars(query).all()

    # Grouped per week and per day. Looking at one person's hours (no project
    # filter), working days without hours are shown too, up to today.
    show_gaps = user_id is not None and not project_id
    week_groups = svc.weeks(entries, first, last, empty_workdays_until=date.today() if show_gaps else None)
    per_project: dict[int, list] = {}
    for e in entries:
        per_project.setdefault(e.project_id, [e.project, Decimal("0")])[1] += e.hours
    project_totals = sorted(per_project.values(), key=lambda t: -t[1])

    prev_month = (first.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    next_month = (last + timedelta(days=1)).strftime("%Y-%m")
    return render_template(
        "timesheet/index.html", entries=entries, weeks=week_groups, project_totals=project_totals,
        first=first, last=last, can_add=user_id == current_user.id,
        workday_hours=Decimal(str(current_app.config["WORKDAY_HOURS"])),
        prev_month=prev_month, next_month=next_month, month=first.strftime("%Y-%m"),
        user_id=user_id, user_arg=user_arg, users=User.active_choices() if current_user.is_admin else [],
        project_id=project_id, projects=[(p.id, p.display_name) for p in _visible_projects(False)],
        total_hours=sum((e.hours for e in entries), Decimal("0")),
        total_km=sum((e.km or Decimal("0") for e in entries), Decimal("0")),
    )


def _choices(form, entry: TimeEntry | None = None):
    """Projects (open ones; plus the entry's own) and customers for the forms."""
    projects = _visible_projects(open_only=True)
    if entry and entry.project not in projects:
        projects.insert(0, entry.project)          # editing hours of a project that was closed since
    form.project_id.choices = [(0, "- kies een project -")] + [(p.id, p.display_name) for p in projects]
    customers = db.session.scalars(sa.select(Company).where(Company.is_customer.is_(True), Company.active.is_(True))
                                   .order_by(Company.name)).all()
    form.customer_id.choices = [(0, "- kies de klant -")] + [(c.id, c.name) for c in customers]
    return projects, customers


def _apply_field_service(entry: TimeEntry, project: Project, customer_id: int | None) -> None:
    """Field service: customer (default the project's) and km (default 2 x distance)."""
    if entry.field_service:
        customer = db.session.get(Company, none_if_zero(customer_id) or project.customer_id or 0)
        entry.customer_id = customer.id if customer else None   # never the "0 = none" choice
        if entry.km is None and customer and customer.distance_km is not None:
            entry.km = customer.distance_km * 2                # there and back
    else:
        entry.customer_id, entry.km = None, None


# ------------------------------------------------------------------ edit
@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:entry_id>/edit", methods=["GET", "POST"])
def edit(entry_id: int | None = None):
    entry = db.get_or_404(TimeEntry, entry_id) if entry_id else None
    if entry and not _may_edit(entry):
        abort(403)
    if entry:
        require_project(entry.project)
    form = TimeEntryForm(obj=entry)
    projects, customers = _choices(form, entry)

    if not form.is_submitted():
        if entry:
            form.customer_id.data = entry.customer_id or 0
        else:
            form.work_date.data = _parse_date(request.args.get("day")) or date.today()
            form.project_id.data = int_arg("project") or 0

    if form.validate_on_submit():
        project = db.session.get(Project, form.project_id.data)
        if project is None or project not in projects:
            form.project_id.errors.append("Kies een project.")
        else:
            if entry is None:
                entry = TimeEntry(user_id=current_user.id)
                db.session.add(entry)
            form.populate_obj(entry)
            _apply_field_service(entry, project, form.customer_id.data)
            db.session.commit()
            flash(f"{entry.hours.normalize():f}".replace(".", ",") + f" uur op {project.code} geregistreerd.", "success")
            if is_modal_request():
                return jsonify(ok=True)
            return redirect(next_url(url_for("timesheet.index", month=entry.work_date.strftime("%Y-%m"))))

    context = dict(form=form, entry=entry, projects=projects, customers=customers,
                   action=url_for("timesheet.edit", entry_id=entry.id) if entry else url_for("timesheet.edit"))
    if is_modal_request():
        return render_template("timesheet/_form_modal.html", **context), 422 if form.errors else 200
    return render_template("timesheet/edit.html", cancel_url=next_url(url_for("timesheet.index")), **context)


DAY_NAMES = ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"]


@bp.route("/week", methods=["GET", "POST"])
def week():
    """One project, several days of one week at once (one entry per filled-in day)."""
    form = WeekEntryForm()
    projects, customers = _choices(form)
    if not form.is_submitted():
        form.week_start.data = svc.monday(_parse_date(request.args.get("start")) or date.today())
        form.project_id.data = int_arg("project") or 0
    start = svc.monday(form.week_start.data or date.today())
    days = [start + timedelta(days=i) for i in range(7)]
    already = svc.hours_per_day(current_user.id, days[0], days[-1])
    hours_error = None

    if form.validate_on_submit():
        project = db.session.get(Project, form.project_id.data)
        filled = [(day, f.data) for day, f in zip(days, form.hours) if f.data]
        if project is None or project not in projects:
            form.project_id.errors.append("Kies een project.")
        elif not filled:
            hours_error = "Vul voor minstens één dag uren in."
        else:
            too_much = [d for d, h in filled if already.get(d, 0) + h > 24]
            if too_much:
                hours_error = "Meer dan 24 uur op " + ", ".join(f"{d:%d/%m}" for d in too_much) + "."
            else:
                for day, hours in filled:
                    entry = TimeEntry(user_id=current_user.id, project_id=project.id, work_date=day, hours=hours,
                                      description=form.description.data or None,
                                      field_service=form.field_service.data, km=form.km.data)
                    _apply_field_service(entry, project, form.customer_id.data)
                    db.session.add(entry)
                db.session.commit()
                total = sum((h for _, h in filled), Decimal("0"))
                flash(f"{total.normalize():f}".replace(".", ",") + f" uur op {project.code} geregistreerd "
                      f"({len(filled)} {'dag' if len(filled) == 1 else 'dagen'}).", "success")
                if is_modal_request():
                    return jsonify(ok=True)
                return redirect(next_url(url_for("timesheet.index", month=start.strftime("%Y-%m"))))

    context = dict(form=form, projects=projects, customers=customers, days=list(zip(days, DAY_NAMES, form.hours)),
                   already=already, start=start, hours_error=hours_error, workday_hours=current_app.config["WORKDAY_HOURS"],
                   prev_week=(start - timedelta(days=7)).isoformat(), next_week=(start + timedelta(days=7)).isoformat(),
                   action=url_for("timesheet.week"))
    if is_modal_request():
        return render_template("timesheet/_week_modal.html", **context), 422 if form.errors or hours_error else 200
    return render_template("timesheet/week.html", cancel_url=next_url(url_for("timesheet.index")), **context)


@bp.route("/<int:entry_id>/delete", methods=["POST"])
def delete(entry_id: int):
    entry = db.get_or_404(TimeEntry, entry_id)
    if not _may_edit(entry):
        abort(403)
    month = entry.work_date.strftime("%Y-%m")
    db.session.delete(entry)
    db.session.commit()
    flash("Registratie verwijderd.", "success")
    return redirect(next_url(url_for("timesheet.index", month=month)))


# ------------------------------------------------------------------ timesheet
def _report_args():
    default_start, default_end = svc.previous_month()
    start = _parse_date(request.args.get("start")) or default_start
    end = _parse_date(request.args.get("end")) or default_end
    if end < start:
        start, end = end, start
    project = db.session.get(Project, int_arg("project") or 0)
    if project is not None:
        require_project(project)
    return project, start, end


@bp.route("/report")
def report():
    """Timesheet of a project for a period (default the previous month);
    without a project: all projects with hours in the period, per project."""
    project, start, end = _report_args()
    sheet = svc.build(project.id, start, end) if project else None
    sheets = None if project else svc.build_all(start, end, visible_projects_condition())
    return render_template("timesheet/report.html", project=project, start=start, end=end, sheet=sheet,
                           sheets=sheets, projects=[(p.id, p.display_name) for p in _visible_projects(False)])


def _pdf_name(project: Project, start: date, end: date) -> str:
    return f"Urenstaat {project.code.replace('/', '-')} {start:%Y-%m-%d} tot {end:%Y-%m-%d}.pdf"


def _pdf(project: Project, start: date, end: date, sheet) -> bytes:
    logo = os.path.join(current_app.static_folder, "img", "vopa-logo-print.png")
    return build_pdf(project, start, end, sheet, distance.company_name(), distance.office_display_text(), logo=logo,
                     company_lines=distance.company_lines())


@bp.route("/report.pdf")
def report_pdf():
    project, start, end = _report_args()
    if project is None:
        abort(404)
    pdf = _pdf(project, start, end, svc.build(project.id, start, end))
    current_app.logger.info("Urenstaat %s %s-%s gedownload door %s", project.code, start, end, current_user.username)
    return send_file(BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
                     download_name=_pdf_name(project, start, end))


@bp.route("/report.zip")
def report_zip():
    """Every project's timesheet of the period as a separate PDF, together in one ZIP."""
    _, start, end = _report_args()
    sheets = svc.build_all(start, end, visible_projects_condition())
    if not sheets:
        flash("Geen uren geregistreerd in deze periode: er is niets te exporteren.", "warning")
        return redirect(url_for("timesheet.report", start=start.isoformat(), end=end.isoformat()))
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for project, sheet in sheets:
            archive.writestr(_pdf_name(project, start, end), _pdf(project, start, end, sheet))
    buffer.seek(0)
    current_app.logger.info("%d urenstaten %s-%s gedownload door %s", len(sheets), start, end, current_user.username)
    return send_file(buffer, mimetype="application/zip", as_attachment=True,
                     download_name=f"Urenstaten {start:%Y-%m-%d} tot {end:%Y-%m-%d}.zip")
