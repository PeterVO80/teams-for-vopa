"""Team overview: workload and availability of every team member."""

from datetime import date, timedelta

import sqlalchemy as sa
from flask import current_app, flash, jsonify, redirect, render_template, request, url_for

from ..extensions import db
from ..forms import AssignmentForm, none_if_zero
from ..models import Assignment, AssignmentKind, Project, User
from ..services.workload import build_overview, monday_of
from ..utils import int_arg, is_modal_request, next_url
from . import protected_blueprint

bp = protected_blueprint("team", __name__)


@bp.route("/")
def overview():
    """Planning grid: team members x working days."""
    try:
        start = date.fromisoformat(request.args["start"])
    except (KeyError, ValueError):
        start = date.today()
    start = monday_of(start)
    weeks = min(max(request.args.get("weeks", 2, type=int), 1), 8)

    days, rows = build_overview(start, weeks)
    return render_template(
        "team/overview.html",
        days=days,
        rows=rows,
        start=start,
        weeks=weeks,
        prev_start=start - timedelta(weeks=weeks),
        next_start=start + timedelta(weeks=weeks),
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
    """
    assignment = db.get_or_404(Assignment, assignment_id) if assignment_id else None
    form = AssignmentForm(obj=assignment)
    form.user_id.choices = User.active_choices()
    form.project_id.choices = Project.open_choices()

    if not form.is_submitted():
        if assignment:
            form.project_id.data = assignment.project_id or 0
        else:
            form.user_id.data = int_arg("user")
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
    start = assignment.start_date
    db.session.delete(assignment)
    db.session.commit()
    flash("Planning verwijderd.", "success")
    if is_modal_request():
        return jsonify(ok=True)
    return redirect(next_url(url_for("team.overview", start=start.isoformat())))
