"""Team overview: workload and availability of every team member."""

from datetime import date, timedelta

import sqlalchemy as sa
from flask import current_app, flash, redirect, render_template, request, url_for

from ..extensions import db
from ..forms import AssignmentForm, none_if_zero
from ..models import Assignment, AssignmentKind, Project, User
from ..services.workload import build_overview, monday_of
from ..utils import int_arg, next_url
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


@bp.route("/assignments/new", methods=["GET", "POST"])
@bp.route("/assignments/<int:assignment_id>/edit", methods=["GET", "POST"])
def edit_assignment(assignment_id: int | None = None):
    assignment = db.get_or_404(Assignment, assignment_id) if assignment_id else None
    form = AssignmentForm(obj=assignment)
    form.user_id.choices = User.active_choices()
    form.project_id.choices = Project.open_choices()

    if not form.is_submitted():
        if assignment:
            form.project_id.data = assignment.project_id or 0
        else:  # clicking an empty cell in the grid pre-fills person and day
            form.user_id.data = int_arg("user")
            day = request.args.get("day")
            if day:
                form.start_date.data = form.end_date.data = date.fromisoformat(day)

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
        return redirect(next_url(url_for("team.overview", start=assignment.start_date.isoformat())))

    return render_template(
        "team/assignment_form.html",
        form=form,
        assignment=assignment,
        cancel_url=next_url(url_for("team.overview")),
    )


@bp.route("/assignments/<int:assignment_id>/delete", methods=["POST"])
def delete_assignment(assignment_id: int):
    assignment = db.get_or_404(Assignment, assignment_id)
    start = assignment.start_date
    db.session.delete(assignment)
    db.session.commit()
    flash("Planning verwijderd.", "success")
    return redirect(next_url(url_for("team.overview", start=start.isoformat())))
