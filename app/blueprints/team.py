"""Team overview: workload and availability of every team member."""

from datetime import date, timedelta

import sqlalchemy as sa
from flask import current_app, flash, jsonify, redirect, render_template, request, url_for

from ..access import link_user, require_project
from ..extensions import db
from ..forms import AssignmentForm, none_if_zero
from ..models import Assignment, AssignmentKind, Project, User
from ..services.workload import build_overview, planning_period
from ..utils import int_arg, is_modal_request, next_url
from . import protected_blueprint

bp = protected_blueprint("team", __name__)


@bp.route("/")
def overview():
    """Planning board: team members x days, as a week view or a month view.

    Query string: ``view`` (weeks|month), ``start`` (any date in the period),
    ``weeks`` (number of weeks in the week view).
    """
    period = planning_period(request.args.get("view"), request.args.get("start"),
                             request.args.get("weeks", type=int), default_weeks=2, max_weeks=8)
    days, rows = build_overview(period.start, days=period.days)
    return render_template(
        "team/overview.html",
        period=period,
        days=days,
        rows=rows,
        workday_hours=current_app.config["WORKDAY_HOURS"],
    )


@bp.route("/assignments")
def assignments():
    """List view of upcoming planning entries (useful for editing)."""
    user_id = int_arg("user")
    query = (
        sa.select(Assignment)
        .where(Assignment.end_date >= date.today() - timedelta(days=30))
        .order_by(Assignment.start_date, Assignment.user_id)
    )
    if user_id:
        query = query.where(Assignment.user_id == user_id)
    return render_template(
        "team/assignments.html",
        assignments=db.session.scalars(query).all(),
        users=User.active_choices(),
        user_id=user_id,
    )


def _date_arg(name: str) -> date | None:
    """Read an optional ISO date (``2026-11-16``) from the query string."""
    try:
        return date.fromisoformat(request.args[name])
    except (KeyError, ValueError):
        return None


@bp.route("/assignments/new", methods=["GET", "POST"])
@bp.route("/assignments/<int:assignment_id>/edit", methods=["GET", "POST"])
def edit_assignment(assignment_id: int | None = None):
    """Create or edit a planning entry, as a popup or as a full page.

    Pre-filling a new entry (used by the planning grid):

    * ``?user=3``                              - the team member
    * ``?start=2026-11-16&end=2026-11-18``     - a range selected by dragging
    * ``?day=2026-11-16``                      - a single clicked day
    * ``?project=5``                           - the project (project page)
    """
    assignment = db.get_or_404(Assignment, assignment_id) if assignment_id else None
    require_project(assignment.project if assignment else None)
    form = AssignmentForm(obj=assignment)
    form.user_id.choices = User.active_choices()
    form.project_id.choices = Project.open_choices()

    if not form.is_submitted():
        if assignment:
            form.project_id.data = assignment.project_id or 0
        else:
            form.user_id.data = int_arg("user")
            form.project_id.data = int_arg("project") or 0  # planned from a project page
            start = _date_arg("start") or _date_arg("day")
            end = _date_arg("end") or start
            if start and end and end < start:  # dragged from right to left
                start, end = end, start
            form.start_date.data, form.end_date.data = start, end
            # Dragging onto (or clicking) a Saturday/Sunday means: plan the weekend.
            form.include_weekends.data = any(d is not None and d.weekday() >= 5 for d in (start, end))

    if form.validate_on_submit():
        if assignment is None:
            assignment = Assignment()
            db.session.add(assignment)
        form.populate_obj(assignment)
        assignment.kind = AssignmentKind(form.kind.data)
        # Absences are not linked to a project.
        assignment.project_id = (
            none_if_zero(form.project_id.data) if assignment.kind == AssignmentKind.PROJECT else None
        )
        # Whoever is planned on a project must be able to see that project.
        if assignment.project_id:
            link_user(db.session.get(Project, assignment.project_id), db.session.get(User, assignment.user_id))
        db.session.commit()
        flash("Planning opgeslagen.", "success")
        if is_modal_request():
            # The popup reloads the page, which then shows the flash message.
            return jsonify(ok=True)
        return redirect(next_url(url_for("team.overview", start=assignment.start_date.isoformat())))

    context = dict(
        form=form,
        assignment=assignment,
        action_url=url_for("team.edit_assignment", assignment_id=assignment.id) if assignment
        else url_for("team.edit_assignment"),
    )
    if is_modal_request():
        # 422 tells the popup that the form contains validation errors.
        status = 422 if form.errors else 200
        return render_template("team/_assignment_modal.html", **context), status
    return render_template(
        "team/assignment_form.html", cancel_url=next_url(url_for("team.overview")), **context
    )


@bp.route("/assignments/<int:assignment_id>/delete", methods=["POST"])
def delete_assignment(assignment_id: int):
    assignment = db.get_or_404(Assignment, assignment_id)
    require_project(assignment.project)
    start = assignment.start_date
    db.session.delete(assignment)
    db.session.commit()
    flash("Planning verwijderd.", "success")
    if is_modal_request():
        return jsonify(ok=True)
    return redirect(next_url(url_for("team.overview", start=start.isoformat())))


@bp.route("/assignments/<int:assignment_id>/reschedule", methods=["POST"])
def reschedule_assignment(assignment_id: int):
    """Change the period and/or the team member of a planning entry.

    Called by the planning board (static/js/team.js) after a bar was
    resized at one of its edges or dragged to other days / another person.
    JSON body: ``{"start_date": "2026-11-16", "end_date": "2026-11-20", "user_id": 3}``.
    Answers ``{"ok": true}`` or ``{"ok": false, "error": "..."}`` (status 400).
    """
    assignment = db.get_or_404(Assignment, assignment_id)
    require_project(assignment.project)
    data = request.get_json(silent=True) or {}

    def refuse(message: str):
        return jsonify(ok=False, error=message), 400

    try:
        start = date.fromisoformat(data["start_date"])
        end = date.fromisoformat(data["end_date"])
        user_id = int(data.get("user_id") or assignment.user_id)
    except (KeyError, TypeError, ValueError):
        return refuse("Ongeldige datum of medewerker.")

    user = db.session.get(User, user_id)
    if user is None or not user.active:
        return refuse("Onbekende of inactieve medewerker.")
    if end < start:
        return refuse("De einddatum ligt voor de startdatum.")
    period = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    if not assignment.include_weekends and all(day.weekday() >= 5 for day in period):
        return refuse("Deze periode bevat enkel weekenddagen. Open de planning en vink "
                      "'Weekenddagen inplannen' aan om in het weekend te plannen.")

    assignment.start_date, assignment.end_date, assignment.user_id = start, end, user.id
    link_user(assignment.project, user)  # moved to another person: they now see the project
    db.session.commit()
    return jsonify(ok=True)
